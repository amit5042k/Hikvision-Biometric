"""Employee self-service: own attendance, leave requests and missed-punch corrections.

Only users with role 'employee' and a linked employee record use these pages.
"""
from datetime import date, datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import attendance as att
from . import db as dbmod
from . import leave as lv
from . import login_required
from .db import get_db

bp = Blueprint("portal", __name__, url_prefix="/me")


def _employee():
    """The employee record behind the signed-in self-service login."""
    if g.user["role"] != "employee" or not g.user["employee_id"]:
        abort(403)
    rows = att.load_employees(get_db(), employee_id=g.user["employee_id"], include_inactive=True)
    if not rows:
        abort(403)
    return rows[0]


@bp.route("/")
@login_required
def my_attendance():
    if g.user["role"] != "employee":
        return redirect(url_for("main.dashboard"))
    from .routes import WEEKDAYS, _month_range
    db = get_db()
    emp = _employee()
    start, end = _month_range(request.args.get("month"))
    _, days, summary = att.compute(db, start, end, [emp])[0]
    today_punches = db.execute("SELECT punch_time FROM punches WHERE employee_no = ? AND punch_time LIKE ? "
                               "ORDER BY punch_time", (emp["employee_no"], f"{date.today()}%")).fetchall()
    return render_template("portal/attendance.html", emp=emp, days=days, summary=summary,
                           month=start.strftime("%Y-%m"), month_label=start.strftime("%B %Y"),
                           pad=start.weekday(), weekdays=WEEKDAYS, today_punches=today_punches)


@bp.route("/leave", methods=["GET", "POST"])
@login_required
def my_leave():
    db = get_db()
    emp = _employee()
    if request.method == "POST":
        start = att.parse_date(request.form.get("start_date"))
        end = att.parse_date(request.form.get("end_date")) or start
        leave_type = request.form.get("leave_type", "")
        half_day = 1 if request.form.get("half_day") else 0
        reason = request.form.get("reason", "").strip()[:500] or None
        known = {t["name"] for t in lv.leave_types(db)}
        if not start or end < start or leave_type not in known:
            flash("Choose a leave type and a valid date range.", "error")
        elif half_day and start != end:
            flash("A half-day leave must be for a single date.", "error")
        elif (end - start).days > 366:
            flash("A leave request cannot be longer than a year.", "error")
        elif sum(lv.request_days(db, emp["id"], start, end, half_day).values()) == 0:
            flash("Those dates are all holidays or weekly offs - no leave is needed.", "warning")
        elif db.execute("SELECT 1 FROM leaves WHERE employee_id = ? AND status IN ('pending', 'approved') "
                        "AND start_date <= ? AND end_date >= ?",
                        (emp["id"], end.isoformat(), start.isoformat())).fetchone():
            flash("You already have leave on some of those dates.", "error")
        else:
            problem = lv.check_balance(db, emp["id"], leave_type, start, end, half_day)
            if problem:
                flash(problem, "error")
            else:
                db.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, half_day, reason, "
                           "status, requested_by) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
                           (emp["id"], start.isoformat(), end.isoformat(), leave_type, half_day, reason,
                            g.user["username"]))
                dbmod.audit(db, g.user["username"], "leave.request",
                            f"{leave_type} {start} to {end}{' (half day)' if half_day else ''}")
                db.commit()
                flash("Leave request sent for approval.", "success")
        return redirect(url_for("portal.my_leave"))
    year = date.today().year
    return render_template("portal/leave.html", emp=emp, types=lv.leave_types(db), year=year,
                           balance=lv.balances(db, year, [emp["id"]])[emp["id"]],
                           rows=db.execute("SELECT * FROM leaves WHERE employee_id = ? ORDER BY start_date DESC "
                                           "LIMIT 100", (emp["id"],)).fetchall(),
                           today=date.today())


@bp.route("/leave/<int:leave_id>/cancel", methods=["POST"])
@login_required
def my_leave_cancel(leave_id):
    db = get_db()
    emp = _employee()
    cur = db.execute("DELETE FROM leaves WHERE id = ? AND employee_id = ? AND status = 'pending'",
                     (leave_id, emp["id"]))
    if cur.rowcount:
        dbmod.audit(db, g.user["username"], "leave.cancel", f"request #{leave_id}")
        db.commit()
        flash("Leave request withdrawn.", "success")
    return redirect(url_for("portal.my_leave"))


@bp.route("/corrections", methods=["GET", "POST"])
@login_required
def my_corrections():
    db = get_db()
    emp = _employee()
    if request.method == "POST":
        try:
            when = datetime.strptime(request.form.get("punch_time", ""), "%Y-%m-%dT%H:%M")
        except ValueError:
            when = None
        reason = request.form.get("reason", "").strip()[:500]
        if not when or when > datetime.now():
            flash("Enter the date and time you were actually in or out (not in the future).", "error")
        elif (datetime.now() - when).days > 60:
            flash("Corrections older than 60 days must be raised with HR directly.", "error")
        elif not reason:
            flash("Please give a reason, e.g. 'Forgot to punch out'.", "error")
        else:
            db.execute("INSERT INTO punch_requests (employee_id, punch_time, reason, requested_by) VALUES (?, ?, ?, ?)",
                       (emp["id"], when.strftime(att.FMT), reason, g.user["username"]))
            dbmod.audit(db, g.user["username"], "punch.request", f"{when:%Y-%m-%d %H:%M} - {reason}")
            db.commit()
            flash("Correction request sent for approval.", "success")
        return redirect(url_for("portal.my_corrections"))
    rows = db.execute("SELECT * FROM punch_requests WHERE employee_id = ? ORDER BY created_at DESC LIMIT 100",
                      (emp["id"],)).fetchall()
    return render_template("portal/corrections.html", emp=emp, rows=rows)


@bp.route("/corrections/<int:req_id>/cancel", methods=["POST"])
@login_required
def my_correction_cancel(req_id):
    db = get_db()
    emp = _employee()
    if db.execute("DELETE FROM punch_requests WHERE id = ? AND employee_id = ? AND status = 'pending'",
                  (req_id, emp["id"])).rowcount:
        db.commit()
        flash("Correction request withdrawn.", "success")
    return redirect(url_for("portal.my_corrections"))
