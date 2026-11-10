"""Turns raw punches into daily attendance using shifts, holidays and leaves.

Nothing here is stored: attendance is always derived from the punches, so
fixing a punch, shift, leave or holiday immediately corrects every report.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import db as dbmod

FMT = "%Y-%m-%d %H:%M:%S"

STATUS_LABELS = {
    "P": "Present",
    "HD": "Half Day",
    "A": "Absent",
    "MP": "Missing Punch",
    "L": "Leave",
    "H": "Holiday",
    "WO": "Weekly Off",
    "-": "",
}


@dataclass
class Day:
    date: date
    status: str = "-"
    first_in: datetime | None = None
    last_out: datetime | None = None
    punch_count: int = 0
    work_minutes: int = 0
    late_minutes: int = 0
    early_minutes: int = 0
    ot_minutes: int = 0
    leave: float = 0          # 1 = full day of leave, 0.5 = half day
    leave_type: str | None = None
    shift_name: str | None = None   # set when the roster assigns a different shift
    remarks: list = field(default_factory=list)

    leave_paid = True

    @property
    def is_late(self):
        return self.late_minutes > 0

    @property
    def label(self):
        return STATUS_LABELS.get(self.status, self.status)


@dataclass
class Summary:
    present: float = 0
    absent: float = 0
    half_day: int = 0
    missing_punch: int = 0
    leave: float = 0
    unpaid_leave: float = 0
    holiday: int = 0
    weekly_off: int = 0
    late: int = 0
    early: int = 0
    work_minutes: int = 0
    ot_minutes: int = 0

    @property
    def payable(self):
        return self.present + self.leave - self.unpaid_leave + self.holiday + self.weekly_off


def daterange(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def parse_date(value, default=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return default


def parse_hm(value):
    """'09:30' -> (9, 30); None for anything that is not a valid 24-hour time."""
    try:
        h, m = str(value).strip().split(":")[:2]
        h, m = int(h), int(m)
    except (AttributeError, ValueError):
        return None
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


def _hm(value, default="09:00"):
    h, m = parse_hm(value) or parse_hm(default)
    return timedelta(hours=h, minutes=m)


def _number(value, default, cast=float):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _minutes(td):
    return int(td.total_seconds() // 60)


# only approved leave affects attendance; pending requests do not
LEAVE_QUERY = "SELECT * FROM leaves WHERE status = 'approved' AND start_date <= ? AND end_date >= ?"


def load_employees(db, department_id=None, employee_id=None, include_inactive=False, site_id=None):
    sql = ("SELECT e.*, d.name AS department, st.name AS site, s.name AS shift_name, s.start_time, s.end_time, "
           "s.grace_in_min, s.grace_out_min, s.break_minutes, s.half_day_hours, s.full_day_hours, s.weekly_off "
           "FROM employees e LEFT JOIN departments d ON d.id = e.department_id "
           "LEFT JOIN sites st ON st.id = e.site_id "
           "LEFT JOIN shifts s ON s.id = e.shift_id WHERE 1=1")
    args = []
    if not include_inactive:
        sql += " AND e.active = 1"
    if department_id:
        sql += " AND e.department_id = ?"
        args.append(department_id)
    if site_id:
        sql += " AND e.site_id = ?"
        args.append(site_id)
    if employee_id:
        sql += " AND e.id = ?"
        args.append(employee_id)
    sql += " ORDER BY CAST(e.employee_no AS INTEGER), e.employee_no"
    return db.execute(sql, args).fetchall()


def report_employees(db, start, end, department_id=None, site_id=None):
    """Employees to show in a report: everyone active, plus inactive employees
    (leavers) who still have punches or leave in the period, so a leaver's
    last month stays on the muster roll and salary summary."""
    rows = load_employees(db, department_id=department_id, include_inactive=True, site_id=site_id)
    if all(r["active"] for r in rows):
        return rows
    with_data = {r[0] for r in db.execute(
        "SELECT DISTINCT employee_no FROM punches WHERE punch_time >= ? AND punch_time < ?",
        (f"{start} 00:00:00", f"{end + timedelta(days=2)} 00:00:00"))}
    with_leave = {r[0] for r in db.execute(
        "SELECT DISTINCT employee_id FROM leaves WHERE start_date <= ? AND end_date >= ?",
        (end.isoformat(), start.isoformat()))}
    return [r for r in rows if r["active"] or r["employee_no"] in with_data or r["id"] in with_leave]


def compute(db, start, end, employees=None, now=None):
    """Return ``[(employee_row, [Day, ...], Summary), ...]`` for the date range."""
    now = now or datetime.now()
    employees = load_employees(db) if employees is None else employees
    if not employees:
        return []
    settings = dbmod.get_settings(db)
    before = timedelta(hours=_number(settings.get("window_before_hours"), 4.0))
    ot_min = _number(settings.get("ot_min_minutes"), 30, int)

    holidays = {r["date"]: r["name"] for r in db.execute(
        "SELECT date, name FROM holidays WHERE date BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))}

    leaves = {}
    for r in db.execute(LEAVE_QUERY, (end.isoformat(), start.isoformat())):
        leaves.setdefault(r["employee_id"], []).append(r)
    unpaid = {r["name"] for r in db.execute("SELECT name FROM leave_types WHERE paid = 0")}

    lo = datetime.combine(start, datetime.min.time()) - timedelta(days=1)
    hi = datetime.combine(end, datetime.min.time()) + timedelta(days=2)
    punches = {}
    for r in db.execute("SELECT employee_no, punch_time FROM punches WHERE punch_time >= ? AND punch_time < ? "
                        "ORDER BY punch_time", (lo.strftime(FMT), hi.strftime(FMT))):
        try:
            punches.setdefault(r["employee_no"], []).append(datetime.strptime(r["punch_time"], FMT))
        except ValueError:
            continue  # a malformed row must not break every report

    shifts = {r["id"]: r for r in db.execute("SELECT * FROM shifts")}
    roster = {}
    for r in db.execute("SELECT employee_id, date, shift_id FROM roster WHERE date BETWEEN ? AND ?",
                        ((start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat())):
        roster[(r["employee_id"], r["date"])] = r["shift_id"]

    out = []
    for emp in employees:
        times = punches.get(emp["employee_no"], [])
        emp_leaves = leaves.get(emp["id"], [])
        plans = _plans(emp, start, end, shifts, roster, before)
        days = [_compute_day(emp, d, plans[d], times, holidays, emp_leaves, ot_min, now)
                for d in daterange(start, end)]
        for d in days:
            d.leave_paid = d.leave_type not in unpaid
        out.append((emp, days, summarize(days)))
    return out


SHIFT_FIELDS = ("start_time", "end_time", "grace_in_min", "grace_out_min", "break_minutes",
                "half_day_hours", "full_day_hours", "weekly_off")


@dataclass
class Plan:
    """What applies to one employee on one date."""
    shift: dict
    rostered: bool
    rest_day: bool
    shift_start: datetime
    shift_end: datetime
    window_start: datetime
    window_end: datetime


def _plan(emp, d, shifts, roster, before):
    rostered = (emp["id"], d.isoformat()) in roster
    shift_id = roster.get((emp["id"], d.isoformat()))
    if rostered and shift_id is None:
        shift, rest = {k: emp[k] for k in SHIFT_FIELDS}, True       # rostered rest day
    elif rostered and shift_id in shifts:
        shift, rest = {k: shifts[shift_id][k] for k in SHIFT_FIELDS}, False
        shift["shift_name"] = shifts[shift_id]["name"]
    else:
        shift, rest = {k: emp[k] for k in SHIFT_FIELDS}, False      # the employee's default shift
    start_td = _hm(shift["start_time"], "09:00")
    end_td = _hm(shift["end_time"], "18:00")
    if end_td <= start_td:  # overnight shift
        end_td += timedelta(days=1)
    midnight = datetime.combine(d, datetime.min.time())
    shift_start, shift_end = midnight + start_td, midnight + end_td
    window_start = shift_start - before
    return Plan(shift, rostered, rest, shift_start, shift_end, window_start, window_start + timedelta(days=1))


def _plans(emp, start, end, shifts, roster, before):
    """Day plans for [start, end]; neighbouring punch windows never overlap.

    Each day's punches are taken from its window (shift start - N hours, +24h).
    When the shift changes between days (e.g. night shift then a day shift)
    two windows can overlap and one punch would count twice; the boundary is
    then moved to halfway between the earlier shift's end and the next start.
    """
    days = list(daterange(start - timedelta(days=1), end + timedelta(days=1)))
    plans = [_plan(emp, d, shifts, roster, before) for d in days]
    for prev, cur in zip(plans, plans[1:]):
        if cur.window_start < prev.window_end:
            gap_start = min(prev.shift_end, cur.shift_start)
            boundary = gap_start + (cur.shift_start - gap_start) / 2
            prev.window_end = cur.window_start = max(boundary, prev.window_start)
    return dict(zip(days, plans))


def _compute_day(emp, d, plan, times, holidays, emp_leaves, ot_min, now):
    day = Day(d)
    join = parse_date(emp["join_date"])
    if d > now.date() or (join and d < join):
        return day
    sh = plan.shift
    shift_start, shift_end = plan.shift_start, plan.shift_end
    window_end = plan.window_end
    day_punches = times[bisect_left(times, plan.window_start):bisect_left(times, window_end)]
    if plan.rostered and not plan.rest_day:
        day.shift_name = sh.get("shift_name")

    iso = d.isoformat()
    if plan.rostered:
        weekly_off = plan.rest_day  # the roster decides working and rest days
    else:
        weekly_off = str(d.weekday()) in (sh["weekly_off"] or "").split(",")
    holiday = holidays.get(iso)
    leave = next((lv for lv in emp_leaves if lv["start_date"] <= iso <= lv["end_date"]), None)

    if not day_punches:
        # holidays and weekly offs take precedence: leave is never charged on them
        if holiday:
            day.status = "H"
            day.remarks.append(holiday)
        elif weekly_off:
            day.status = "WO"
            if plan.rest_day:
                day.remarks.append("Rest day (roster)")
        elif leave:
            day.status = "L"
            day.leave = 0.5 if leave["half_day"] else 1
            day.leave_type = leave["leave_type"]
            day.remarks.append(f"{leave['leave_type']} leave" + (" (half day)" if leave["half_day"] else ""))
        elif now < shift_end:
            day.status = "-"  # shift not over yet (also across midnight), not absent yet
        else:
            day.status = "A"
        return day

    day.punch_count = len(day_punches)
    day.first_in = day_punches[0]
    if len(day_punches) > 1:
        day.last_out = day_punches[-1]
        span = _minutes(day.last_out - day.first_in)
        brk = int(sh["break_minutes"] or 0)
        day.work_minutes = span - brk if span > brk else span

    late = _minutes(day.first_in - shift_start)
    if late > int(sh["grace_in_min"] or 0):
        day.late_minutes = late
    if day.last_out:
        early = _minutes(shift_end - day.last_out)
        if early > int(sh["grace_out_min"] or 0):
            day.early_minutes = early
        ot = _minutes(day.last_out - shift_end)
        if ot >= ot_min and ot > 0:
            day.ot_minutes = ot

    off_day = holiday or weekly_off
    if off_day:
        day.status = "P"
        day.ot_minutes = day.work_minutes
        day.late_minutes = day.early_minutes = 0
        day.remarks.append(f"Worked on {holiday or 'weekly off'}")
        return day

    if not day.last_out:
        if now < window_end:  # still inside this day's punch window: the OUT punch may yet come
            day.status = "P"
            day.remarks.append("In")
        else:
            day.status = "MP"
            day.remarks.append("Single punch")
        return day

    full = float(sh["full_day_hours"] or 0) * 60
    half = float(sh["half_day_hours"] or 0) * 60
    if day.work_minutes >= full:
        day.status = "P"
    elif day.work_minutes >= half:
        day.status = "HD"
    else:
        day.status = "A"
        day.remarks.append("Short hours")
    if leave:
        day.remarks.append(f"{leave['leave_type']} leave")
        if leave["half_day"]:
            if day.status != "P":  # worked part of the day, half day on leave
                day.status = "HD"
                day.leave = 0.5
                day.leave_type = leave["leave_type"]
        elif day.status in ("A", "HD"):  # dropped in briefly on a full day of leave
            day.status = "L"
            day.leave = 1
            day.leave_type = leave["leave_type"]
    return day


def summarize(days):
    s = Summary()
    for d in days:
        if d.status == "P":
            s.present += 1
        elif d.status == "MP":
            s.present += 1
            s.missing_punch += 1
        elif d.status == "HD":
            s.present += 0.5
            s.half_day += 1
            s.leave += d.leave
            if not d.leave_paid:
                s.unpaid_leave += d.leave
        elif d.status == "A":
            s.absent += 1
        elif d.status == "L":
            s.leave += d.leave
            if not d.leave_paid:
                s.unpaid_leave += d.leave
            s.absent += 1 - d.leave  # the other half of a half-day leave with no punches
        elif d.status == "H":
            s.holiday += 1
        elif d.status == "WO":
            s.weekly_off += 1
        if d.late_minutes:
            s.late += 1
        if d.early_minutes:
            s.early += 1
        s.work_minutes += d.work_minutes
        s.ot_minutes += d.ot_minutes
    return s


def fmt_minutes(minutes):
    if not minutes:
        return ""
    return f"{minutes // 60}:{minutes % 60:02d}"
