"""Turns raw punches into daily attendance using shifts, holidays and leaves.

Nothing here is stored: attendance is always derived from the punches, so
fixing a punch, shift, leave or holiday immediately corrects every report.
"""
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
    remarks: list = field(default_factory=list)

    @property
    def is_late(self):
        return self.late_minutes > 0

    @property
    def label(self):
        return STATUS_LABELS.get(self.status, self.status)


@dataclass
class Summary:
    present: float = 0
    absent: int = 0
    half_day: int = 0
    missing_punch: int = 0
    leave: float = 0
    holiday: int = 0
    weekly_off: int = 0
    late: int = 0
    early: int = 0
    work_minutes: int = 0
    ot_minutes: int = 0

    @property
    def payable(self):
        return self.present + self.leave + self.holiday + self.weekly_off


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


def _hm(value):
    h, m = (value or "00:00").split(":")[:2]
    return timedelta(hours=int(h), minutes=int(m))


def _minutes(td):
    return int(td.total_seconds() // 60)


def load_employees(db, department_id=None, employee_id=None, include_inactive=False):
    sql = ("SELECT e.*, d.name AS department, s.name AS shift_name, s.start_time, s.end_time, "
           "s.grace_in_min, s.grace_out_min, s.break_minutes, s.half_day_hours, s.full_day_hours, s.weekly_off "
           "FROM employees e LEFT JOIN departments d ON d.id = e.department_id "
           "LEFT JOIN shifts s ON s.id = e.shift_id WHERE 1=1")
    args = []
    if not include_inactive:
        sql += " AND e.active = 1"
    if department_id:
        sql += " AND e.department_id = ?"
        args.append(department_id)
    if employee_id:
        sql += " AND e.id = ?"
        args.append(employee_id)
    sql += " ORDER BY CAST(e.employee_no AS INTEGER), e.employee_no"
    return db.execute(sql, args).fetchall()


def compute(db, start, end, employees=None, now=None):
    """Return ``[(employee_row, [Day, ...], Summary), ...]`` for the date range."""
    now = now or datetime.now()
    employees = load_employees(db) if employees is None else employees
    if not employees:
        return []
    settings = dbmod.get_settings(db)
    before = timedelta(hours=float(settings.get("window_before_hours") or 4))
    ot_min = int(settings.get("ot_min_minutes") or 0)

    holidays = {r["date"]: r["name"] for r in db.execute(
        "SELECT date, name FROM holidays WHERE date BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))}

    leaves = {}
    for r in db.execute("SELECT * FROM leaves WHERE start_date <= ? AND end_date >= ?",
                        (end.isoformat(), start.isoformat())):
        leaves.setdefault(r["employee_id"], []).append(r)

    lo = datetime.combine(start, datetime.min.time()) - timedelta(days=1)
    hi = datetime.combine(end, datetime.min.time()) + timedelta(days=2)
    punches = {}
    for r in db.execute("SELECT employee_no, punch_time FROM punches WHERE punch_time >= ? AND punch_time < ? "
                        "ORDER BY punch_time", (lo.strftime(FMT), hi.strftime(FMT))):
        punches.setdefault(r["employee_no"], []).append(datetime.strptime(r["punch_time"], FMT))

    out = []
    for emp in employees:
        times = punches.get(emp["employee_no"], [])
        emp_leaves = leaves.get(emp["id"], [])
        days = [_compute_day(emp, d, times, holidays, emp_leaves, before, ot_min, now)
                for d in daterange(start, end)]
        out.append((emp, days, summarize(days)))
    return out


def _compute_day(emp, d, times, holidays, emp_leaves, before, ot_min, now):
    day = Day(d)
    join = parse_date(emp["join_date"])
    if d > now.date() or (join and d < join):
        return day

    start_td = _hm(emp["start_time"] or "09:00")
    end_td = _hm(emp["end_time"] or "18:00")
    if end_td <= start_td:  # overnight shift
        end_td += timedelta(days=1)
    midnight = datetime.combine(d, datetime.min.time())
    shift_start, shift_end = midnight + start_td, midnight + end_td
    window_start = shift_start - before
    window_end = window_start + timedelta(days=1)
    day_punches = times[bisect_left(times, window_start):bisect_left(times, window_end)]

    iso = d.isoformat()
    weekly_off = str(d.weekday()) in (emp["weekly_off"] or "").split(",")
    holiday = holidays.get(iso)
    leave = next((lv for lv in emp_leaves if lv["start_date"] <= iso <= lv["end_date"]), None)

    if not day_punches:
        if leave:
            day.status = "L"
            day.remarks.append(f"{leave['leave_type']} leave" + (" (half day)" if leave["half_day"] else ""))
        elif holiday:
            day.status = "H"
            day.remarks.append(holiday)
        elif weekly_off:
            day.status = "WO"
        elif d == now.date() and now < shift_end:
            day.status = "-"  # shift still running, not absent yet
        else:
            day.status = "A"
        return day

    day.punch_count = len(day_punches)
    day.first_in = day_punches[0]
    if len(day_punches) > 1:
        day.last_out = day_punches[-1]
        span = _minutes(day.last_out - day.first_in)
        day.work_minutes = max(0, span - int(emp["break_minutes"] or 0)) if span > (emp["break_minutes"] or 0) else span

    late = _minutes(day.first_in - shift_start)
    if late > int(emp["grace_in_min"] or 0):
        day.late_minutes = late
    if day.last_out:
        early = _minutes(shift_end - day.last_out)
        if early > int(emp["grace_out_min"] or 0):
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
        if d == now.date():
            day.status = "P"
            day.remarks.append("In")
        else:
            day.status = "MP"
            day.remarks.append("Single punch")
        return day

    full = float(emp["full_day_hours"] or 0) * 60
    half = float(emp["half_day_hours"] or 0) * 60
    if day.work_minutes >= full:
        day.status = "P"
    elif day.work_minutes >= half:
        day.status = "HD"
    else:
        day.status = "A"
        day.remarks.append("Short hours")
    if leave:
        day.remarks.append(f"{leave['leave_type']} leave")
        if leave["half_day"] and day.status == "A":
            day.status = "HD"
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
            if any("leave" in r for r in d.remarks):
                s.leave += 0.5
        elif d.status == "A":
            s.absent += 1
        elif d.status == "L":
            s.leave += 1
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
