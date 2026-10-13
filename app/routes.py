import csv
import io
import json
from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import (Blueprint, Response, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import admin_required, login_required
from . import attendance as att
from . import db as dbmod
from . import sync
from .db import get_db
from .hikvision import HikvisionError

bp = Blueprint("main", __name__)

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _month_range(value):
    try:
        y, m = (int(x) for x in value.split("-"))
    except (AttributeError, ValueError):
        t = date.today()
        y, m = t.year, t.month
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def _csv(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def _lookups():
    db = get_db()
    return {
        "departments": db.execute("SELECT * FROM departments ORDER BY name").fetchall(),
        "shifts": db.execute("SELECT * FROM shifts ORDER BY name").fetchall(),
        "devices": db.execute("SELECT * FROM devices ORDER BY name").fetchall(),
    }


# ---------------------------------------------------------------- auth ----
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE username = ?",
                                (request.form.get("username", "").strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            token = session.get("csrf_token")
            session.clear()
            session["user_id"] = user["id"]
            if token:
                session["csrf_token"] = token
            if request.form.get("password") == "admin" and user["username"] == "admin":
                flash("You are using the default password. Please change it in Settings.", "warning")
            nxt = request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


# ----------------------------------------------------------- dashboard ----
@bp.route("/")
@login_required
def dashboard():
    db = get_db()
    today = date.today()
    rows = att.compute(db, today, today)
    counts = {"total": len(rows), "P": 0, "A": 0, "L": 0, "late": 0, "pending": 0, "off": 0}
    for _, days, _ in rows:
        d = days[0]
        if d.status in ("P", "HD", "MP"):
            counts["P"] += 1
        elif d.status == "A":
            counts["A"] += 1
        elif d.status == "L":
            counts["L"] += 1
        elif d.status in ("H", "WO"):
            counts["off"] += 1
        else:
            counts["pending"] += 1
        if d.is_late:
            counts["late"] += 1

    # last 7 days trend
    week_start = today - timedelta(days=6)
    trend = {d: 0 for d in att.daterange(week_start, today)}
    for _, days, _ in att.compute(db, week_start, today):
        for d in days:
            if d.status in ("P", "HD", "MP"):
                trend[d.date] += 1
    trend_max = max([*trend.values(), 1])

    recent = db.execute(
        "SELECT p.*, e.name, dv.name AS device FROM punches p "
        "LEFT JOIN employees e ON e.employee_no = p.employee_no "
        "LEFT JOIN devices dv ON dv.id = p.device_id ORDER BY p.punch_time DESC LIMIT 15").fetchall()
    devices = db.execute("SELECT * FROM devices ORDER BY name").fetchall()
    late_today = [(e, days[0]) for e, days, _ in rows if days[0].is_late]
    return render_template("dashboard.html", counts=counts, recent=recent, devices=devices,
                           trend=trend, trend_max=trend_max, late_today=late_today, today=today)


# ----------------------------------------------------------- employees ----
@bp.route("/employees")
@login_required
def employees():
    db = get_db()
    q = request.args.get("q", "").strip()
    dept = _int(request.args.get("department"))
    status = request.args.get("status", "active")
    sql = ("SELECT e.*, d.name AS department, s.name AS shift_name FROM employees e "
           "LEFT JOIN departments d ON d.id = e.department_id LEFT JOIN shifts s ON s.id = e.shift_id WHERE 1=1")
    args = []
    if q:
        sql += " AND (e.name LIKE ? OR e.employee_no LIKE ? OR e.card_no LIKE ?)"
        args += [f"%{q}%"] * 3
    if dept:
        sql += " AND e.department_id = ?"
        args.append(dept)
    if status == "active":
        sql += " AND e.active = 1"
    elif status == "inactive":
        sql += " AND e.active = 0"
    sql += " ORDER BY CAST(e.employee_no AS INTEGER), e.employee_no"
    return render_template("employees.html", employees=db.execute(sql, args).fetchall(),
                           q=q, dept=dept, status=status, **_lookups())


@bp.route("/employees/new", methods=["GET", "POST"])
@bp.route("/employees/<int:emp_id>/edit", methods=["GET", "POST"])
@admin_required
def employee_form(emp_id=None):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone() if emp_id else None
    if emp_id and not emp:
        flash("Employee not found.", "error")
        return redirect(url_for("main.employees"))
    if request.method == "POST":
        f = request.form
        data = {
            "employee_no": f.get("employee_no", "").strip(),
            "name": f.get("name", "").strip(),
            "department_id": _int(f.get("department_id")),
            "shift_id": _int(f.get("shift_id")),
            "designation": f.get("designation", "").strip() or None,
            "email": f.get("email", "").strip() or None,
            "phone": f.get("phone", "").strip() or None,
            "card_no": f.get("card_no", "").strip() or None,
            "gender": f.get("gender") or None,
            "join_date": f.get("join_date") or None,
            "active": 1 if f.get("active") else 0,
        }
        if not data["employee_no"] or not data["name"]:
            flash("Employee number and name are required.", "error")
        elif not data["employee_no"].isalnum() or len(data["employee_no"]) > 32:
            flash("Employee number must be letters/digits only (max 32).", "error")
        else:
            clash = db.execute("SELECT id FROM employees WHERE employee_no = ? AND id != ?",
                               (data["employee_no"], emp_id or 0)).fetchone()
            if clash:
                flash("Another employee already uses that employee number.", "error")
            else:
                cols = list(data)
                if emp:
                    if emp["employee_no"] != data["employee_no"]:
                        db.execute("UPDATE punches SET employee_no = ? WHERE employee_no = ?",
                                   (data["employee_no"], emp["employee_no"]))
                    db.execute(f"UPDATE employees SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                               [data[c] for c in cols] + [emp_id])
                else:
                    cur = db.execute(f"INSERT INTO employees ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                     [data[c] for c in cols])
                    emp_id = cur.lastrowid
                db.commit()
                msg = "Employee saved."
                if f.get("push"):
                    msg += " " + _push_to_devices(emp_id)
                flash(msg, "success")
                return redirect(url_for("main.employee_detail", emp_id=emp_id))
        emp = data | {"id": emp_id}
    return render_template("employee_form.html", emp=emp, **_lookups())


def _push_to_devices(emp_id, device_ids=None):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    sql = "SELECT * FROM devices WHERE enabled = 1"
    devices = [d for d in db.execute(sql) if not device_ids or d["id"] in device_ids]
    if not devices:
        return "No enabled devices to sync to."
    ok, errors = [], []
    for dev in devices:
        try:
            sync.push_employee(dev, emp)
            ok.append(dev["name"])
        except HikvisionError as exc:
            errors.append(f"{dev['name']}: {exc}")
    parts = []
    if ok:
        parts.append("Sent to " + ", ".join(ok) + ".")
    if errors:
        parts.append("Failed - " + "; ".join(errors))
    return " ".join(parts)


@bp.route("/employees/<int:emp_id>")
@login_required
def employee_detail(emp_id):
    db = get_db()
    emps = att.load_employees(db, employee_id=emp_id, include_inactive=True)
    if not emps:
        flash("Employee not found.", "error")
        return redirect(url_for("main.employees"))
    start, end = _month_range(request.args.get("month"))
    _, days, summary = att.compute(db, start, end, emps)[0]
    leaves = db.execute("SELECT * FROM leaves WHERE employee_id = ? ORDER BY start_date DESC LIMIT 20",
                        (emp_id,)).fetchall()
    # calendar padding so the 1st falls on the right weekday column
    pad = start.weekday()
    return render_template("employee_detail.html", emp=emps[0], days=days, summary=summary, leaves=leaves,
                           month=start.strftime("%Y-%m"), month_label=start.strftime("%B %Y"), pad=pad,
                           weekdays=WEEKDAYS, devices=_lookups()["devices"])


@bp.route("/employees/<int:emp_id>/push", methods=["POST"])
@admin_required
def employee_push(emp_id):
    ids = [int(x) for x in request.form.getlist("device_id") if x.isdigit()]
    flash(_push_to_devices(emp_id, ids or None), "info")
    return redirect(url_for("main.employee_detail", emp_id=emp_id))


@bp.route("/employees/<int:emp_id>/face", methods=["POST"])
@admin_required
def employee_face(emp_id):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    file = request.files.get("photo")
    if not emp or not file or not file.filename:
        flash("Choose a JPEG photo to upload.", "error")
        return redirect(url_for("main.employee_detail", emp_id=emp_id))
    data = file.read()
    if not data.startswith(b"\xff\xd8"):
        flash("The face photo must be a JPEG image (ideally under 200 KB, face clearly visible).", "error")
        return redirect(url_for("main.employee_detail", emp_id=emp_id))
    ok, errors = [], []
    for dev in db.execute("SELECT * FROM devices WHERE enabled = 1").fetchall():
        try:
            client = sync.client_for(dev)
            client.upsert_user(emp["employee_no"], emp["name"], emp["gender"])
            client.upload_face(emp["employee_no"], data)
            ok.append(dev["name"])
        except HikvisionError as exc:
            errors.append(f"{dev['name']}: {exc}")
    if ok:
        db.execute("UPDATE employees SET has_face = 1 WHERE id = ?", (emp_id,))
        db.commit()
        flash("Face enrolled on " + ", ".join(ok) + ".", "success")
    if errors:
        flash("Face upload failed - " + "; ".join(errors), "error")
    if not ok and not errors:
        flash("No enabled devices configured.", "error")
    return redirect(url_for("main.employee_detail", emp_id=emp_id))


@bp.route("/employees/<int:emp_id>/delete", methods=["POST"])
@admin_required
def employee_delete(emp_id):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    if not emp:
        return redirect(url_for("main.employees"))
    msgs = []
    if request.form.get("from_devices"):
        for dev in db.execute("SELECT * FROM devices WHERE enabled = 1").fetchall():
            try:
                sync.client_for(dev).delete_user(emp["employee_no"])
            except HikvisionError as exc:
                msgs.append(f"{dev['name']}: {exc}")
    if request.form.get("keep_history"):
        db.execute("UPDATE employees SET active = 0 WHERE id = ?", (emp_id,))
        text = f"{emp['name']} deactivated."
    else:
        db.execute("DELETE FROM punches WHERE employee_no = ?", (emp["employee_no"],))
        db.execute("DELETE FROM employees WHERE id = ?", (emp_id,))
        text = f"{emp['name']} deleted."
    db.commit()
    flash(text + (" Device errors: " + "; ".join(msgs) if msgs else ""), "warning" if msgs else "success")
    return redirect(url_for("main.employees"))


@bp.route("/employees/import", methods=["POST"])
@admin_required
def employees_import_csv():
    """CSV columns: employee_no,name[,department,designation,card_no,phone,email,join_date]"""
    db = get_db()
    file = request.files.get("file")
    if not file:
        flash("Choose a CSV file.", "error")
        return redirect(url_for("main.employees"))
    reader = csv.DictReader(io.StringIO(file.read().decode("utf-8-sig")))
    added = updated = 0
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        no, name = row.get("employee_no"), row.get("name")
        if not no or not name:
            continue
        dept_id = None
        if row.get("department"):
            db.execute("INSERT OR IGNORE INTO departments (name) VALUES (?)", (row["department"],))
            dept_id = db.execute("SELECT id FROM departments WHERE name = ?", (row["department"],)).fetchone()["id"]
        fields = {"name": name, "department_id": dept_id, "designation": row.get("designation") or None,
                  "card_no": row.get("card_no") or None, "phone": row.get("phone") or None,
                  "email": row.get("email") or None, "join_date": row.get("join_date") or None}
        if db.execute("SELECT 1 FROM employees WHERE employee_no = ?", (no,)).fetchone():
            sets = {k: v for k, v in fields.items() if v is not None}
            db.execute(f"UPDATE employees SET {', '.join(k + ' = ?' for k in sets)} WHERE employee_no = ?",
                       [*sets.values(), no])
            updated += 1
        else:
            db.execute("INSERT INTO employees (employee_no, name, department_id, designation, card_no, phone, email, "
                       "join_date, shift_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, (SELECT MIN(id) FROM shifts))",
                       (no, *fields.values()))
            added += 1
    db.commit()
    flash(f"Imported {added} new and updated {updated} employees.", "success")
    return redirect(url_for("main.employees"))


@bp.route("/employees/export.csv")
@login_required
def employees_export():
    rows = att.load_employees(get_db(), include_inactive=True)
    return _csv("employees.csv",
                ["employee_no", "name", "department", "designation", "shift", "card_no", "phone", "email",
                 "join_date", "active"],
                [[e["employee_no"], e["name"], e["department"] or "", e["designation"] or "", e["shift_name"] or "",
                  e["card_no"] or "", e["phone"] or "", e["email"] or "", e["join_date"] or "", e["active"]]
                 for e in rows])


# ------------------------------------------------------------- devices ----
@bp.route("/devices")
@login_required
def devices():
    db = get_db()
    rows = db.execute("SELECT d.*, (SELECT COUNT(*) FROM punches p WHERE p.device_id = d.id) AS punch_count "
                      "FROM devices d ORDER BY name").fetchall()
    return render_template("devices.html", devices=rows,
                           push_token=dbmod.get_setting(db, "push_token"))


@bp.route("/devices/new", methods=["GET", "POST"])
@bp.route("/devices/<int:dev_id>/edit", methods=["GET", "POST"])
@admin_required
def device_form(dev_id=None):
    db = get_db()
    dev = db.execute("SELECT * FROM devices WHERE id = ?", (dev_id,)).fetchone() if dev_id else None
    if request.method == "POST":
        f = request.form
        data = {
            "name": f.get("name", "").strip() or "Terminal",
            "host": f.get("host", "").strip(),
            "port": _int(f.get("port"), 80),
            "scheme": "https" if f.get("scheme") == "https" else "http",
            "username": f.get("username", "").strip() or "admin",
            "door_no": _int(f.get("door_no"), 1),
            "tz_offset": f.get("tz_offset", "+05:30").strip() or "+05:30",
            "enabled": 1 if f.get("enabled") else 0,
        }
        if f.get("password") or not dev:
            data["password"] = f.get("password", "")
        if not data["host"]:
            flash("Device IP / host is required.", "error")
        else:
            cols = list(data)
            if dev:
                db.execute(f"UPDATE devices SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                           [data[c] for c in cols] + [dev_id])
            else:
                dev_id = db.execute(f"INSERT INTO devices ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                    [data[c] for c in cols]).lastrowid
            db.commit()
            flash("Device saved. Use “Test” to verify the connection.", "success")
            return redirect(url_for("main.devices"))
        dev = data
    return render_template("device_form.html", dev=dev)


@bp.route("/devices/<int:dev_id>/delete", methods=["POST"])
@admin_required
def device_delete(dev_id):
    db = get_db()
    db.execute("DELETE FROM devices WHERE id = ?", (dev_id,))
    db.commit()
    flash("Device removed. Its punches are kept.", "success")
    return redirect(url_for("main.devices"))


@bp.route("/devices/<int:dev_id>/<action>", methods=["POST"])
@admin_required
def device_action(dev_id, action):
    db = get_db()
    dev = db.execute("SELECT * FROM devices WHERE id = ?", (dev_id,)).fetchone()
    if not dev:
        flash("Device not found.", "error")
        return redirect(url_for("main.devices"))
    try:
        client = sync.client_for(dev)
        if action == "test":
            info = client.device_info()
            db.execute("UPDATE devices SET model = ?, serial_no = ?, firmware = ?, mac_address = ?, "
                       "last_status = 'ok', last_error = NULL WHERE id = ?",
                       (info.get("model"), info.get("serialNumber"), info.get("firmwareVersion"),
                        info.get("macAddress"), dev_id))
            db.commit()
            flash(f"Connected: {info.get('model', '?')} (S/N {info.get('serialNumber', '?')}, "
                  f"firmware {info.get('firmwareVersion', '?')}).", "success")
        elif action == "sync-events":
            start = att.parse_date(request.form.get("start"))
            end = att.parse_date(request.form.get("end"))
            res = sync.sync_events(
                db, dev,
                datetime.combine(start, datetime.min.time()) if start else None,
                datetime.combine(end, datetime.max.time().replace(microsecond=0)) if end else None)
            flash(f"Downloaded {res['seen']} events, {res['added']} new punches.", "success")
        elif action == "sync-users":
            res = sync.sync_users_from_device(db, dev)
            flash(f"Imported users: {res['created']} new, {res['existing']} already known.", "success")
        elif action == "push-users":
            count, errors = 0, []
            for emp in db.execute("SELECT * FROM employees WHERE active = 1"):
                try:
                    sync.push_employee(dev, emp, client)
                    count += 1
                except HikvisionError as exc:
                    errors.append(f"{emp['employee_no']}: {exc}")
            flash(f"Sent {count} employees to {dev['name']}." +
                  (f" {len(errors)} failed: " + "; ".join(errors[:5]) if errors else ""),
                  "warning" if errors else "success")
        elif action == "set-time":
            client.set_time(sync.device_now(dev["tz_offset"]))
            flash("Device clock set to current time.", "success")
        elif action == "open-door":
            client.open_door()
            flash("Door open command sent.", "success")
        elif action == "configure-push":
            ip = request.form.get("server_ip", "").strip()
            port = _int(request.form.get("server_port"), 5000)
            if not ip:
                flash("Enter this server's LAN IP address.", "error")
            else:
                token = dbmod.get_setting(db, "push_token")
                path = url_for("main.device_push") + (f"?token={token}" if token else "")
                client.set_http_host(ip, port, path)
                flash(f"Device will now push events to http://{ip}:{port}{path}", "success")
        else:
            flash("Unknown action.", "error")
    except HikvisionError as exc:
        db.execute("UPDATE devices SET last_status = 'error', last_error = ? WHERE id = ?", (str(exc), dev_id))
        db.commit()
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("main.devices"))


@bp.route("/api/hik/event", methods=["POST"])
def device_push():
    """Receiver for the terminal's HTTP listening / event push feature."""
    db = get_db()
    token = dbmod.get_setting(db, "push_token")
    if token and request.args.get("token") != token:
        return jsonify({"error": "bad token"}), 403
    payloads = []
    if request.is_json:
        payloads.append(request.get_json(silent=True) or {})
    else:
        for value in list(request.form.values()) + [f.read().decode("utf-8", "ignore") for f in request.files.values()]:
            try:
                payloads.append(json.loads(value))
            except (TypeError, ValueError):
                continue
        if not payloads and request.data:
            try:
                payloads.append(json.loads(request.data))
            except ValueError:
                pass
    added = 0
    for payload in payloads:
        if isinstance(payload, dict) and sync.handle_push_event(db, payload, request.remote_addr):
            added += 1
    return jsonify({"status": "ok", "added": added})


# ---------------------------------------------------------- attendance ----
@bp.route("/attendance")
@login_required
def attendance_daily():
    db = get_db()
    day = att.parse_date(request.args.get("date"), date.today())
    dept = _int(request.args.get("department"))
    status = request.args.get("status", "")
    rows = att.compute(db, day, day, att.load_employees(db, department_id=dept))
    rows = [(e, days[0]) for e, days, _ in rows]
    if status == "late":
        rows = [r for r in rows if r[1].is_late]
    elif status:
        rows = [r for r in rows if r[1].status == status]
    if request.args.get("export"):
        return _csv(f"attendance-{day}.csv",
                    ["Employee No", "Name", "Department", "Shift", "Status", "In", "Out", "Work", "Late (min)",
                     "Early (min)", "OT", "Remarks"],
                    [[e["employee_no"], e["name"], e["department"] or "", e["shift_name"] or "", d.label,
                      d.first_in.strftime("%H:%M") if d.first_in else "",
                      d.last_out.strftime("%H:%M") if d.last_out else "", att.fmt_minutes(d.work_minutes),
                      d.late_minutes or "", d.early_minutes or "", att.fmt_minutes(d.ot_minutes),
                      "; ".join(d.remarks)] for e, d in rows])
    return render_template("attendance_daily.html", rows=rows, day=day, dept=dept, status=status,
                           prev=day - timedelta(days=1), next=day + timedelta(days=1), **_lookups())


@bp.route("/punches")
@login_required
def punches():
    db = get_db()
    start = att.parse_date(request.args.get("start"), date.today() - timedelta(days=6))
    end = att.parse_date(request.args.get("end"), date.today())
    q = request.args.get("q", "").strip()
    sql = ("SELECT p.*, e.name, e.id AS emp_id, dv.name AS device FROM punches p "
           "LEFT JOIN employees e ON e.employee_no = p.employee_no "
           "LEFT JOIN devices dv ON dv.id = p.device_id WHERE p.punch_time BETWEEN ? AND ?")
    args = [f"{start} 00:00:00", f"{end} 23:59:59"]
    if q:
        sql += " AND (p.employee_no = ? OR e.name LIKE ?)"
        args += [q, f"%{q}%"]
    sql += " ORDER BY p.punch_time DESC"
    rows = db.execute(sql, args).fetchall()
    if request.args.get("export"):
        return _csv(f"punches-{start}-to-{end}.csv",
                    ["Employee No", "Name", "Time", "Device", "Source", "Verify Mode", "Note"],
                    [[r["employee_no"], r["name"] or "", r["punch_time"], r["device"] or "", r["source"],
                      r["verify_mode"] or "", r["note"] or ""] for r in rows])
    employees = db.execute("SELECT id, employee_no, name FROM employees WHERE active = 1 "
                           "ORDER BY name").fetchall()
    return render_template("punches.html", rows=rows[:2000], total=len(rows), start=start, end=end, q=q,
                           employees=employees)


@bp.route("/punches/add", methods=["POST"])
@admin_required
def punch_add():
    db = get_db()
    emp = db.execute("SELECT employee_no FROM employees WHERE id = ?",
                     (_int(request.form.get("employee_id")),)).fetchone()
    try:
        when = datetime.strptime(request.form.get("punch_time", ""), "%Y-%m-%dT%H:%M")
    except ValueError:
        when = None
    if not emp or not when:
        flash("Select an employee and a valid date/time.", "error")
    else:
        note = request.form.get("note", "").strip() or "Manual entry"
        note += f" (by {g.user['username']})"
        if sync.record_punch(db, emp["employee_no"], when.strftime(att.FMT), source="manual", note=note):
            flash("Manual punch added.", "success")
        else:
            flash("A punch already exists at that time.", "warning")
        db.commit()
    return redirect(request.referrer or url_for("main.punches"))


@bp.route("/punches/<int:punch_id>/delete", methods=["POST"])
@admin_required
def punch_delete(punch_id):
    db = get_db()
    db.execute("DELETE FROM punches WHERE id = ?", (punch_id,))
    db.commit()
    flash("Punch deleted.", "success")
    return redirect(request.referrer or url_for("main.punches"))


# ------------------------------------------------------------- reports ----
@bp.route("/reports/monthly")
@login_required
def report_monthly():
    db = get_db()
    start, end = _month_range(request.args.get("month"))
    dept = _int(request.args.get("department"))
    view = request.args.get("view", "muster")
    rows = att.compute(db, start, end, att.load_employees(db, department_id=dept))
    days = list(att.daterange(start, end))
    export = request.args.get("export")
    if export == "muster":
        header = ["Employee No", "Name", "Department"] + [d.strftime("%d") for d in days] + \
                 ["Present", "Absent", "Half Day", "Leave", "Holiday", "Weekly Off", "Late", "Payable"]
        return _csv(f"muster-{start:%Y-%m}.csv", header,
                    [[e["employee_no"], e["name"], e["department"] or ""] + [d.status for d in ds] +
                     [s.present, s.absent, s.half_day, s.leave, s.holiday, s.weekly_off, s.late, s.payable]
                     for e, ds, s in rows])
    if export == "summary":
        return _csv(f"summary-{start:%Y-%m}.csv",
                    ["Employee No", "Name", "Department", "Present", "Absent", "Half Day", "Missing Punch",
                     "Leave", "Holiday", "Weekly Off", "Late Days", "Early Exits", "Work Hours", "OT Hours",
                     "Payable Days"],
                    [[e["employee_no"], e["name"], e["department"] or "", s.present, s.absent, s.half_day,
                      s.missing_punch, s.leave, s.holiday, s.weekly_off, s.late, s.early,
                      att.fmt_minutes(s.work_minutes), att.fmt_minutes(s.ot_minutes), s.payable]
                     for e, _, s in rows])
    if export == "detail":
        out = []
        for e, ds, _ in rows:
            for d in ds:
                out.append([e["employee_no"], e["name"], d.date.isoformat(), d.label,
                            d.first_in.strftime("%H:%M") if d.first_in else "",
                            d.last_out.strftime("%H:%M") if d.last_out else "",
                            att.fmt_minutes(d.work_minutes), d.late_minutes or "", d.early_minutes or "",
                            att.fmt_minutes(d.ot_minutes), "; ".join(d.remarks)])
        return _csv(f"attendance-detail-{start:%Y-%m}.csv",
                    ["Employee No", "Name", "Date", "Status", "In", "Out", "Work", "Late (min)", "Early (min)",
                     "OT", "Remarks"], out)
    return render_template("report_monthly.html", rows=rows, days=days, month=start.strftime("%Y-%m"),
                           month_label=start.strftime("%B %Y"), dept=dept, view=view, weekdays=WEEKDAYS,
                           **_lookups())


# -------------------------------------------------------------- leaves ----
@bp.route("/leaves", methods=["GET", "POST"])
@login_required
def leaves():
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can add leave.", "error")
            return redirect(url_for("main.leaves"))
        emp_id = _int(request.form.get("employee_id"))
        start = att.parse_date(request.form.get("start_date"))
        end = att.parse_date(request.form.get("end_date")) or start
        if not emp_id or not start or end < start:
            flash("Select an employee and a valid date range.", "error")
        else:
            db.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, half_day, reason) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (emp_id, start.isoformat(), end.isoformat(), request.form.get("leave_type") or "Casual",
                        1 if request.form.get("half_day") else 0, request.form.get("reason", "").strip() or None))
            db.commit()
            flash("Leave recorded.", "success")
        return redirect(url_for("main.leaves"))
    rows = db.execute("SELECT l.*, e.name, e.employee_no FROM leaves l JOIN employees e ON e.id = l.employee_id "
                      "ORDER BY l.start_date DESC LIMIT 300").fetchall()
    employees = db.execute("SELECT id, employee_no, name FROM employees WHERE active = 1 ORDER BY name").fetchall()
    return render_template("leaves.html", rows=rows, employees=employees, today=date.today())


@bp.route("/leaves/<int:leave_id>/delete", methods=["POST"])
@admin_required
def leave_delete(leave_id):
    db = get_db()
    db.execute("DELETE FROM leaves WHERE id = ?", (leave_id,))
    db.commit()
    flash("Leave removed.", "success")
    return redirect(url_for("main.leaves"))


# ---------------------------------------------------- holidays & setup ----
@bp.route("/holidays", methods=["GET", "POST"])
@login_required
def holidays():
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can add holidays.", "error")
            return redirect(url_for("main.holidays"))
        day = att.parse_date(request.form.get("date"))
        name = request.form.get("name", "").strip()
        if not day or not name:
            flash("Date and name are required.", "error")
        else:
            db.execute("INSERT INTO holidays (date, name) VALUES (?, ?) "
                       "ON CONFLICT(date) DO UPDATE SET name = excluded.name", (day.isoformat(), name))
            db.commit()
            flash("Holiday saved.", "success")
        return redirect(url_for("main.holidays"))
    year = _int(request.args.get("year"), date.today().year)
    rows = db.execute("SELECT * FROM holidays WHERE date LIKE ? ORDER BY date", (f"{year}-%",)).fetchall()
    return render_template("holidays.html", rows=rows, year=year)


@bp.route("/holidays/<int:hid>/delete", methods=["POST"])
@admin_required
def holiday_delete(hid):
    db = get_db()
    db.execute("DELETE FROM holidays WHERE id = ?", (hid,))
    db.commit()
    return redirect(url_for("main.holidays"))


@bp.route("/shifts", methods=["GET", "POST"])
@login_required
def shifts():
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can edit shifts.", "error")
            return redirect(url_for("main.shifts"))
        f = request.form
        sid = _int(f.get("id"))
        data = {
            "name": f.get("name", "").strip(),
            "start_time": f.get("start_time") or "09:00",
            "end_time": f.get("end_time") or "18:00",
            "grace_in_min": _int(f.get("grace_in_min"), 0),
            "grace_out_min": _int(f.get("grace_out_min"), 0),
            "break_minutes": _int(f.get("break_minutes"), 0),
            "half_day_hours": float(f.get("half_day_hours") or 0),
            "full_day_hours": float(f.get("full_day_hours") or 0),
            "weekly_off": ",".join(sorted(f.getlist("weekly_off"))),
        }
        if not data["name"]:
            flash("Shift name is required.", "error")
        else:
            cols = list(data)
            try:
                if sid:
                    db.execute(f"UPDATE shifts SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                               [data[c] for c in cols] + [sid])
                else:
                    db.execute(f"INSERT INTO shifts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [data[c] for c in cols])
                db.commit()
                flash("Shift saved.", "success")
            except Exception:
                flash("A shift with that name already exists.", "error")
        return redirect(url_for("main.shifts"))
    rows = db.execute("SELECT s.*, (SELECT COUNT(*) FROM employees e WHERE e.shift_id = s.id) AS emp_count "
                      "FROM shifts s ORDER BY name").fetchall()
    edit = db.execute("SELECT * FROM shifts WHERE id = ?", (_int(request.args.get("edit")),)).fetchone()
    return render_template("shifts.html", rows=rows, edit=edit, weekdays=WEEKDAYS)


@bp.route("/shifts/<int:sid>/delete", methods=["POST"])
@admin_required
def shift_delete(sid):
    db = get_db()
    db.execute("DELETE FROM shifts WHERE id = ?", (sid,))
    db.commit()
    flash("Shift deleted; its employees now have no shift (default 09:00-18:00 is used).", "success")
    return redirect(url_for("main.shifts"))


@bp.route("/departments", methods=["POST"])
@admin_required
def department_add():
    db = get_db()
    name = request.form.get("name", "").strip()
    if name:
        db.execute("INSERT OR IGNORE INTO departments (name) VALUES (?)", (name,))
        db.commit()
    return redirect(url_for("main.settings"))


@bp.route("/departments/<int:did>/delete", methods=["POST"])
@admin_required
def department_delete(did):
    db = get_db()
    db.execute("DELETE FROM departments WHERE id = ?", (did,))
    db.commit()
    return redirect(url_for("main.settings"))


@bp.route("/bulk-shift", methods=["POST"])
@admin_required
def bulk_shift():
    db = get_db()
    ids = [int(x) for x in request.form.getlist("employee_id") if x.isdigit()]
    shift_id = _int(request.form.get("shift_id"))
    dept_id = _int(request.form.get("department_id"))
    if ids and (shift_id or dept_id):
        marks = ",".join("?" * len(ids))
        if shift_id:
            db.execute(f"UPDATE employees SET shift_id = ? WHERE id IN ({marks})", [shift_id, *ids])
        if dept_id:
            db.execute(f"UPDATE employees SET department_id = ? WHERE id IN ({marks})", [dept_id, *ids])
        db.commit()
        flash(f"Updated {len(ids)} employees.", "success")
    else:
        flash("Select employees and a shift or department.", "error")
    return redirect(request.referrer or url_for("main.employees"))


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    db = get_db()
    if request.method == "POST":
        form = request.form.get("form")
        if form == "password":
            user = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
            new = request.form.get("new_password", "")
            if not check_password_hash(user["password_hash"], request.form.get("current_password", "")):
                flash("Current password is wrong.", "error")
            elif len(new) < 6 or new != request.form.get("confirm_password"):
                flash("New password must be at least 6 characters and match the confirmation.", "error")
            else:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), user["id"]))
                db.commit()
                flash("Password changed.", "success")
        elif g.user["role"] != "admin":
            flash("Only administrators can change settings.", "error")
        elif form == "general":
            for key in dbmod.DEFAULT_SETTINGS:
                if key in request.form:
                    dbmod.set_setting(db, key, request.form[key].strip())
            db.commit()
            flash("Settings saved.", "success")
        elif form == "user":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = "viewer" if request.form.get("role") == "viewer" else "admin"
            if not username or len(password) < 6:
                flash("Username and a password of at least 6 characters are required.", "error")
            elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                flash("That username is taken.", "error")
            else:
                db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                           (username, generate_password_hash(password), role))
                db.commit()
                flash(f"User {username} created.", "success")
        elif form == "delete_user":
            uid = _int(request.form.get("user_id"))
            if uid == g.user["id"]:
                flash("You cannot delete yourself.", "error")
            else:
                db.execute("DELETE FROM users WHERE id = ?", (uid,))
                db.commit()
                flash("User deleted.", "success")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", settings=dbmod.get_settings(db),
                           users=db.execute("SELECT id, username, role, created_at FROM users ORDER BY username"),
                           departments=db.execute(
                               "SELECT d.*, (SELECT COUNT(*) FROM employees e WHERE e.department_id = d.id) AS n "
                               "FROM departments d ORDER BY name").fetchall())
