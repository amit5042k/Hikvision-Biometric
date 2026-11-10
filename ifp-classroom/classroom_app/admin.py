"""Sign-in, users, school settings, holidays, audit log and backup."""
import csv
import io
from datetime import date, datetime

from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template, request, send_file, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import admin_required, login_required
from . import db as dbmod
from .db import get_db

bp = Blueprint("admin", __name__)

ROLES = {"admin": "Admin", "viewer": "Viewer"}
LOCKOUT_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _audit(action, details=""):
    dbmod.audit(get_db(), g.user["username"] if g.get("user") else None, action, details)


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@bp.route("/")
def home():
    return redirect(url_for("classroom.overview"))


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
            db.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
            dbmod.audit(db, username, "login", request.remote_addr or "")
            db.commit()
            token = session.get("csrf_token")
            session.clear()
            session["user_id"] = user["id"]
            if token:
                session["csrf_token"] = token
            if username == "admin" and request.form.get("password") == "admin":
                flash("You are using the default password. Please change it in Settings.", "warning")
            return redirect(_safe_next(request.args.get("next")))
        db.execute("INSERT INTO login_attempts (username, ip) VALUES (?, ?)", (username, request.remote_addr))
        db.commit()
        flash("Invalid username or password.", "error")
    return render_template("login.html")


def _too_many_attempts(db, username):
    since = f"-{LOCKOUT_MINUTES} minutes"
    db.execute("DELETE FROM login_attempts WHERE created_at < datetime('now', '-1 day')")
    by_user = db.execute("SELECT COUNT(*) FROM login_attempts WHERE username = ? "
                         "AND created_at >= datetime('now', ?)", (username, since)).fetchone()[0]
    by_ip = db.execute("SELECT COUNT(*) FROM login_attempts WHERE ip = ? "
                       "AND created_at >= datetime('now', ?)", (request.remote_addr, since)).fetchone()[0]
    return by_user >= LOCKOUT_ATTEMPTS or by_ip >= LOCKOUT_ATTEMPTS * 4


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("classroom.overview")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


# ------------------------------------------------------------ settings ----
def _admin_count(db, excluding=None):
    return db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND id != ?", (excluding or 0,)).fetchone()[0]


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
            name = request.form.get("school_name", "").strip()[:100]
            if name:
                dbmod.set_setting(db, "school_name", name)
                _audit("settings.general", name)
                db.commit()
                flash("Settings saved.", "success")
        elif form == "user":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role")
            if not username or len(username) > 40 or len(password) < 6:
                flash("Username (max 40 characters) and a password of at least 6 characters are required.", "error")
            elif role not in ROLES:
                flash("Choose a role.", "error")
            elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
                flash("That username is taken.", "error")
            else:
                db.execute("INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                           (username, generate_password_hash(password), role))
                _audit("user.create", f"{username} ({role})")
                db.commit()
                flash("User created.", "success")
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
                if role not in ROLES:
                    flash("Choose Admin or Viewer.", "error")
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
        return redirect(url_for("admin.settings"))
    return render_template("settings.html", settings=dbmod.get_settings(db), roles=ROLES,
                           users=db.execute("SELECT id, username, role, created_at FROM users "
                                            "ORDER BY role, username").fetchall())


@bp.route("/settings/backup")
@admin_required
def download_backup():
    path = dbmod.backup(current_app.config["DATABASE"], current_app.config["BACKUP_DIR"])
    return send_file(path, as_attachment=True, download_name=path.name)


# ------------------------------------------------------------ holidays ----
@bp.route("/holidays", methods=["GET", "POST"])
@login_required
def holidays():
    db = get_db()
    year = _int(request.args.get("year"), date.today().year)
    if request.method == "POST":
        if g.user["role"] != "admin":
            flash("Only administrators can add holidays.", "error")
            return redirect(url_for("admin.holidays", year=year))
        try:
            day = datetime.strptime(request.form.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            day = None
        name = request.form.get("name", "").strip()
        if not day or not name:
            flash("Enter a date and a name.", "error")
        else:
            db.execute("INSERT INTO holidays (date, name) VALUES (?, ?) "
                       "ON CONFLICT(date) DO UPDATE SET name = excluded.name", (day.isoformat(), name))
            _audit("holiday.add", f"{day} {name}")
            db.commit()
            flash("Holiday saved.", "success")
            year = day.year
        return redirect(url_for("admin.holidays", year=year))
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
    return redirect(request.referrer or url_for("admin.holidays"))


# --------------------------------------------------------------- audit ----
@bp.route("/audit")
@admin_required
def audit_log():
    q = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM audit_log", []
    if q:
        sql += " WHERE username LIKE ? OR action LIKE ? OR details LIKE ?"
        args = [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY id DESC LIMIT 5000", args).fetchall()
    if request.args.get("export"):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Time", "User", "Action", "Details"])
        for r in rows:
            w.writerow([r["at"], r["username"] or "", r["action"], r["details"] or ""])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=audit-log.csv"})
    return render_template("audit.html", rows=rows[:500], q=q)
