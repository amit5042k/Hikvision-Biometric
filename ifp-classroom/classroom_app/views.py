"""Classroom pages, enrolment and the kiosk API used by the panels."""
import csv
import io
import json
import secrets
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import admin_required, staff_required
from . import engine as cl
from . import db as dbmod
from .db import get_db

bp = Blueprint("classroom", __name__)

ROLES = {"student": "Student", "teacher": "Teacher"}
MAX_THUMBNAIL = 80_000
MAX_FACES_PER_PERSON = 10


def parse_date(value, default=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _cell(value):
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _csv(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows([_cell(v) for v in row] for row in rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def _xlsx(filename, title, header, rows, fills=None):
    from .excel import table_workbook
    data = table_workbook(title, header, rows, dbmod.get_setting(get_db(), "school_name"), fills=fills)
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def _audit(action, details=""):
    dbmod.audit(get_db(), g.user["username"] if g.get("user") else None, action, details)


def _rooms():
    return get_db().execute("SELECT * FROM classrooms ORDER BY name").fetchall()


def _class_names():
    return [r[0] for r in get_db().execute("SELECT DISTINCT class_name FROM class_persons "
                                           "WHERE COALESCE(class_name, '') != '' ORDER BY class_name")]


def _room_or_404(room_id):
    room = get_db().execute("SELECT * FROM classrooms WHERE id = ?", (room_id,)).fetchone()
    if room is None:
        abort(404)
    return room


def _valid_time(value):
    try:
        return datetime.strptime(value or "", "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


# ------------------------------------------------------------ overview ----
@bp.route("/classroom")
@staff_required
def overview():
    db = get_db()
    now = datetime.now()
    rooms = []
    for room in _rooms():
        rows, _, _ = cl.register(db, room, now.date(), now=now)
        home = [r for r in rows if r.person["classroom_id"] == room["id"]]
        rooms.append({"room": room, "status": cl.classroom_status(room, now),
                      "in_class": sum(1 for r in rows if r.in_class),
                      "present": sum(1 for r in home if r.status == "P"), "expected": len(home),
                      "kiosk_url": url_for("classroom.kiosk", token=room["kiosk_token"], _external=True)})
    counts = db.execute("SELECT role, COUNT(*) AS n, SUM(EXISTS(SELECT 1 FROM face_samples f "
                        "WHERE f.person_id = p.id)) AS enrolled FROM class_persons p WHERE active = 1 "
                        "GROUP BY role").fetchall()
    return render_template("classroom/overview.html", rooms=rooms, periods=cl.periods(db),
                           counts={r["role"]: r for r in counts}, settings=dbmod.get_settings(db))


@bp.route("/classroom/rooms", methods=["POST"])
@admin_required
def room_save():
    db = get_db()
    room_id = _int(request.form.get("id"))
    name = request.form.get("name", "").strip()
    absence = min(max(_int(request.form.get("absence_minutes"), 10), 1), 240)
    if not name:
        flash("Enter a classroom name.", "error")
        return redirect(url_for("classroom.overview"))
    try:
        if room_id:
            db.execute("UPDATE classrooms SET name = ?, absence_minutes = ?, enabled = ? WHERE id = ?",
                       (name, absence, 1 if request.form.get("enabled", "1") == "1" else 0, room_id))
            _audit("classroom.edit", name)
        else:
            db.execute("INSERT INTO classrooms (name, kiosk_token, absence_minutes) VALUES (?, ?, ?)",
                       (name, secrets.token_urlsafe(18), absence))
            _audit("classroom.add", name)
        db.commit()
        flash(f"Classroom {name} saved.", "success")
    except sqlite3.IntegrityError:
        flash("A classroom with that name already exists.", "error")
    return redirect(url_for("classroom.overview"))


@bp.route("/classroom/rooms/<int:room_id>/token", methods=["POST"])
@admin_required
def room_token(room_id):
    room = _room_or_404(room_id)
    get_db().execute("UPDATE classrooms SET kiosk_token = ? WHERE id = ?", (secrets.token_urlsafe(18), room_id))
    _audit("classroom.new_link", room["name"])
    get_db().commit()
    flash(f"New kiosk link created for {room['name']}. Update the link on that panel; the old one stops working.",
          "warning")
    return redirect(url_for("classroom.overview"))


@bp.route("/classroom/rooms/<int:room_id>/delete", methods=["POST"])
@admin_required
def room_delete(room_id):
    room = _room_or_404(room_id)
    get_db().execute("DELETE FROM classrooms WHERE id = ?", (room_id,))
    _audit("classroom.delete", room["name"])
    get_db().commit()
    flash(f"Classroom {room['name']} and its records deleted.", "success")
    return redirect(url_for("classroom.overview"))


@bp.route("/classroom/periods", methods=["POST"])
@admin_required
def period_add():
    name = request.form.get("name", "").strip()
    start, end = _valid_time(request.form.get("start_time")), _valid_time(request.form.get("end_time"))
    if not name or not start or not end or end <= start:
        flash("Enter a period name and a start time before the end time.", "error")
    else:
        get_db().execute("INSERT INTO class_periods (name, start_time, end_time) VALUES (?, ?, ?)",
                         (name, start, end))
        _audit("period.add", f"{name} {start}-{end}")
        get_db().commit()
        flash("Period added.", "success")
    return redirect(url_for("classroom.overview"))


@bp.route("/classroom/periods/<int:period_id>/delete", methods=["POST"])
@admin_required
def period_delete(period_id):
    get_db().execute("DELETE FROM class_periods WHERE id = ?", (period_id,))
    _audit("period.delete", str(period_id))
    get_db().commit()
    return redirect(url_for("classroom.overview"))


@bp.route("/classroom/settings", methods=["POST"])
@admin_required
def settings_save():
    db = get_db()
    try:
        threshold = float(request.form.get("class_match_threshold", "0.5"))
    except ValueError:
        threshold = 0.5
    threshold = min(max(threshold, 0.3), 0.7)
    detector = request.form.get("class_detector") if request.form.get("class_detector") in ("ssd", "tiny") else "ssd"
    offs = ",".join(str(d) for d in range(7) if request.form.get(f"off_{d}"))
    grace = min(max(_int(request.form.get("class_late_grace"), 10), 0), 120)
    low = min(max(_int(request.form.get("class_low_attendance"), 75), 1), 100)
    dbmod.set_setting(db, "class_late_grace", str(grace))
    dbmod.set_setting(db, "class_low_attendance", str(low))
    dbmod.set_setting(db, "class_match_threshold", f"{threshold:.2f}")
    dbmod.set_setting(db, "class_detector", detector)
    dbmod.set_setting(db, "class_weekly_off", offs)
    _audit("classroom.settings", f"threshold={threshold:.2f} detector={detector} weekly_off={offs}")
    db.commit()
    flash("Recognition settings saved. Kiosks pick them up within 5 minutes.", "success")
    return redirect(url_for("classroom.overview"))


# -------------------------------------------------------------- people ----
@bp.route("/classroom/people")
@staff_required
def people():
    db = get_db()
    role = request.args.get("role", "")
    room_id = _int(request.args.get("classroom"))
    class_name = request.args.get("class_name", "")
    q = request.args.get("q", "").strip()
    sql = ("SELECT p.*, c.name AS room, (SELECT COUNT(*) FROM face_samples f WHERE f.person_id = p.id) AS faces, "
           "(SELECT MAX(seen_at) FROM class_sightings s WHERE s.person_id = p.id) AS last_seen "
           "FROM class_persons p LEFT JOIN classrooms c ON c.id = p.classroom_id WHERE 1 = 1")
    args = []
    if role in ROLES:
        sql += " AND p.role = ?"
        args.append(role)
    if room_id:
        sql += " AND p.classroom_id = ?"
        args.append(room_id)
    if class_name:
        sql += " AND p.class_name = ?"
        args.append(class_name)
    if q:
        sql += " AND (p.name LIKE ? OR p.person_no = ?)"
        args += [f"%{q}%", q]
    rows = db.execute(sql + " ORDER BY p.role DESC, p.class_name, p.name", args).fetchall()
    return render_template("classroom/people.html", rows=rows, role=role, room_id=room_id, class_name=class_name,
                           q=q, rooms=_rooms(), class_names=_class_names(), roles=ROLES)


@bp.route("/classroom/people/new", methods=["GET", "POST"])
@bp.route("/classroom/people/<int:person_id>/edit", methods=["GET", "POST"])
@admin_required
def person_form(person_id=None):
    db = get_db()
    person = db.execute("SELECT * FROM class_persons WHERE id = ?", (person_id,)).fetchone() if person_id else None
    if person_id and person is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        values = (f.get("person_no", "").strip(), f.get("name", "").strip(),
                  f.get("role") if f.get("role") in ROLES else "student", f.get("class_name", "").strip(),
                  _int(f.get("classroom_id")), 1 if f.get("active") else 0)
        if not values[0] or not values[1]:
            flash("Number and name are required.", "error")
            return render_template("classroom/person_form.html", person=f, rooms=_rooms(), roles=ROLES)
        try:
            if person:
                db.execute("UPDATE class_persons SET person_no = ?, name = ?, role = ?, class_name = ?, "
                           "classroom_id = ?, active = ? WHERE id = ?", values + (person_id,))
                _audit("person.edit", f"#{values[0]} {values[1]}")
            else:
                cur = db.execute("INSERT INTO class_persons (person_no, name, role, class_name, classroom_id, active) "
                                 "VALUES (?, ?, ?, ?, ?, ?)", values)
                person_id = cur.lastrowid
                _audit("person.add", f"#{values[0]} {values[1]}")
            db.commit()
        except sqlite3.IntegrityError:
            flash("That number is already used by someone else.", "error")
            return render_template("classroom/person_form.html", person=f, rooms=_rooms(), roles=ROLES)
        flash("Saved. Now add face photos so the classroom camera can recognise them.", "success")
        return redirect(url_for("classroom.person_detail", person_id=person_id))
    return render_template("classroom/person_form.html", person=person or {"active": 1, "role": "student"},
                           rooms=_rooms(), roles=ROLES)


@bp.route("/classroom/people/<int:person_id>")
@staff_required
def person_detail(person_id):
    db = get_db()
    person = db.execute("SELECT p.*, c.name AS room FROM class_persons p LEFT JOIN classrooms c "
                        "ON c.id = p.classroom_id WHERE p.id = ?", (person_id,)).fetchone()
    if person is None:
        abort(404)
    faces = db.execute("SELECT id, thumbnail, created_at FROM face_samples WHERE person_id = ? ORDER BY id",
                       (person_id,)).fetchall()
    day = parse_date(request.args.get("date"), date.today())
    events = cl.movements(db, day, person_id=person_id)
    return render_template("classroom/person_detail.html", person=person, faces=faces, events=events, day=day,
                           max_faces=MAX_FACES_PER_PERSON, roles=ROLES)


def _add_face(db, person, descriptor, thumbnail):
    """Validate and store one face sample. Returns an error message or None."""
    try:
        vec = cl.parse_descriptor(descriptor)
    except (ValueError, TypeError):
        return "The face data was not valid. Try another photo."
    thumbnail = thumbnail or None
    if thumbnail and (not thumbnail.startswith("data:image/jpeg;base64,") or len(thumbnail) > MAX_THUMBNAIL):
        thumbnail = None
    count = db.execute("SELECT COUNT(*) FROM face_samples WHERE person_id = ?", (person["id"],)).fetchone()[0]
    if count >= MAX_FACES_PER_PERSON:
        return f"{person['name']} already has {MAX_FACES_PER_PERSON} face photos. Delete one first."
    db.execute("INSERT INTO face_samples (person_id, descriptor, thumbnail) VALUES (?, ?, ?)",
               (person["id"], json.dumps([round(v, 6) for v in vec]), thumbnail))
    return None


@bp.route("/classroom/people/<int:person_id>/faces", methods=["POST"])
@admin_required
def face_add(person_id):
    db = get_db()
    person = db.execute("SELECT * FROM class_persons WHERE id = ?", (person_id,)).fetchone()
    if person is None:
        return jsonify({"ok": False, "error": "Person not found."}), 404
    error = _add_face(db, person, request.form.get("descriptor", ""), request.form.get("thumbnail", ""))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    _audit("face.add", f"#{person['person_no']} {person['name']}")
    db.commit()
    return jsonify({"ok": True})


@bp.route("/classroom/enroll/by-number", methods=["POST"])
@admin_required
def face_add_by_number():
    """Bulk enrolment: the browser sends one face per photo, named after the person's number."""
    db = get_db()
    number = request.form.get("person_no", "").strip()
    person = db.execute("SELECT * FROM class_persons WHERE person_no = ?", (number,)).fetchone()
    if person is None:
        return jsonify({"ok": False, "error": f"No student or teacher with number {number}."}), 404
    error = _add_face(db, person, request.form.get("descriptor", ""), request.form.get("thumbnail", ""))
    if error:
        return jsonify({"ok": False, "error": error}), 400
    _audit("face.add", f"#{person['person_no']} {person['name']} (bulk)")
    db.commit()
    return jsonify({"ok": True, "name": person["name"]})


@bp.route("/classroom/faces/<int:face_id>/delete", methods=["POST"])
@admin_required
def face_delete(face_id):
    db = get_db()
    row = db.execute("SELECT f.person_id, p.name FROM face_samples f JOIN class_persons p ON p.id = f.person_id "
                     "WHERE f.id = ?", (face_id,)).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM face_samples WHERE id = ?", (face_id,))
    _audit("face.delete", row["name"])
    db.commit()
    flash("Face photo removed.", "success")
    return redirect(url_for("classroom.person_detail", person_id=row["person_id"]))


@bp.route("/classroom/people/<int:person_id>/delete", methods=["POST"])
@admin_required
def person_delete(person_id):
    db = get_db()
    row = db.execute("SELECT * FROM class_persons WHERE id = ?", (person_id,)).fetchone()
    if row:
        db.execute("DELETE FROM class_persons WHERE id = ?", (person_id,))
        _audit("person.delete", f"#{row['person_no']} {row['name']}")
        db.commit()
        flash(f"{row['name']} deleted, with their face photos and records.", "success")
    return redirect(url_for("classroom.people"))


@bp.route("/classroom/enroll")
@admin_required
def enroll():
    return render_template("classroom/enroll.html")


CSV_HEADER = ["Number", "Name", "Role", "Class", "Classroom", "Active"]


@bp.route("/classroom/people/import", methods=["POST"])
@admin_required
def people_import():
    db = get_db()
    upload = request.files.get("file")
    if not upload:
        flash("Choose a CSV file.", "error")
        return redirect(url_for("classroom.people"))
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("The file must be a UTF-8 CSV (in Excel: Save As → CSV UTF-8).", "error")
        return redirect(url_for("classroom.people"))
    rooms = {r["name"].lower(): r["id"] for r in _rooms()}
    added = updated = 0
    errors = []
    for i, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        number, name = row.get("number", ""), row.get("name", "")
        if not number or not name:
            errors.append(f"line {i}: number and name are required")
            continue
        role = row.get("role", "student").lower()
        role = role if role in ROLES else "student"
        room_name = row.get("classroom", "")
        room_id = rooms.get(room_name.lower()) if room_name else None
        if room_name and room_id is None:
            errors.append(f"line {i}: unknown classroom '{room_name}' (add it on the Classroom page first)")
        active = 0 if row.get("active", "1").lower() in ("0", "no", "n", "false", "inactive") else 1
        existing = db.execute("SELECT id FROM class_persons WHERE person_no = ?", (number,)).fetchone()
        if existing:
            db.execute("UPDATE class_persons SET name = ?, role = ?, class_name = ?, classroom_id = ?, active = ? "
                       "WHERE id = ?", (name, role, row.get("class", ""), room_id, active, existing["id"]))
            updated += 1
        else:
            db.execute("INSERT INTO class_persons (person_no, name, role, class_name, classroom_id, active) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (number, name, role, row.get("class", ""), room_id, active))
            added += 1
    _audit("person.import", f"{added} added, {updated} updated")
    db.commit()
    flash(f"Import finished: {added} added, {updated} updated.", "success")
    for e in errors[:10]:
        flash(e, "warning")
    return redirect(url_for("classroom.people"))


@bp.route("/classroom/people/export.csv")
@staff_required
def people_export():
    rows = get_db().execute("SELECT p.*, c.name AS room FROM class_persons p LEFT JOIN classrooms c "
                            "ON c.id = p.classroom_id ORDER BY p.role DESC, p.class_name, p.name").fetchall()
    return _csv("students-teachers.csv", CSV_HEADER,
                [[r["person_no"], r["name"], r["role"], r["class_name"] or "", r["room"] or "", r["active"]]
                 for r in rows])


# ------------------------------------------------------------- reports ----
@bp.route("/classroom/register")
@staff_required
def register():
    db = get_db()
    rooms = _rooms()
    room_id = _int(request.args.get("classroom")) or (rooms[0]["id"] if rooms else None)
    day = parse_date(request.args.get("date"), date.today())
    role = request.args.get("role", "") if request.args.get("role", "") in ROLES else ""
    if not room_id:
        flash("Add a classroom first.", "warning")
        return redirect(url_for("classroom.overview"))
    room = _room_or_404(room_id)
    rows, plist, kind = cl.register(db, room, day, role=role)
    export = request.args.get("export")
    if export:
        header = ["Number", "Name", "Role", "Class", "Status", "First entry", "Last exit", "Minutes in class",
                  "Entries"] + [f"{p['name']} ({p['start_time']}-{p['end_time']})" for p in plist]
        data = [[r.person["person_no"], r.person["name"], ROLES.get(r.person["role"], r.person["role"]),
                 r.person["class_name"] or "", r.status, cl.fmt_time(r.first_in),
                 "In class" if r.in_class else cl.fmt_time(r.last_out), r.minutes if r.sessions else "",
                 len(r.sessions) or ""] + r.periods for r in rows]
        name = f"register-{room['name']}-{day}".replace(" ", "_")
        if export == "xlsx":
            return _xlsx(f"{name}.xlsx", f"Class register - {room['name']} - {day:%d %b %Y}", header, data)
        return _csv(f"{name}.csv", header, data)
    return render_template("classroom/register.html", rows=rows, periods=plist, kind=kind, room=room, rooms=rooms,
                           day=day, role=role, roles=ROLES, prev=day - timedelta(days=1),
                           next=day + timedelta(days=1),
                           summary={"P": sum(1 for r in rows if r.status == "P"),
                                    "A": sum(1 for r in rows if r.status == "A"),
                                    "in": sum(1 for r in rows if r.in_class)})


@bp.route("/classroom/movements")
@staff_required
def movements():
    db = get_db()
    day = parse_date(request.args.get("date"), date.today())
    room_id = _int(request.args.get("classroom"))
    events = cl.movements(db, day, classroom_id=room_id)
    q = request.args.get("q", "").strip().lower()
    if q:
        events = [e for e in events if q in e["person"]["name"].lower() or q == e["person"]["person_no"].lower()]
    if request.args.get("export"):
        return _csv(f"entry-exit-{day}.csv", ["Time", "Event", "Classroom", "Number", "Name", "Role", "Class"],
                    [[e["time"].strftime(cl.FMT), e["type"], e["room"]["name"], e["person"]["person_no"],
                      e["person"]["name"], e["person"]["role"], e["person"]["class_name"] or ""]
                     for e in sorted(events, key=lambda e: e["time"])])
    return render_template("classroom/movements.html", events=events, day=day, room_id=room_id, rooms=_rooms(),
                           q=request.args.get("q", ""), prev=day - timedelta(days=1), next=day + timedelta(days=1))


@bp.route("/classroom/monthly")
@staff_required
def monthly():
    db = get_db()
    try:
        y, m = (int(x) for x in request.args.get("month", "").split("-"))
        start = date(y, m, 1)
    except ValueError:
        start = date.today().replace(day=1)
    end = start.replace(day=monthrange(start.year, start.month)[1])
    room_id = _int(request.args.get("classroom"))
    class_name = request.args.get("class_name", "")
    role = request.args.get("role", "student")
    role = role if role in ROLES else ""
    rows, days = cl.monthly(db, start, end, classroom_id=room_id, class_name=class_name, role=role)
    export = request.args.get("export")
    if export:
        header = ["Number", "Name", "Class"] + [d.strftime("%d") for d in days] + \
                 ["Present", "Absent", "Working days", "Attendance %"]
        data = [[r["person"]["person_no"], r["person"]["name"], r["person"]["class_name"] or ""] + r["marks"] +
                [r["present"], r["absent"], r["working"], "" if r["percent"] is None else r["percent"]] for r in rows]
        if export == "xlsx":
            return _xlsx(f"monthly-register-{start:%Y-%m}.xlsx", f"Monthly attendance register - {start:%B %Y}",
                         header, data, fills=(3, 3 + len(days)))
        return _csv(f"monthly-register-{start:%Y-%m}.csv", header, data)
    return render_template("classroom/monthly.html", rows=rows, days=days, month=start.strftime("%Y-%m"),
                           month_label=start.strftime("%B %Y"), room_id=room_id, class_name=class_name, role=role,
                           rooms=_rooms(), class_names=_class_names(), roles=ROLES)


# ----------------------------------------------------------- analytics ----
@bp.route("/classroom/analytics")
@staff_required
def analytics():
    db = get_db()
    today = date.today()
    preset = request.args.get("range", "month")
    start = parse_date(request.args.get("start"))
    end = parse_date(request.args.get("end"))
    if not (start and end and start <= end):
        if preset == "week":
            start, end = today - timedelta(days=today.weekday()), today
        elif preset == "30":
            start, end = today - timedelta(days=29), today
        elif preset == "year":
            # Indian academic year: April to March
            y = today.year if today.month >= 4 else today.year - 1
            start, end = date(y, 4, 1), today
        else:
            preset, start, end = "month", today.replace(day=1), today
    else:
        preset = "custom"
    if (end - start).days > 400:
        start = end - timedelta(days=400)
    class_name = request.args.get("class_name", "")
    data = cl.analytics(db, start, end, class_name=class_name)
    export = request.args.get("export")
    if export == "students":
        rows = sorted(data["students"], key=lambda r: (r["percent"] is None, r["percent"] or 0))
        return _csv(f"student-attendance-{start}-to-{end}.csv",
                    ["Number", "Name", "Class", "Days present", "Days absent", "Late arrivals", "Attendance %"],
                    [[r["person"]["person_no"], r["person"]["name"], r["person"]["class_name"] or "", r["present"],
                      r["absent"], r["late"], "" if r["percent"] is None else r["percent"]] for r in rows])
    if export == "teachers":
        return _csv(f"teacher-time-{start}-to-{end}.csv",
                    ["Number", "Name", "Days seen", "Minutes in class", "Average minutes per day"],
                    [[r["person"]["person_no"], r["person"]["name"], r["days"], r["minutes"], r["per_day"]]
                     for r in data["teachers"]])
    if export == "daily":
        return _csv(f"daily-attendance-{start}-to-{end}.csv", ["Date", "Present", "Students", "Attendance %"],
                    [[d["date"].isoformat(), d["present"], d["total"], "" if d["percent"] is None else d["percent"]]
                     for d in data["daily"]])
    return render_template("classroom/analytics.html", data=data, start=start, end=end, preset=preset,
                           class_name=class_name, class_names=_class_names())


# ------------------------------------------------------- panel app ----
@bp.route("/app")
def panel_app():
    """Download page for the IFP Android app. No login: open it in the panel's browser."""
    import os
    from flask import current_app
    apk = os.path.join(current_app.static_folder, "app", "ifp-classroom.apk")
    size = os.path.getsize(apk) // (1024 * 1024) if os.path.exists(apk) else None
    return render_template("classroom/panel_app.html", size=size)


# --------------------------------------------------------------- kiosk ----
def _kiosk_room(token):
    room = get_db().execute("SELECT * FROM classrooms WHERE kiosk_token = ? AND enabled = 1", (token,)).fetchone()
    if room is None:
        abort(404)
    return room


@bp.after_app_request
def kiosk_cors(response):
    """The IFP Android app runs its page from a secure in-app address and calls
    the kiosk API cross-origin. The API is authorised by the secret in the URL,
    not by cookies, so any origin may call it."""
    if request.path.startswith("/api/class/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "86400"
    return response


@bp.route("/kiosk/<token>")
def kiosk(token):
    room = _kiosk_room(token)
    return render_template("classroom/kiosk.html", room=room, token=token)


@bp.route("/api/class/<token>/roster")
def kiosk_roster(token):
    db = get_db()
    room = _kiosk_room(token)
    db.execute("UPDATE classrooms SET last_seen_at = ? WHERE id = ?", (datetime.now().strftime(cl.FMT), room["id"]))
    db.commit()
    return jsonify(cl.roster(db, room))


@bp.route("/api/class/<token>/sightings", methods=["POST"])
def kiosk_sightings(token):
    db = get_db()
    room = _kiosk_room(token)
    payload = request.get_json(silent=True) or {}
    stored = cl.record_sightings(db, room, (payload.get("sightings") or [])[:2000])
    db.execute("UPDATE classrooms SET last_seen_at = ? WHERE id = ?", (datetime.now().strftime(cl.FMT), room["id"]))
    db.commit()
    return jsonify({"ok": True, "stored": stored})
