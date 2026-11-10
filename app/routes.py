import base64
import hmac
import csv
import io
import json
import secrets
import sqlite3
import time
from calendar import monthrange
from datetime import date, datetime, timedelta

from flask import (Blueprint, Response, current_app, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import admin_required, login_required, staff_required
from . import attendance as att
from . import leave as lv
from . import db as dbmod
from . import sync
from .db import get_db

bp = Blueprint("main", __name__)

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _month_range(value):
    try:
        y, m = (int(x) for x in value.split("-"))
        first = date(y, m, 1)
    except (AttributeError, TypeError, ValueError):
        first = date.today().replace(day=1)
    return first, first.replace(day=monthrange(first.year, first.month)[1])


def _cell(value):
    """Neutralise spreadsheet formulas: a name like '=HYPERLINK(...)' must stay text in Excel."""
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


def _audit(action, details=""):
    dbmod.audit(get_db(), g.user["username"] if g.get("user") else None, action, details)


def _lookups():
    db = get_db()
    return {
        "departments": db.execute("SELECT * FROM departments ORDER BY name").fetchall(),
        "shifts": db.execute("SELECT * FROM shifts ORDER BY name").fetchall(),
        "sites": db.execute("SELECT * FROM sites ORDER BY name").fetchall(),
        "devices": db.execute("SELECT * FROM devices ORDER BY name").fetchall(),
    }


# ---------------------------------------------------------------- auth ----
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        db = get_db()
        username = request.form.get("username", "").strip()
        if _too_many_attempts(db, username):
            flash(f"Too many failed sign-in attempts. Try again in {LOCKOUT_MINUTES} minutes.", "error")
            return render_template("login.html"), 429
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            if user["totp_enabled"]:  # password was right; now ask for the authenticator code
                token = session.get("csrf_token")
                session.clear()
                session.update({"2fa_user": user["id"], "2fa_at": time.time(), "2fa_next": request.args.get("next")})
                if token:
                    session["csrf_token"] = token
                return redirect(url_for("twofactor.login_code"))
            db.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
            dbmod.audit(db, username, "login", request.remote_addr or "")
            db.commit()
            token = session.get("csrf_token")
            session.clear()
            session["user_id"] = user["id"]
            if token:
                session["csrf_token"] = token
            if request.form.get("password") == "admin" and user["username"] == "admin":
                flash("You are using the default password. Please change it in Settings.", "warning")
            if user["role"] == "employee":
                return redirect(url_for("portal.my_attendance"))
            return redirect(_safe_next(request.args.get("next")))
        db.execute("INSERT INTO login_attempts (username, ip) VALUES (?, ?)", (username, request.remote_addr))
        db.commit()
        flash("Invalid username or password.", "error")
    return render_template("login.html")


LOCKOUT_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _too_many_attempts(db, username):
    """Lock a username, and an IP address, after repeated failures."""
    since = f"-{LOCKOUT_MINUTES} minutes"
    db.execute("DELETE FROM login_attempts WHERE created_at < datetime('now', '-1 day')")
    by_user = db.execute("SELECT COUNT(*) FROM login_attempts WHERE username = ? "
                         "AND created_at >= datetime('now', ?)", (username, since)).fetchone()[0]
    by_ip = db.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = ? "
                       "AND created_at >= datetime('now', ?)", (request.remote_addr, since)).fetchone()[0]
    return by_user >= LOCKOUT_ATTEMPTS or by_ip >= LOCKOUT_ATTEMPTS * 4


def _safe_next(target):
    """Only follow same-site paths after login ('/x', never '//host' or '/\\host')."""
    target = target or ""
    if target.startswith("/") and not target.startswith(("//", "/\\")) and "\\" not in target:
        return target
    return url_for("main.dashboard")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


# ----------------------------------------------------------- dashboard ----
@bp.route("/")
@staff_required
def dashboard():
    db = get_db()
    today = date.today()
    site = _int(request.args.get("site"))
    rows = att.compute(db, today, today, att.load_employees(db, site_id=site))
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
    for _, days, _ in att.compute(db, week_start, today, att.load_employees(db, site_id=site)):
        for d in days:
            if d.status in ("P", "HD", "MP"):
                trend[d.date] += 1
    trend_max = max([*trend.values(), 1])

    recent = db.execute(
        "SELECT p.*, e.name, dv.name AS device FROM punches p "
        "LEFT JOIN employees e ON e.employee_no = p.employee_no "
        "LEFT JOIN devices dv ON dv.id = p.device_id ORDER BY p.punch_time DESC LIMIT 15").fetchall()
    devices = db.execute("SELECT * FROM devices WHERE ? IS NULL OR site_id = ? ORDER BY name", (site, site)).fetchall()
    late_today = [(e, days[0]) for e, days, _ in rows if days[0].is_late]
    pending = (db.execute("SELECT COUNT(*) FROM leaves WHERE status = 'pending'").fetchone()[0] +
               db.execute("SELECT COUNT(*) FROM punch_requests WHERE status = 'pending'").fetchone()[0])
    return render_template("dashboard.html", counts=counts, recent=recent, devices=devices, pending=pending,
                           site=site, sites=_lookups()["sites"],
                           trend=trend, trend_max=trend_max, late_today=late_today, today=today)


# ----------------------------------------------------------- employees ----
@bp.route("/employees")
@staff_required
def employees():
    db = get_db()
    q = request.args.get("q", "").strip()
    dept = _int(request.args.get("department"))
    site = _int(request.args.get("site"))
    status = request.args.get("status", "active")
    sql = ("SELECT e.*, d.name AS department, s.name AS shift_name, st.name AS site FROM employees e "
           "LEFT JOIN departments d ON d.id = e.department_id LEFT JOIN shifts s ON s.id = e.shift_id "
           "LEFT JOIN sites st ON st.id = e.site_id WHERE 1=1")
    args = []
    if site:
        sql += " AND e.site_id = ?"
        args.append(site)
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
                           q=q, dept=dept, site=site, status=status, **_lookups())


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
            "site_id": _int(f.get("site_id")),
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
        elif not _valid_employee_no(data["employee_no"]):
            flash("Employee number must be letters/digits only (max 32).", "error")
        elif data["join_date"] and not att.parse_date(data["join_date"]):
            flash("Joining date is not a valid date.", "error")
        elif data["card_no"] and db.execute("SELECT 1 FROM employees WHERE card_no = ? AND id != ?",
                                            (data["card_no"], emp_id or 0)).fetchone():
            flash("Another employee already has that card number.", "error")
        elif emp and emp["employee_no"] != data["employee_no"] and db.execute(
                "SELECT 1 FROM punches WHERE employee_no = ? AND source != 'manual' LIMIT 1",
                (emp["employee_no"],)).fetchone():
            flash("This employee number is already used by the terminal (it has device punches), so it "
                  "cannot be changed - the terminal would keep sending punches under the old number.", "error")
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
                _audit("employee.update" if emp else "employee.create", f"#{data['employee_no']} {data['name']}")
                db.commit()
                msg = "Employee saved."
                if f.get("push"):
                    msg += " " + _push_to_devices(emp_id)
                flash(msg, "success")
                return redirect(url_for("main.employee_detail", emp_id=emp_id))
        emp = data | {"id": emp_id}
    return render_template("employee_form.html", emp=emp, **_lookups())


def _valid_employee_no(value):
    return bool(value) and len(value) <= 32 and value.isascii() and value.isalnum()


def _push_to_devices(emp_id, device_ids=None):
    """Create/update one employee on every enabled device; returns a flash-ready summary."""
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    payload = {"employees": [sync.employee_payload(emp)]}
    return _dispatch_all("push-employees", payload, device_ids)


def _dispatch_all(action, payload, device_ids=None):
    db = get_db()
    devices = [d for d in db.execute("SELECT * FROM devices WHERE enabled = 1").fetchall()
               if not device_ids or d["id"] in device_ids]
    if not devices:
        return "No enabled devices configured."
    return " ".join(sync.dispatch(db, dev, action, payload, g.user["username"])[1] for dev in devices)


@bp.route("/employees/<int:emp_id>")
@staff_required
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
    login = db.execute("SELECT username FROM users WHERE employee_id = ?", (emp_id,)).fetchone()
    balance = lv.balances(db, start.year, [emp_id])[emp_id]
    # calendar padding so the 1st falls on the right weekday column
    pad = start.weekday()
    return render_template("employee_detail.html", emp=emps[0], days=days, summary=summary, leaves=leaves,
                           month=start.strftime("%Y-%m"), month_label=start.strftime("%B %Y"), pad=pad,
                           weekdays=WEEKDAYS, devices=_lookups()["devices"], login=login, balance=balance,
                           year=start.year)


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
    payload = {"employee_no": emp["employee_no"], "name": emp["name"], "gender": emp["gender"],
               "image_b64": base64.b64encode(data).decode()}
    flash(_dispatch_all("upload-face", payload), "info")
    return redirect(url_for("main.employee_detail", emp_id=emp_id))


@bp.route("/employees/<int:emp_id>/delete", methods=["POST"])
@admin_required
def employee_delete(emp_id):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    if not emp:
        return redirect(url_for("main.employees"))
    if request.form.get("from_devices"):
        flash(_dispatch_all("delete-employee", {"employee_no": emp["employee_no"]}), "info")
    if request.form.get("keep_history"):
        db.execute("UPDATE employees SET active = 0 WHERE id = ?", (emp_id,))
        text = f"{emp['name']} deactivated."
    else:
        db.execute("DELETE FROM punches WHERE employee_no = ?", (emp["employee_no"],))
        db.execute("DELETE FROM employees WHERE id = ?", (emp_id,))
        text = f"{emp['name']} deleted."
    _audit("employee.deactivate" if request.form.get("keep_history") else "employee.delete",
           f"#{emp['employee_no']} {emp['name']}")
    db.commit()
    flash(text, "success")
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
    raw = file.read()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):  # Excel on Windows saves CSV as cp1252
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except csv.Error as exc:
        flash(f"Could not read the CSV file: {exc}", "error")
        return redirect(url_for("main.employees"))
    added = updated = 0
    skipped = []
    for line, row in enumerate(rows, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if isinstance(v, str) or v is None}
        no, name = row.get("employee_no"), row.get("name")
        if not no and not name:
            continue
        if not _valid_employee_no(no or "") or not name:
            skipped.append(str(line))
            continue
        if row.get("join_date") and not att.parse_date(row["join_date"]):
            row["join_date"] = ""
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
    _audit("employee.import", f"{added} new, {updated} updated")
    db.commit()
    msg = f"Imported {added} new and updated {updated} employees."
    if skipped:
        msg += f" Skipped rows {', '.join(skipped[:20])} (missing name or invalid employee number)."
    flash(msg, "warning" if skipped else "success")
    return redirect(url_for("main.employees"))


@bp.route("/employees/export.csv")
@staff_required
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
@staff_required
def devices():
    db = get_db()
    rows = db.execute("SELECT d.*, st.name AS site, (SELECT COUNT(*) FROM punches p WHERE p.device_id = d.id) "
                      "AS punch_count FROM devices d LEFT JOIN sites st ON st.id = d.site_id ORDER BY d.name").fetchall()
    jobs = db.execute("SELECT j.*, d.name AS device FROM device_jobs j JOIN devices d ON d.id = j.device_id "
                      "ORDER BY j.id DESC LIMIT 15").fetchall()
    return render_template("devices.html", devices=rows, jobs=jobs,
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
            "connection": "bridge" if f.get("connection") == "bridge" else "direct",
            "site_id": _int(f.get("site_id")),
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
            _audit("device.save", f"{data['name']} ({data['host']}, {data['connection']})")
            db.commit()
            flash("Device saved. Use “Test” to verify the connection.", "success")
            return redirect(url_for("main.devices"))
        dev = data
    return render_template("device_form.html", dev=dev, sites=_lookups()["sites"])


@bp.route("/devices/<int:dev_id>/delete", methods=["POST"])
@admin_required
def device_delete(dev_id):
    db = get_db()
    dev = db.execute("SELECT name FROM devices WHERE id = ?", (dev_id,)).fetchone()
    db.execute("DELETE FROM devices WHERE id = ?", (dev_id,))
    _audit("device.delete", dev["name"] if dev else str(dev_id))
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
    payload = None
    if action == "sync-events":
        start = att.parse_date(request.form.get("start"))
        end = att.parse_date(request.form.get("end"))
        if start and end:
            payload = {"start": f"{start} 00:00:00", "end": f"{end} 23:59:59"}
    elif action == "push-users":
        action = "push-employees"
        payload = {"employees": [sync.employee_payload(e)
                                 for e in db.execute("SELECT * FROM employees WHERE active = 1")]}
    elif action == "configure-push":
        ip = request.form.get("server_ip", "").strip()
        if not ip:
            flash("Enter the server address the terminal should send events to.", "error")
            return redirect(url_for("main.devices"))
        token = dbmod.get_setting(db, "push_token")
        payload = {"ip": ip, "port": _int(request.form.get("server_port"), 80),
                   "path": url_for("main.device_push") + (f"?token={token}" if token else "")}
    elif action not in ("test", "sync-users", "set-time", "open-door"):
        flash("Unknown action.", "error")
        return redirect(url_for("main.devices"))
    _audit(f"device.{action}", dev["name"])
    db.commit()
    category, message = sync.dispatch(db, dev, action, payload, g.user["username"])
    flash(message, category)
    return redirect(request.referrer or url_for("main.devices"))


@bp.route("/api/hik/event", methods=["POST"])
def device_push():
    """Receiver for the terminal's HTTP listening / event push feature."""
    db = get_db()
    token = dbmod.get_setting(db, "push_token")
    if token:
        if not hmac.compare_digest(request.args.get("token", "").encode(), token.encode()):
            return jsonify({"error": "bad token"}), 403
    elif not db.execute("SELECT 1 FROM devices WHERE host = ?", (request.remote_addr,)).fetchone():
        # without a token, only accept events from the address of a registered terminal
        return jsonify({"error": "unknown sender - set a push token in Settings"}), 403
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
@staff_required
def attendance_daily():
    db = get_db()
    day = att.parse_date(request.args.get("date"), date.today())
    dept = _int(request.args.get("department"))
    site = _int(request.args.get("site"))
    status = request.args.get("status", "")
    rows = att.compute(db, day, day, att.report_employees(db, day, day, department_id=dept, site_id=site))
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
    return render_template("attendance_daily.html", rows=rows, day=day, dept=dept, site=site, status=status,
                           prev=day - timedelta(days=1), next=day + timedelta(days=1), **_lookups())


@bp.route("/punches")
@staff_required
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
            _audit("punch.add", f"#{emp['employee_no']} {when:%Y-%m-%d %H:%M} - {note}")
            flash("Manual punch added.", "success")
        else:
            flash("A punch already exists at that time.", "warning")
        db.commit()
    return redirect(request.referrer or url_for("main.punches"))


@bp.route("/punches/<int:punch_id>/delete", methods=["POST"])
@admin_required
def punch_delete(punch_id):
    db = get_db()
    row = db.execute("SELECT * FROM punches WHERE id = ?", (punch_id,)).fetchone()
    db.execute("DELETE FROM punches WHERE id = ?", (punch_id,))
    if row:
        _audit("punch.delete", f"#{row['employee_no']} {row['punch_time']} ({row['source']})")
    db.commit()
    flash("Punch deleted.", "success")
    return redirect(request.referrer or url_for("main.punches"))


# ------------------------------------------------------------- reports ----
@bp.route("/reports/monthly")
@staff_required
def report_monthly():
    db = get_db()
    start, end = _month_range(request.args.get("month"))
    dept = _int(request.args.get("department"))
    site = _int(request.args.get("site"))
    view = request.args.get("view", "muster")
    rows = att.compute(db, start, end, att.report_employees(db, start, end, department_id=dept, site_id=site))
    days = list(att.daterange(start, end))
    export = request.args.get("export")
    if export == "xlsx":
        from .excel import monthly_workbook
        data = monthly_workbook(rows, days, start.strftime("%B %Y"), dbmod.get_setting(db, "company_name"))
        return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=attendance-{start:%Y-%m}.xlsx"})
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
                     "Leave", "Unpaid Leave", "Holiday", "Weekly Off", "Late Days", "Early Exits", "Work Hours",
                     "OT Hours", "Payable Days"],
                    [[e["employee_no"], e["name"], e["department"] or "", s.present, s.absent, s.half_day,
                      s.missing_punch, s.leave, s.unpaid_leave, s.holiday, s.weekly_off, s.late, s.early,
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
                           month_label=start.strftime("%B %Y"), dept=dept, site=site, view=view, weekdays=WEEKDAYS,
                           **_lookups())


# -------------------------------------------------------------- leaves ----
@bp.route("/leaves", methods=["GET", "POST"])
@staff_required
def leaves():
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can add leave.", "error")
            return redirect(url_for("main.leaves"))
        emp_id = _int(request.form.get("employee_id"))
        start = att.parse_date(request.form.get("start_date"))
        end = att.parse_date(request.form.get("end_date")) or start
        leave_type = request.form.get("leave_type") or "Casual"
        half_day = 1 if request.form.get("half_day") else 0
        emp = db.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone() if emp_id else None
        if not emp or not start or end < start:
            flash("Select an employee and a valid date range.", "error")
        elif leave_type not in {t["name"] for t in lv.leave_types(db)}:
            flash("Choose a leave type from the list.", "error")
        elif half_day and start != end:
            flash("A half-day leave must be for a single date.", "error")
        else:
            problem = lv.check_balance(db, emp_id, leave_type, start, end, half_day)
            db.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, half_day, reason, status, "
                       "requested_by, decided_by, decided_at) VALUES (?, ?, ?, ?, ?, ?, 'approved', ?, ?, "
                       "datetime('now','localtime'))",
                       (emp_id, start.isoformat(), end.isoformat(), leave_type, half_day,
                        request.form.get("reason", "").strip() or None, g.user["username"], g.user["username"]))
            _audit("leave.add", f"#{emp['employee_no']} {emp['name']}: {leave_type} {start} to {end}"
                                f"{' (half day)' if half_day else ''}")
            db.commit()
            if problem:
                flash(f"Leave recorded, but note: {problem}", "warning")
            else:
                flash("Leave recorded.", "success")
        return redirect(url_for("main.leaves"))
    year = _int(request.args.get("year"), date.today().year)
    status = request.args.get("status", "")
    sql = ("SELECT l.*, e.name, e.employee_no FROM leaves l JOIN employees e ON e.id = l.employee_id "
           "WHERE l.start_date <= ? AND l.end_date >= ?")
    args = [f"{year}-12-31", f"{year}-01-01"]
    if status in ("pending", "approved", "rejected"):
        sql += " AND l.status = ?"
        args.append(status)
    rows = db.execute(sql + " ORDER BY l.start_date DESC LIMIT 500", args).fetchall()
    employees = db.execute("SELECT id, employee_no, name FROM employees WHERE active = 1 ORDER BY name").fetchall()
    types = lv.leave_types(db)
    return render_template("leaves.html", rows=rows, employees=employees, today=date.today(), types=types,
                           year=year, status=status, balances=lv.balances(db, year, [e["id"] for e in employees]))


@bp.route("/leaves/<int:leave_id>/delete", methods=["POST"])
@admin_required
def leave_delete(leave_id):
    db = get_db()
    row = db.execute("SELECT l.*, e.employee_no FROM leaves l JOIN employees e ON e.id = l.employee_id "
                     "WHERE l.id = ?", (leave_id,)).fetchone()
    db.execute("DELETE FROM leaves WHERE id = ?", (leave_id,))
    if row:
        _audit("leave.delete", f"#{row['employee_no']} {row['leave_type']} {row['start_date']} to {row['end_date']}")
    db.commit()
    flash("Leave removed.", "success")
    return redirect(request.referrer or url_for("main.leaves"))


@bp.route("/leave-types", methods=["POST"])
@admin_required
def leave_type_save():
    db = get_db()
    name = request.form.get("name", "").strip()[:40]
    quota = _float(request.form.get("yearly_quota"), -1)
    paid = 1 if request.form.get("paid") else 0
    if not name or not 0 <= quota <= 366:
        flash("Enter a leave type name and a yearly quota from 0 (no limit) to 366.", "error")
    else:
        db.execute("INSERT INTO leave_types (name, yearly_quota, paid) VALUES (?, ?, ?) "
                   "ON CONFLICT(name) DO UPDATE SET yearly_quota = excluded.yearly_quota, paid = excluded.paid",
                   (name, quota, paid))
        _audit("leave_type.save", f"{name}: quota {quota:g}, {'paid' if paid else 'unpaid'}")
        db.commit()
        flash(f"Leave type {name} saved.", "success")
    return redirect(url_for("main.leaves"))


@bp.route("/leave-types/<int:type_id>/delete", methods=["POST"])
@admin_required
def leave_type_delete(type_id):
    db = get_db()
    t = db.execute("SELECT * FROM leave_types WHERE id = ?", (type_id,)).fetchone()
    if t and db.execute("SELECT 1 FROM leaves WHERE leave_type = ? LIMIT 1", (t["name"],)).fetchone():
        flash(f"{t['name']} is used by existing leave records and cannot be deleted. Set its quota instead.", "error")
    elif t:
        db.execute("DELETE FROM leave_types WHERE id = ?", (type_id,))
        _audit("leave_type.delete", t["name"])
        db.commit()
        flash(f"Leave type {t['name']} deleted.", "success")
    return redirect(url_for("main.leaves"))


# ------------------------------------------------------------ requests ----
@bp.route("/requests")
@staff_required
def requests_page():
    db = get_db()
    leave_rows = db.execute("SELECT l.*, e.name, e.employee_no FROM leaves l JOIN employees e ON e.id = l.employee_id "
                            "WHERE l.status = 'pending' ORDER BY l.start_date").fetchall()
    punch_rows = db.execute("SELECT r.*, e.name, e.employee_no FROM punch_requests r "
                            "JOIN employees e ON e.id = r.employee_id WHERE r.status = 'pending' "
                            "ORDER BY r.punch_time").fetchall()
    recent = db.execute(
        "SELECT 'Leave' AS kind, e.name, l.leave_type || ' ' || l.start_date || CASE WHEN l.end_date != l.start_date "
        "THEN ' to ' || l.end_date ELSE '' END AS what, l.status, l.decided_by, l.decided_at, l.decision_note "
        "FROM leaves l JOIN employees e ON e.id = l.employee_id WHERE l.requested_by IS NOT NULL "
        "AND l.status != 'pending' AND l.decided_at IS NOT NULL AND l.requested_by != l.decided_by "
        "UNION ALL SELECT 'Correction', e.name, r.punch_time, r.status, r.decided_by, r.decided_at, r.decision_note "
        "FROM punch_requests r JOIN employees e ON e.id = r.employee_id WHERE r.status != 'pending' "
        "ORDER BY decided_at DESC LIMIT 20").fetchall()
    year = date.today().year
    ids = sorted({r["employee_id"] for r in leave_rows})
    return render_template("requests.html", leave_rows=leave_rows, punch_rows=punch_rows, recent=recent,
                           balances=lv.balances(db, year, ids), year=year)


@bp.route("/requests/<kind>/<int:req_id>/<decision>", methods=["POST"])
@admin_required
def request_decide(kind, req_id, decision):
    db = get_db()
    if decision not in ("approve", "reject") or kind not in ("leave", "punch"):
        flash("Unknown request action.", "error")
        return redirect(url_for("main.requests_page"))
    status = "approved" if decision == "approve" else "rejected"
    note = request.form.get("note", "").strip()[:500] or None
    who = g.user["username"]
    if kind == "leave":
        row = db.execute("SELECT l.*, e.employee_no, e.name FROM leaves l JOIN employees e ON e.id = l.employee_id "
                         "WHERE l.id = ? AND l.status = 'pending'", (req_id,)).fetchone()
        if not row:
            flash("That request was already handled.", "warning")
            return redirect(url_for("main.requests_page"))
        db.execute("UPDATE leaves SET status = ?, decided_by = ?, decided_at = datetime('now','localtime'), "
                   "decision_note = ? WHERE id = ?", (status, who, note, req_id))
        _audit(f"leave.{status}", f"#{row['employee_no']} {row['name']}: {row['leave_type']} "
                                  f"{row['start_date']} to {row['end_date']}")
    else:
        row = db.execute("SELECT r.*, e.employee_no, e.name FROM punch_requests r "
                         "JOIN employees e ON e.id = r.employee_id WHERE r.id = ? AND r.status = 'pending'",
                         (req_id,)).fetchone()
        if not row:
            flash("That request was already handled.", "warning")
            return redirect(url_for("main.requests_page"))
        db.execute("UPDATE punch_requests SET status = ?, decided_by = ?, decided_at = datetime('now','localtime'), "
                   "decision_note = ? WHERE id = ?", (status, who, note, req_id))
        if status == "approved":
            sync.record_punch(db, row["employee_no"], row["punch_time"], source="manual",
                              note=f"Correction request: {row['reason'] or ''} (approved by {who})")
        _audit(f"punch.{status}", f"#{row['employee_no']} {row['name']}: {row['punch_time']}")
    db.commit()
    flash(f"Request {status}.", "success")
    return redirect(url_for("main.requests_page"))


# ---------------------------------------------------------------- audit ---
@bp.route("/audit")
@admin_required
def audit_log():
    db = get_db()
    q = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM audit_log", []
    if q:
        sql += " WHERE username LIKE ? OR action LIKE ? OR details LIKE ?"
        args = [f"%{q}%"] * 3
    rows = db.execute(sql + " ORDER BY id DESC LIMIT 500", args).fetchall()
    if request.args.get("export"):
        return _csv("audit-log.csv", ["Time", "User", "Action", "Details"],
                    [[r["at"], r["username"] or "", r["action"], r["details"] or ""] for r in rows])
    return render_template("audit.html", rows=rows, q=q)


# ---------------------------------------------------- holidays & setup ----
@bp.route("/holidays", methods=["GET", "POST"])
@staff_required
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
            _audit("holiday.save", f"{day} {name}")
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
    row = db.execute("SELECT * FROM holidays WHERE id = ?", (hid,)).fetchone()
    db.execute("DELETE FROM holidays WHERE id = ?", (hid,))
    if row:
        _audit("holiday.delete", f"{row['date']} {row['name']}")
    db.commit()
    return redirect(url_for("main.holidays"))


@bp.route("/shifts", methods=["GET", "POST"])
@staff_required
def shifts():
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can edit shifts.", "error")
            return redirect(url_for("main.shifts"))
        f = request.form
        sid = _int(f.get("id"))
        start_hm, end_hm = att.parse_hm(f.get("start_time")), att.parse_hm(f.get("end_time"))
        data = {
            "name": f.get("name", "").strip(),
            "start_time": "%02d:%02d" % start_hm if start_hm else None,
            "end_time": "%02d:%02d" % end_hm if end_hm else None,
            "grace_in_min": max(0, _int(f.get("grace_in_min"), 0)),
            "grace_out_min": max(0, _int(f.get("grace_out_min"), 0)),
            "break_minutes": max(0, _int(f.get("break_minutes"), 0)),
            "half_day_hours": _float(f.get("half_day_hours"), 0),
            "full_day_hours": _float(f.get("full_day_hours"), 0),
            "weekly_off": ",".join(sorted({w for w in f.getlist("weekly_off") if w in "0123456"})),
        }
        if not data["name"]:
            flash("Shift name is required.", "error")
        elif not start_hm or not end_hm or start_hm == end_hm:
            flash("Enter valid, different start and end times (HH:MM).", "error")
        elif not (0 <= data["half_day_hours"] <= data["full_day_hours"] <= 24):
            flash("Half-day hours must not exceed full-day hours, and both must be between 0 and 24.", "error")
        else:
            cols = list(data)
            try:
                if sid:
                    db.execute(f"UPDATE shifts SET {', '.join(c + ' = ?' for c in cols)} WHERE id = ?",
                               [data[c] for c in cols] + [sid])
                else:
                    db.execute(f"INSERT INTO shifts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [data[c] for c in cols])
                _audit("shift.save", f"{data['name']} {data['start_time']}-{data['end_time']}")
                db.commit()
                flash("Shift saved.", "success")
            except sqlite3.IntegrityError:
                db.rollback()
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
    row = db.execute("SELECT name FROM shifts WHERE id = ?", (sid,)).fetchone()
    db.execute("DELETE FROM shifts WHERE id = ?", (sid,))
    if row:
        _audit("shift.delete", row["name"])
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
        _audit("department.add", name)
        db.commit()
    return redirect(url_for("main.settings"))


@bp.route("/departments/<int:did>/delete", methods=["POST"])
@admin_required
def department_delete(did):
    db = get_db()
    db.execute("DELETE FROM departments WHERE id = ?", (did,))
    _audit("department.delete", str(did))
    db.commit()
    return redirect(url_for("main.settings"))


@bp.route("/bulk-shift", methods=["POST"])
@admin_required
def bulk_shift():
    db = get_db()
    ids = [int(x) for x in request.form.getlist("employee_id") if x.isdigit()]
    shift_id = _int(request.form.get("shift_id"))
    dept_id = _int(request.form.get("department_id"))
    site_id = _int(request.form.get("site_id"))
    if ids and (shift_id or dept_id or site_id):
        marks = ",".join("?" * len(ids))
        if shift_id:
            db.execute(f"UPDATE employees SET shift_id = ? WHERE id IN ({marks})", [shift_id, *ids])
        if dept_id:
            db.execute(f"UPDATE employees SET department_id = ? WHERE id IN ({marks})", [dept_id, *ids])
        if site_id:
            db.execute(f"UPDATE employees SET site_id = ? WHERE id IN ({marks})", [site_id, *ids])
        _audit("employee.bulk", f"{len(ids)} employees: shift={shift_id or '-'} department={dept_id or '-'} "
                                f"office={site_id or '-'}")
        db.commit()
        flash(f"Updated {len(ids)} employees.", "success")
    else:
        flash("Select employees and a shift, department or office.", "error")
    return redirect(request.referrer or url_for("main.employees"))


NUMERIC_SETTINGS = {
    "sync_interval_min": (0, 1440, "Auto-sync interval"),
    "initial_sync_days": (1, 366, "First sync look-back"),
    "window_before_hours": (0, 12, "Hours before shift"),
    "ot_min_minutes": (0, 600, "Minimum overtime"),
}


ROLES = {"admin": "Admin", "viewer": "Viewer (read-only)", "employee": "Employee (self-service)"}


def _admin_count(db, excluding=None):
    return db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND id != ?", (excluding or 0,)).fetchone()[0]


def _create_user(db, username, password, role, employee_id=None):
    """Validate and create a login; returns an error message or None."""
    if not username or len(username) > 40 or len(password) < 6:
        return "Username (max 40 characters) and a password of at least 6 characters are required."
    if role not in ROLES:
        return "Choose a role."
    if role == "employee":
        if not employee_id or not db.execute("SELECT 1 FROM employees WHERE id = ?", (employee_id,)).fetchone():
            return "Choose the employee this self-service login belongs to."
        if db.execute("SELECT 1 FROM users WHERE employee_id = ?", (employee_id,)).fetchone():
            return "That employee already has a login."
    if db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        return "That username is taken."
    db.execute("INSERT INTO users (username, password_hash, role, employee_id) VALUES (?, ?, ?, ?)",
               (username, generate_password_hash(password), role, employee_id if role == "employee" else None))
    _audit("user.create", f"{username} ({role})")
    db.commit()
    return None


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
                _audit("user.password", "changed own password")
                db.commit()
                flash("Password changed.", "success")
        elif g.user["role"] != "admin":
            flash("Only administrators can change settings.", "error")
        elif form == "general":
            errors = []
            for key, (lo, hi, label) in NUMERIC_SETTINGS.items():
                value = _float(request.form.get(key))
                if key in request.form and (value is None or not lo <= value <= hi):
                    errors.append(f"{label} must be a number from {lo:g} to {hi:g}.")
            minors = request.form.get("event_minors", "0").replace(" ", "")
            if not minors or not all(x.isdigit() for x in minors.split(",")):
                errors.append("Event minor codes must be numbers separated by commas, e.g. 75,1,38 (or 0).")
            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                for key in ("company_name", "sync_interval_min", "initial_sync_days", "event_minors",
                            "window_before_hours", "ot_min_minutes", "push_token"):
                    if key in request.form:
                        value = request.form[key].strip()
                        if key in NUMERIC_SETTINGS:
                            value = "%g" % float(value)
                        dbmod.set_setting(db, key, value)
                dbmod.set_setting(db, "require_2fa_admins", "1" if request.form.get("require_2fa_admins") else "0")
                if request.form.get("default_language") in ("en", "hi"):
                    dbmod.set_setting(db, "default_language", request.form["default_language"])
                _audit("settings.general", "saved")
                db.commit()
                flash("Settings saved.", "success")
        elif form == "email":
            port = _int(request.form.get("smtp_port"))
            hm = att.parse_hm(request.form.get("report_time"))
            recipients = [r.strip() for r in request.form.get("report_recipients", "").split(",") if r.strip()]
            if request.form.get("smtp_host") and not (port and 0 < port < 65536):
                flash("Enter a valid SMTP port (usually 587, or 465 for SSL).", "error")
            elif not hm:
                flash("Enter the daily report time as HH:MM.", "error")
            elif any("@" not in r for r in recipients):
                flash("Recipients must be email addresses separated by commas.", "error")
            else:
                for key in ("smtp_host", "smtp_user", "smtp_from"):
                    dbmod.set_setting(db, key, request.form.get(key, "").strip())
                dbmod.set_setting(db, "smtp_port", str(port or 587))
                dbmod.set_setting(db, "smtp_security", request.form.get("smtp_security") if
                                  request.form.get("smtp_security") in ("starttls", "ssl", "none") else "starttls")
                dbmod.set_setting(db, "report_recipients", ", ".join(recipients))
                dbmod.set_setting(db, "report_time", "%02d:%02d" % hm)
                if request.form.get("smtp_password"):  # blank keeps the saved password
                    dbmod.set_setting(db, "smtp_password", request.form["smtp_password"])
                _audit("settings.email", f"server {request.form.get('smtp_host', '')}, {len(recipients)} recipients")
                db.commit()
                flash("Email settings saved.", "success")
        elif form == "send_report":
            from . import notify
            try:
                sent_to = notify.send_daily_report(db, force=True)
                flash(f"Daily summary sent to {', '.join(sent_to)}.", "success")
            except notify.NotifyError as exc:
                flash(str(exc), "error")
        elif form == "user":
            error = _create_user(db, request.form.get("username", "").strip(), request.form.get("password", ""),
                                 request.form.get("role"), _int(request.form.get("employee_id")))
            flash(error or "User created.", "error" if error else "success")
        elif form == "reset_2fa":
            target = db.execute("SELECT * FROM users WHERE id = ?", (_int(request.form.get("user_id")),)).fetchone()
            if target:
                db.execute("UPDATE users SET totp_enabled = 0, totp_secret = NULL, recovery_codes = NULL, "
                           "totp_last_step = NULL WHERE id = ?", (target["id"],))
                _audit("user.2fa_reset", target["username"])
                db.commit()
                flash(f"Two-factor sign-in turned off for {target['username']}. They can set it up again.", "success")
        elif form in ("set_role", "reset_password", "delete_user"):
            uid = _int(request.form.get("user_id"))
            target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if not target:
                flash("User not found.", "error")
            elif form == "reset_password":
                new = request.form.get("new_password", "")
                if len(new) < 6:
                    flash("The new password must be at least 6 characters.", "error")
                else:
                    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), uid))
                    db.execute("DELETE FROM login_attempts WHERE username = ?", (target["username"],))
                    _audit("user.reset_password", target["username"])
                    db.commit()
                    flash(f"Password for {target['username']} reset.", "success")
            elif target["role"] == "admin" and _admin_count(db, excluding=uid) == 0:
                flash("At least one administrator must remain.", "error")
            elif form == "set_role":
                role = request.form.get("role")
                if role not in ("admin", "viewer"):
                    flash("Choose Admin or Viewer.", "error")
                elif target["role"] == "employee":
                    flash("Employee logins stay linked to their employee; delete and recreate to change.", "error")
                else:
                    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
                    _audit("user.role", f"{target['username']} -> {role}")
                    db.commit()
                    flash(f"{target['username']} is now {ROLES[role]}.", "success")
            elif uid == g.user["id"]:
                flash("You cannot delete yourself.", "error")
            else:
                db.execute("DELETE FROM users WHERE id = ?", (uid,))
                _audit("user.delete", target["username"])
                db.commit()
                flash("User deleted.", "success")
        elif form == "mobile":
            raw = request.form.get("android_assetlinks", "").strip()
            try:
                if raw and not isinstance(json.loads(raw), list):
                    raise ValueError
                dbmod.set_setting(db, "android_assetlinks", raw)
                _audit("settings.mobile", "asset links updated")
                db.commit()
                flash("Mobile app settings saved.", "success")
            except ValueError:
                flash("Paste the assetlinks.json content exactly as given (a JSON list starting with [).", "error")
        elif form == "regen_bridge":
            dbmod.set_setting(db, "bridge_token", secrets.token_urlsafe(24))
            _audit("settings.bridge_token", "regenerated")
            db.commit()
            flash("New bridge token created. Update bridge.ini on the bridge PC, then restart the bridge.", "warning")
        return redirect(url_for("main.settings"))
    return render_template("settings.html", settings=dbmod.get_settings(db), roles=ROLES,
                           schema_version=db.execute("PRAGMA user_version").fetchone()[0],
                           server_url=request.host_url.rstrip("/"),
                           users=db.execute("SELECT u.id, u.username, u.role, u.created_at, u.totp_enabled, "
                                            "e.name AS employee_name, "
                                            "e.employee_no FROM users u LEFT JOIN employees e ON e.id = u.employee_id "
                                            "ORDER BY u.role, u.username").fetchall(),
                           employees_without_login=db.execute(
                               "SELECT id, employee_no, name FROM employees WHERE active = 1 AND id NOT IN "
                               "(SELECT employee_id FROM users WHERE employee_id IS NOT NULL) ORDER BY name").fetchall(),
                           departments=db.execute(
                               "SELECT d.*, (SELECT COUNT(*) FROM employees e WHERE e.department_id = d.id) AS n "
                               "FROM departments d ORDER BY name").fetchall())


@bp.route("/employees/<int:emp_id>/login", methods=["POST"])
@admin_required
def employee_login_create(emp_id):
    db = get_db()
    error = _create_user(db, request.form.get("username", "").strip(), request.form.get("password", ""),
                         "employee", emp_id)
    flash(error or "Self-service login created. Give the employee their username and password.",
          "error" if error else "success")
    return redirect(url_for("main.employee_detail", emp_id=emp_id))


@bp.route("/settings/backup")
@admin_required
def download_backup():
    """Take a fresh database backup and download it."""
    path = dbmod.backup(current_app.config["DATABASE"], current_app.config["BACKUP_DIR"])
    return send_file(path, as_attachment=True, download_name=path.name)


# ---------------------------------------------------------------- roster ---
def _week_start(value):
    d = att.parse_date(value, date.today())
    return d - timedelta(days=d.weekday())


@bp.route("/roster", methods=["GET", "POST"])
@staff_required
def roster():
    """Weekly shift roster: per-day shift overrides on top of each employee's default shift."""
    db = get_db()
    week = _week_start(request.values.get("week"))
    days = [week + timedelta(days=i) for i in range(7)]
    dept = _int(request.values.get("department"))
    site = _int(request.values.get("site"))
    employees = att.load_employees(db, department_id=dept, site_id=site)
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can change the roster.", "error")
            return redirect(url_for("main.roster", week=week, department=dept or "", site=site or ""))
        shift_ids = {str(r["id"]) for r in db.execute("SELECT id FROM shifts")}
        changed = 0
        for e in employees:
            for d in days:
                key = f"r_{e['id']}_{d.isoformat()}"
                if key not in request.form:
                    continue
                value = request.form[key]
                if value == "":
                    changed += db.execute("DELETE FROM roster WHERE employee_id = ? AND date = ?",
                                          (e["id"], d.isoformat())).rowcount
                elif value == "off" or value in shift_ids:
                    db.execute("INSERT INTO roster (employee_id, date, shift_id) VALUES (?, ?, ?) "
                               "ON CONFLICT(employee_id, date) DO UPDATE SET shift_id = excluded.shift_id",
                               (e["id"], d.isoformat(), None if value == "off" else int(value)))
                    changed += 1
        _audit("roster.save", f"week of {week}: {changed} cell(s)")
        db.commit()
        flash(f"Roster saved for the week of {week:%d %b %Y}.", "success")
        return redirect(url_for("main.roster", week=week, department=dept or "", site=site or ""))
    cells = {}
    if employees:
        ids = [e["id"] for e in employees]
        for r in db.execute(f"SELECT * FROM roster WHERE date BETWEEN ? AND ? AND employee_id IN "
                            f"({','.join('?' * len(ids))})", [days[0].isoformat(), days[-1].isoformat(), *ids]):
            cells[(r["employee_id"], r["date"])] = "off" if r["shift_id"] is None else str(r["shift_id"])
    return render_template("roster.html", week=week, days=days, employees=employees, cells=cells, dept=dept,
                           site=site, prev=week - timedelta(days=7), next=week + timedelta(days=7),
                           weekdays=WEEKDAYS, **_lookups())


@bp.route("/roster/rotate", methods=["POST"])
@admin_required
def roster_rotate():
    """Fill the roster with a repeating weekly pattern, e.g. Morning, Evening, Night."""
    db = get_db()
    start = _week_start(request.form.get("start"))
    weeks = max(1, min(_int(request.form.get("weeks"), 4), 26))
    pattern = [_int(x) for x in request.form.getlist("pattern") if x]
    pattern = [p for p in pattern if p and db.execute("SELECT 1 FROM shifts WHERE id = ?", (p,)).fetchone()]
    ids = [int(x) for x in request.form.getlist("employee_id") if x.isdigit()]
    stagger = bool(request.form.get("stagger"))
    if not pattern or not ids:
        flash("Pick at least one employee and one shift for the rotation.", "error")
        return redirect(url_for("main.roster", week=start))
    shifts = {r["id"]: r for r in db.execute("SELECT * FROM shifts")}
    written = 0
    for n, emp_id in enumerate(ids):
        offset = n if stagger else 0  # stagger: employees start at different points of the pattern
        for w in range(weeks):
            shift = shifts[pattern[(w + offset) % len(pattern)]]
            offs = {x for x in (shift["weekly_off"] or "").split(",") if x}
            for i in range(7):
                d = start + timedelta(days=7 * w + i)
                db.execute("INSERT INTO roster (employee_id, date, shift_id) VALUES (?, ?, ?) "
                           "ON CONFLICT(employee_id, date) DO UPDATE SET shift_id = excluded.shift_id",
                           (emp_id, d.isoformat(), None if str(i) in offs else shift["id"]))
                written += 1
    _audit("roster.rotate", f"{len(ids)} employee(s), {weeks} week(s) from {start}: "
                            + " > ".join(shifts[p]["name"] for p in pattern))
    db.commit()
    flash(f"Rotation applied: {len(ids)} employee(s) for {weeks} week(s) from {start:%d %b %Y}.", "success")
    return redirect(url_for("main.roster", week=start))


@bp.route("/roster/copy", methods=["POST"])
@admin_required
def roster_copy():
    """Copy the previous week's roster into this week."""
    db = get_db()
    week = _week_start(request.form.get("week"))
    src = week - timedelta(days=7)
    rows = db.execute("SELECT employee_id, date, shift_id FROM roster WHERE date BETWEEN ? AND ?",
                      (src.isoformat(), (src + timedelta(days=6)).isoformat())).fetchall()
    for r in rows:
        d = att.parse_date(r["date"]) + timedelta(days=7)
        db.execute("INSERT INTO roster (employee_id, date, shift_id) VALUES (?, ?, ?) "
                   "ON CONFLICT(employee_id, date) DO UPDATE SET shift_id = excluded.shift_id",
                   (r["employee_id"], d.isoformat(), r["shift_id"]))
    _audit("roster.copy", f"{src} -> {week}: {len(rows)} cell(s)")
    db.commit()
    flash(f"Copied {len(rows)} roster entries from the previous week.", "success" if rows else "warning")
    return redirect(url_for("main.roster", week=week))


# ---------------------------------------------------------------- offices --
@bp.route("/sites", methods=["GET", "POST"])
@staff_required
def sites():
    """Offices / locations. Each can have its own terminals, bridge PC and reports."""
    db = get_db()
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can change offices.", "error")
            return redirect(url_for("main.sites"))
        sid = _int(request.form.get("id"))
        name = request.form.get("name", "").strip()[:80]
        address = request.form.get("address", "").strip()[:200] or None
        if not name:
            flash("Office name is required.", "error")
        else:
            try:
                if sid:
                    db.execute("UPDATE sites SET name = ?, address = ? WHERE id = ?", (name, address, sid))
                else:
                    db.execute("INSERT INTO sites (name, address) VALUES (?, ?)", (name, address))
                _audit("site.save", name)
                db.commit()
                flash(f"Office {name} saved.", "success")
            except sqlite3.IntegrityError:
                db.rollback()
                flash("An office with that name already exists.", "error")
        return redirect(url_for("main.sites"))
    rows = db.execute(
        "SELECT st.*, (SELECT COUNT(*) FROM employees e WHERE e.site_id = st.id AND e.active = 1) AS n_emp, "
        "(SELECT COUNT(*) FROM devices d WHERE d.site_id = st.id) AS n_dev FROM sites st ORDER BY name").fetchall()
    unassigned = db.execute("SELECT COUNT(*) FROM employees WHERE site_id IS NULL AND active = 1").fetchone()[0]
    return render_template("sites.html", rows=rows, unassigned=unassigned, server_url=request.host_url.rstrip("/"),
                           edit=db.execute("SELECT * FROM sites WHERE id = ?",
                                           (_int(request.args.get("edit")),)).fetchone())


@bp.route("/sites/<int:sid>/token", methods=["POST"])
@admin_required
def site_token(sid):
    db = get_db()
    site = db.execute("SELECT name FROM sites WHERE id = ?", (sid,)).fetchone()
    if site:
        remove = request.form.get("remove")
        db.execute("UPDATE sites SET bridge_token = ? WHERE id = ?", (None if remove else secrets.token_urlsafe(24), sid))
        _audit("site.bridge_token", f"{site['name']}: {'removed' if remove else 'new token'}")
        db.commit()
        flash(f"Bridge token for {site['name']} {'removed' if remove else 'created - put it in that office’s bridge.ini'}.",
              "warning")
    return redirect(url_for("main.sites"))


@bp.route("/sites/<int:sid>/delete", methods=["POST"])
@admin_required
def site_delete(sid):
    db = get_db()
    site = db.execute("SELECT name FROM sites WHERE id = ?", (sid,)).fetchone()
    if site:
        db.execute("DELETE FROM sites WHERE id = ?", (sid,))
        _audit("site.delete", site["name"])
        db.commit()
        flash(f"Office {site['name']} deleted. Its employees and devices are kept without an office.", "success")
    return redirect(url_for("main.sites"))
