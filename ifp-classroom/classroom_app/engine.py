"""Classroom attendance from IFP (interactive flat panel) cameras.

The kiosk page running on each classroom panel recognises faces in the browser
and reports *sightings* (person X seen in classroom Y at time T). Everything
else is derived from those raw sightings, so changing a classroom's settings,
a holiday or a person's details corrects every report straight away:

* A **presence session** is a run of sightings of one person in one classroom
  where no gap is longer than the classroom's ``absence_minutes``. The first
  sighting is the **entry**, the last one the **exit**. A session on today that
  was seen within the absence window is still open ("in class now").
* A person is **present** in a classroom on a day when they have at least one
  session there, and present in a timetable **period** when they were seen
  during it.
* The monthly register marks a student present on a day when they were seen in
  any classroom that day.
"""
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import db as dbmod

FMT = "%Y-%m-%d %H:%M:%S"
DESCRIPTOR_LEN = 128
MIN_SIGHTING_GAP_S = 15       # repeated sightings closer than this are dropped
MAX_SIGHTING_AGE_S = 24 * 3600


@dataclass
class Session:
    entry: datetime
    last_seen: datetime
    open: bool = False

    @property
    def exit(self):
        return None if self.open else self.last_seen

    @property
    def minutes(self):
        return int((self.last_seen - self.entry).total_seconds() // 60)


@dataclass
class RegisterRow:
    person: object
    sessions: list = field(default_factory=list)
    periods: list = field(default_factory=list)   # "P" / "A" / "" per period
    status: str = "A"                              # P | A | H | WO | -

    @property
    def first_in(self):
        return self.sessions[0].entry if self.sessions else None

    @property
    def last_out(self):
        return self.sessions[-1].exit if self.sessions else None

    @property
    def in_class(self):
        return bool(self.sessions) and self.sessions[-1].open

    @property
    def minutes(self):
        return sum(s.minutes for s in self.sessions)


# ------------------------------------------------------------- helpers ----
def parse_descriptor(value):
    """Validate a face descriptor (JSON text or list) and return it as a list of floats."""
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or len(value) != DESCRIPTOR_LEN:
        raise ValueError("a face descriptor must be a list of 128 numbers")
    out = [float(v) for v in value]
    if not all(math.isfinite(v) for v in out):
        raise ValueError("a face descriptor must contain only finite numbers")
    return out


def weekly_offs(db):
    out = set()
    for part in dbmod.get_setting(db, "class_weekly_off").split(","):
        part = part.strip()
        if part.isdigit() and int(part) < 7:
            out.add(int(part))
    return out


def day_kind(db, day, offs=None, holidays=None):
    """'H' for a holiday, 'WO' for a weekly off, '' for a school day."""
    if holidays is None:
        holidays = {r["date"] for r in db.execute("SELECT date FROM holidays WHERE date = ?", (day.isoformat(),))}
    if day.isoformat() in holidays:
        return "H"
    if day.weekday() in (weekly_offs(db) if offs is None else offs):
        return "WO"
    return ""


def classroom_status(room, now=None):
    """'online' when the kiosk checked in during the last 10 minutes."""
    if not room["last_seen_at"]:
        return "never"
    now = now or datetime.now()
    seen = datetime.strptime(room["last_seen_at"], FMT)
    return "online" if now - seen <= timedelta(minutes=10) else "offline"


# ----------------------------------------------------------- sightings ----
def record_sightings(db, room, items, now=None):
    """Store sightings sent by a kiosk. Returns how many were stored.

    Each item is ``{"person_id": int, "age_seconds": float, "distance": float}``.
    ``age_seconds`` is how long ago the kiosk saw the face, so the time stored
    is the server's clock minus that age: a panel with a wrong clock, or one
    that was offline and is sending queued sightings, still records correct
    times.
    """
    now = now or datetime.now()
    valid = {r["id"] for r in db.execute("SELECT id FROM class_persons WHERE active = 1")}
    stored = 0
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("person_id"))
            age = float(item.get("age_seconds") or 0)
            distance = item.get("distance")
            distance = None if distance is None else round(float(distance), 4)
        except (TypeError, ValueError):
            continue
        if pid not in valid or not math.isfinite(age) or not 0 <= age <= MAX_SIGHTING_AGE_S:
            continue
        seen = (now - timedelta(seconds=age)).replace(microsecond=0)
        near = db.execute("SELECT 1 FROM class_sightings WHERE classroom_id = ? AND person_id = ? "
                          "AND seen_at BETWEEN ? AND ? LIMIT 1",
                          (room["id"], pid, (seen - timedelta(seconds=MIN_SIGHTING_GAP_S)).strftime(FMT),
                           (seen + timedelta(seconds=MIN_SIGHTING_GAP_S)).strftime(FMT))).fetchone()
        if near:
            continue
        db.execute("INSERT INTO class_sightings (classroom_id, person_id, seen_at, distance) VALUES (?, ?, ?, ?)",
                   (room["id"], pid, seen.strftime(FMT), distance))
        stored += 1
    return stored


def build_sessions(times, absence_minutes, now=None):
    """Group sorted sighting times of one person into presence sessions."""
    gap = timedelta(minutes=max(1, absence_minutes))
    now = now or datetime.now()
    sessions = []
    for t in times:
        if sessions and t - sessions[-1].last_seen <= gap:
            sessions[-1].last_seen = t
        else:
            sessions.append(Session(entry=t, last_seen=t))
    if sessions and now - sessions[-1].last_seen <= gap and sessions[-1].last_seen <= now:
        sessions[-1].open = True
    return sessions


def _sightings(db, classroom_id, day):
    rows = db.execute("SELECT person_id, seen_at FROM class_sightings WHERE classroom_id = ? "
                      "AND seen_at BETWEEN ? AND ? ORDER BY person_id, seen_at",
                      (classroom_id, f"{day} 00:00:00", f"{day} 23:59:59")).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["person_id"], []).append(datetime.strptime(r["seen_at"], FMT))
    return out


def periods(db):
    return db.execute("SELECT * FROM class_periods ORDER BY start_time, name").fetchall()


def register(db, room, day, role="", now=None):
    """Daily register of one classroom: one RegisterRow per person.

    Lists everyone whose home classroom this is, plus anyone else (visiting
    teachers, students from another section) who was seen in it that day.
    """
    now = now or datetime.now()
    seen = _sightings(db, room["id"], day)
    ids = sorted(seen)
    marks = ",".join("?" * len(ids)) or "NULL"
    sql = (f"SELECT * FROM class_persons WHERE (classroom_id = ? AND active = 1) OR id IN ({marks})")
    args = [room["id"], *ids]
    if role:
        sql = f"SELECT * FROM ({sql}) WHERE role = ?"
        args.append(role)
    persons = db.execute(sql + " ORDER BY role DESC, class_name, name", args).fetchall()
    kind = day_kind(db, day)
    plist = periods(db)
    out = []
    for p in persons:
        times = seen.get(p["id"], [])
        row = RegisterRow(person=p, sessions=build_sessions(times, room["absence_minutes"], now))
        row.status = "P" if row.sessions else (kind or ("-" if day > now.date() else "A"))
        for per in plist:
            start = datetime.combine(day, datetime.strptime(per["start_time"], "%H:%M").time())
            end = datetime.combine(day, datetime.strptime(per["end_time"], "%H:%M").time())
            if any(start <= t <= end for t in times):
                row.periods.append("P")
            elif row.status in ("H", "WO") or start > now:
                row.periods.append("")
            else:
                row.periods.append("A")
        out.append(row)
    return out, plist, kind


def movements(db, day, classroom_id=None, person_id=None, now=None):
    """Entry and exit events (derived from sessions), newest first."""
    now = now or datetime.now()
    sql = ("SELECT s.classroom_id, s.person_id, s.seen_at FROM class_sightings s "
           "WHERE s.seen_at BETWEEN ? AND ?")
    args = [f"{day} 00:00:00", f"{day} 23:59:59"]
    if classroom_id:
        sql += " AND s.classroom_id = ?"
        args.append(classroom_id)
    if person_id:
        sql += " AND s.person_id = ?"
        args.append(person_id)
    groups = {}
    for r in db.execute(sql + " ORDER BY s.seen_at", args):
        groups.setdefault((r["classroom_id"], r["person_id"]), []).append(datetime.strptime(r["seen_at"], FMT))
    rooms = {r["id"]: r for r in db.execute("SELECT * FROM classrooms")}
    persons = {r["id"]: r for r in db.execute("SELECT * FROM class_persons")}
    events = []
    for (rid, pid), times in groups.items():
        room = rooms.get(rid)
        person = persons.get(pid)
        if room is None or person is None:
            continue
        for s in build_sessions(times, room["absence_minutes"], now):
            events.append({"time": s.entry, "type": "Entry", "room": room, "person": person, "session": s})
            if not s.open:
                events.append({"time": s.last_seen, "type": "Exit", "room": room, "person": person, "session": s})
    events.sort(key=lambda e: (e["time"], e["type"] == "Entry"), reverse=True)
    return events


def present_now(db, room, now=None):
    """People whose latest session in this classroom is still open."""
    now = now or datetime.now()
    rows, _, _ = register(db, room, now.date(), now=now)
    return [r for r in rows if r.in_class]


def monthly(db, start, end, classroom_id=None, class_name="", role="student", now=None):
    """Monthly register: present on a day = seen in any classroom that day."""
    now = now or datetime.now()
    sql = "SELECT * FROM class_persons WHERE active = 1"
    args = []
    if classroom_id:
        sql += " AND classroom_id = ?"
        args.append(classroom_id)
    if class_name:
        sql += " AND class_name = ?"
        args.append(class_name)
    if role:
        sql += " AND role = ?"
        args.append(role)
    persons = db.execute(sql + " ORDER BY class_name, name", args).fetchall()
    seen = {}
    for r in db.execute("SELECT DISTINCT person_id, substr(seen_at, 1, 10) AS d FROM class_sightings "
                        "WHERE seen_at BETWEEN ? AND ?", (f"{start} 00:00:00", f"{end} 23:59:59")):
        seen.setdefault(r["person_id"], set()).add(r["d"])
    holidays = {r["date"] for r in db.execute("SELECT date FROM holidays WHERE date BETWEEN ? AND ?",
                                              (start.isoformat(), end.isoformat()))}
    offs = weekly_offs(db)
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    kinds = {d: day_kind(db, d, offs, holidays) for d in days}
    out = []
    for p in persons:
        marks = []
        for d in days:
            if d.isoformat() in seen.get(p["id"], ()):
                marks.append("P")
            elif kinds[d]:
                marks.append(kinds[d])
            elif d > now.date():
                marks.append("-")
            else:
                marks.append("A")
        present = marks.count("P")
        absent = marks.count("A")
        working = present + absent
        pct = round(100 * present / working, 1) if working else None
        out.append({"person": p, "marks": marks, "present": present, "absent": absent,
                    "working": working, "percent": pct})
    return out, days


def school_days(db, start, end, today_=None):
    """Working days (not holiday / weekly off) from start to end, never past today."""
    today_ = today_ or date.today()
    holidays = {r["date"] for r in db.execute("SELECT date FROM holidays WHERE date BETWEEN ? AND ?",
                                              (start.isoformat(), end.isoformat()))}
    offs = weekly_offs(db)
    out = []
    d = start
    while d <= min(end, today_):
        if not day_kind(db, d, offs, holidays):
            out.append(d)
        d += timedelta(days=1)
    return out


def analytics(db, start, end, class_name="", classroom_id=None, now=None):
    """Numbers for the Analytics page, for school days between start and end.

    With ``classroom_id`` (a class login) everything is limited to that room:
    its home students, the teachers seen in it, and the room itself.
    """
    now = now or datetime.now()
    days = school_days(db, start, end, now.date())
    day_set = {d.isoformat() for d in days}
    grace = int(dbmod.get_setting(db, "class_late_grace") or 10)
    low = float(dbmod.get_setting(db, "class_low_attendance") or 75)
    plist = periods(db)
    late_after = None
    if plist:
        first = datetime.strptime(plist[0]["start_time"], "%H:%M") + timedelta(minutes=grace)
        late_after = first.strftime("%H:%M:%S")

    sql = "SELECT * FROM class_persons WHERE active = 1"
    args = []
    if class_name:
        sql += " AND (class_name = ? OR role = 'teacher')"
        args.append(class_name)
    persons = db.execute(sql + " ORDER BY class_name, name", args).fetchall()
    students = [p for p in persons if p["role"] == "student"
                and (not classroom_id or p["classroom_id"] == classroom_id)]
    teachers = [p for p in persons if p["role"] == "teacher"]
    if classroom_id:
        seen_here = {r[0] for r in db.execute(
            "SELECT DISTINCT person_id FROM class_sightings WHERE classroom_id = ? AND seen_at BETWEEN ? AND ?",
            (classroom_id, f"{start} 00:00:00", f"{end} 23:59:59"))}
        teachers = [p for p in teachers if p["id"] in seen_here or p["classroom_id"] == classroom_id]

    # first sighting of each person on each day (any classroom)
    first_seen = {}
    for r in db.execute("SELECT person_id, substr(seen_at, 1, 10) AS d, MIN(substr(seen_at, 12)) AS t "
                        "FROM class_sightings WHERE seen_at BETWEEN ? AND ? GROUP BY person_id, d",
                        (f"{start} 00:00:00", f"{end} 23:59:59")):
        if r["d"] in day_set:
            first_seen.setdefault(r["person_id"], {})[r["d"]] = r["t"]

    student_rows = []
    for p in students:
        seen = first_seen.get(p["id"], {})
        present = len(seen)
        late = sum(1 for t in seen.values() if late_after and t > late_after)
        pct = round(100 * present / len(days), 1) if days else None
        student_rows.append({"person": p, "present": present, "absent": len(days) - present, "late": late,
                             "percent": pct, "low": pct is not None and pct < low})

    daily = []
    for d in days:
        iso = d.isoformat()
        n = sum(1 for p in students if iso in first_seen.get(p["id"], {}))
        daily.append({"date": d, "present": n, "total": len(students),
                      "percent": round(100 * n / len(students), 1) if students else None})

    classes = {}
    for r in student_rows:
        c = classes.setdefault(r["person"]["class_name"] or "(no class)",
                               {"name": r["person"]["class_name"] or "(no class)", "students": 0, "present": 0,
                                "late": 0, "low": 0})
        c["students"] += 1
        c["present"] += r["present"]
        c["late"] += r["late"]
        c["low"] += 1 if r["low"] else 0
    for c in classes.values():
        possible = c["students"] * len(days)
        c["percent"] = round(100 * c["present"] / possible, 1) if possible else None

    # teacher time in classrooms, from presence sessions
    rooms = {r["id"]: r for r in db.execute("SELECT * FROM classrooms")
             if not classroom_id or r["id"] == classroom_id}
    teacher_ids = {p["id"] for p in teachers}
    groups = {}
    for r in db.execute("SELECT classroom_id, person_id, seen_at FROM class_sightings WHERE seen_at BETWEEN ? AND ? "
                        "ORDER BY seen_at", (f"{start} 00:00:00", f"{end} 23:59:59")):
        groups.setdefault((r["classroom_id"], r["person_id"], r["seen_at"][:10]), []).append(
            datetime.strptime(r["seen_at"], FMT))
    teacher_minutes, teacher_days, room_people = {}, {}, {}
    for (rid, pid, d), times in groups.items():
        if d not in day_set or rid not in rooms:
            continue
        room_people.setdefault(rid, {}).setdefault(d, set()).add(pid)
        if pid in teacher_ids and rid in rooms:
            mins = sum(s.minutes for s in build_sessions(times, rooms[rid]["absence_minutes"], now))
            teacher_minutes[pid] = teacher_minutes.get(pid, 0) + mins
            teacher_days.setdefault(pid, set()).add(d)
    teacher_rows = [{"person": p, "minutes": teacher_minutes.get(p["id"], 0),
                     "days": len(teacher_days.get(p["id"], ())),
                     "per_day": teacher_minutes.get(p["id"], 0) // max(1, len(teacher_days.get(p["id"], ())))}
                    for p in teachers]
    room_rows = []
    for rid, room in sorted(rooms.items(), key=lambda kv: kv[1]["name"]):
        per_day = room_people.get(rid, {})
        used = [len(v) for v in per_day.values()]
        room_rows.append({"room": room, "days_used": len(used),
                          "avg_people": round(sum(used) / len(used), 1) if used else 0,
                          "status": classroom_status(room, now)})

    possible = len(students) * len(days)
    total_present = sum(r["present"] for r in student_rows)
    return {
        "days": days, "daily": daily, "students": student_rows, "teachers": teacher_rows, "rooms": room_rows,
        "classes": sorted(classes.values(), key=lambda c: c["name"]), "late_after": late_after, "low": low,
        "overall": round(100 * total_present / possible, 1) if possible else None,
        "low_count": sum(1 for r in student_rows if r["low"]),
        "late_count": sum(r["late"] for r in student_rows),
    }


def fmt_time(value):
    return value.strftime("%H:%M") if value else ""


def roster(db, room, now=None):
    """Everything the kiosk needs: who can be recognised, and who is in class right now."""
    people = {}
    for r in db.execute("SELECT f.person_id, f.descriptor, p.name, p.role, p.class_name, p.person_no "
                        "FROM face_samples f JOIN class_persons p ON p.id = f.person_id "
                        "WHERE p.active = 1 ORDER BY f.person_id, f.id"):
        entry = people.setdefault(r["person_id"], {"id": r["person_id"], "name": r["name"], "role": r["role"],
                                                    "class_name": r["class_name"] or "",
                                                    "person_no": r["person_no"], "descriptors": []})
        entry["descriptors"].append(json.loads(r["descriptor"]))
    present = [{"person_id": row.person["id"], "since": fmt_time(row.sessions[-1].entry),
                "last_seen_age": int(((now or datetime.now()) - row.sessions[-1].last_seen).total_seconds())}
               for row in present_now(db, room, now)]
    return {
        "classroom": {"id": room["id"], "name": room["name"], "absence_minutes": room["absence_minutes"]},
        "threshold": float(dbmod.get_setting(db, "class_match_threshold") or 0.5),
        "detector": dbmod.get_setting(db, "class_detector") or "ssd",
        "persons": list(people.values()),
        "present": present,
        "server_time": (now or datetime.now()).strftime(FMT),
    }


def today():
    return date.today()
