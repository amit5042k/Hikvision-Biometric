"""Leave types, quotas and balances.

A day of leave is only counted on the employee's working days: holidays and
the shift's weekly offs inside a leave period are not deducted.
"""
from datetime import date

from .attendance import daterange, parse_date


def leave_types(db):
    return db.execute("SELECT * FROM leave_types ORDER BY name").fetchall()


def working_days(start, end, weekly_off, holidays, roster=None):
    """Leave days in [start, end] excluding weekly offs, holidays and rostered rest days.

    ``roster`` maps 'YYYY-MM-DD' -> shift id (working) or None (rest day) and
    overrides the shift's weekly offs for those dates.
    """
    offs = {int(w) for w in (weekly_off or "").split(",") if w.strip().isdigit()}
    roster = roster or {}
    count = 0
    for d in daterange(start, end):
        iso = d.isoformat()
        if iso in holidays:
            continue
        working = roster[iso] is not None if iso in roster else d.weekday() not in offs
        count += working
    return count


def _roster(db, employee_id, start, end):
    return {r["date"]: r["shift_id"] for r in db.execute(
        "SELECT date, shift_id FROM roster WHERE employee_id = ? AND date BETWEEN ? AND ?",
        (employee_id, str(start), str(end)))}


def days_of(leave, weekly_off, holidays, year=None, roster=None):
    """How many leave days a leave entry uses (optionally only inside one year)."""
    start, end = parse_date(leave["start_date"]), parse_date(leave["end_date"])
    if not start or not end:
        return 0
    if year:
        start, end = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        if end < start:
            return 0
    n = working_days(start, end, weekly_off, holidays, roster)
    return n * 0.5 if leave["half_day"] else n


def balances(db, year, employee_ids=None):
    """``{employee_id: {type_name: {"quota", "used", "pending", "left"}}}`` for a calendar year."""
    types = leave_types(db)
    holidays = {r["date"] for r in db.execute("SELECT date FROM holidays WHERE date LIKE ?", (f"{year}-%",))}
    sql = ("SELECT l.*, s.weekly_off FROM leaves l JOIN employees e ON e.id = l.employee_id "
           "LEFT JOIN shifts s ON s.id = e.shift_id WHERE l.status IN ('approved', 'pending') "
           "AND l.start_date <= ? AND l.end_date >= ?")
    args = [f"{year}-12-31", f"{year}-01-01"]
    if employee_ids is not None:
        if not employee_ids:
            return {}
        sql += f" AND l.employee_id IN ({','.join('?' * len(employee_ids))})"
        args += list(employee_ids)
    used = {}
    for lv in db.execute(sql, args):
        key = (lv["employee_id"], lv["leave_type"], lv["status"])
        roster = _roster(db, lv["employee_id"], lv["start_date"], lv["end_date"])
        used[key] = used.get(key, 0) + days_of(lv, lv["weekly_off"], holidays, year, roster)
    ids = employee_ids if employee_ids is not None else [r[0] for r in db.execute("SELECT id FROM employees")]
    out = {}
    for emp_id in ids:
        out[emp_id] = {}
        for t in types:
            u = used.get((emp_id, t["name"], "approved"), 0)
            p = used.get((emp_id, t["name"], "pending"), 0)
            quota = t["yearly_quota"] or 0
            out[emp_id][t["name"]] = {"quota": quota, "used": u, "pending": p,
                                      "left": (quota - u) if quota else None, "paid": t["paid"]}
    return out


def request_days(db, employee_id, start, end, half_day):
    """Working days a new request would use, by calendar year."""
    emp = db.execute("SELECT s.weekly_off FROM employees e LEFT JOIN shifts s ON s.id = e.shift_id "
                     "WHERE e.id = ?", (employee_id,)).fetchone()
    holidays = {r["date"] for r in db.execute("SELECT date FROM holidays WHERE date BETWEEN ? AND ?",
                                               (start.isoformat(), end.isoformat()))}
    per_year = {}
    for year in range(start.year, end.year + 1):
        s, e = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        n = working_days(s, e, emp["weekly_off"] if emp else "", holidays, _roster(db, employee_id, s, e))
        per_year[year] = n * 0.5 if half_day else n
    return per_year


def check_balance(db, employee_id, leave_type, start, end, half_day):
    """Return an error message if the request exceeds the yearly quota, else None."""
    t = db.execute("SELECT * FROM leave_types WHERE name = ?", (leave_type,)).fetchone()
    if not t or not t["yearly_quota"]:
        return None
    for year, need in request_days(db, employee_id, start, end, half_day).items():
        b = balances(db, year, [employee_id])[employee_id][leave_type]
        available = b["quota"] - b["used"] - b["pending"]
        if need > available:
            return (f"Not enough {leave_type} leave for {year}: this needs {need:g} day(s) but only "
                    f"{max(available, 0):g} remain (including requests awaiting approval).")
    return None
