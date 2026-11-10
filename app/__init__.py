import os
import secrets
from datetime import datetime
from functools import wraps
from pathlib import Path

import click
from flask import Flask, abort, flash, g, redirect, request, session, url_for

from . import db as dbmod
from .attendance import STATUS_LABELS, fmt_minutes


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    # Everything that must survive code updates (database, session key, backups)
    # lives in the data directory. On a server, point ATTENDANCE_DATA_DIR at a
    # folder outside the code checkout so a redeploy can never touch it.
    data_dir = (test_config or {}).get("DATA_DIR") or os.environ.get("ATTENDANCE_DATA_DIR") or app.instance_path
    app.config.from_mapping(
        DATA_DIR=data_dir,
        DATABASE=os.environ.get("ATTENDANCE_DB") or os.path.join(data_dir, "attendance.db"),
        BACKUP_DIR=os.path.join(data_dir, "backups"),
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        MAX_FORM_PARTS=20000,  # the weekly roster grid posts one field per employee per day
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS_ONLY", "").lower() in ("1", "true", "yes"),
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or _persistent_secret(app.config["DATA_DIR"])
    if os.environ.get("BEHIND_PROXY", "").lower() in ("1", "true", "yes"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    dbmod.init_db(app.config["DATABASE"], app.config["BACKUP_DIR"])
    app.teardown_appcontext(dbmod.close_db)

    from . import bridge_api, i18n, portal, routes, twofactor
    app.register_blueprint(routes.bp)
    app.register_blueprint(i18n.bp)
    app.register_blueprint(twofactor.bp)
    app.register_blueprint(bridge_api.bp)
    app.register_blueprint(portal.bp)

    app.jinja_env.filters["hm"] = fmt_minutes
    app.jinja_env.filters["dayname"] = lambda iso: datetime.strptime(iso, "%Y-%m-%d").strftime("%A")
    app.jinja_env.globals["STATUS_LABELS"] = STATUS_LABELS
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["_"] = i18n.gettext
    app.jinja_env.globals["LANGUAGES"] = i18n.LANGUAGES

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        if "user_id" in session:
            g.user = dbmod.get_db().execute("SELECT id, username, role, employee_id, totp_enabled, language "
                                            "FROM users WHERE id = ?", (session["user_id"],)).fetchone()
            if g.user is None:
                session.pop("user_id", None)  # the account was deleted
        g.lang = i18n.current_language()
        # Admins may be required to set up two-factor sign-in before anything else.
        if g.user is not None and request.endpoint not in ("twofactor.manage", "main.logout", "static",
                                                            "i18n.set_language"):
            from .twofactor import setup_required
            if setup_required(g.user):
                flash("Administrators must turn on two-factor sign-in before continuing.", "warning")
                return redirect(url_for("twofactor.manage"))
        # Employee logins may only use their own self-service pages.
        if g.user is not None and g.user["role"] == "employee" and request.endpoint not in EMPLOYEE_ENDPOINTS:
            if request.method == "POST":
                abort(403)
            return redirect(url_for("portal.my_attendance"))
        # /api/ endpoints are called by devices and the bridge agent and use tokens instead
        if request.method == "POST" and not request.path.startswith("/api/"):
            expected = session.get("csrf_token")
            if not app.config.get("TESTING_NO_CSRF") and (not expected or request.form.get("csrf_token") != expected):
                abort(400, "Invalid or missing CSRF token - reload the page and try again.")

    @app.context_processor
    def inject_globals():
        db = dbmod.get_db()
        company = dbmod.get_setting(db, "company_name")
        user = g.get("user")
        pending = 0
        if user is not None and user["role"] in ("admin", "viewer"):
            pending = (db.execute("SELECT COUNT(*) FROM leaves WHERE status = 'pending'").fetchone()[0] +
                       db.execute("SELECT COUNT(*) FROM punch_requests WHERE status = 'pending'").fetchone()[0])
        return {"company_name": company, "user": user, "pending_requests": pending, "lang": g.get("lang", "en")}

    register_cli(app)
    return app


# Endpoints an employee (self-service) login can reach; everything else is staff-only.
EMPLOYEE_ENDPOINTS = {
    "static", "main.login", "main.logout", "main.settings",
    "portal.my_attendance", "portal.my_leave", "portal.my_leave_cancel",
    "portal.my_corrections", "portal.my_correction_cancel",
    "twofactor.manage", "twofactor.login_code", "i18n.set_language",
}


def register_cli(app):
    @app.cli.command("sync")
    def sync_command():
        """Download new punches from every enabled device once."""
        from .hikvision import HikvisionError
        from .sync import sync_events
        db = dbmod.get_db()
        for device in db.execute("SELECT * FROM devices WHERE enabled = 1 AND connection = 'direct'").fetchall():
            try:
                print(device["name"], sync_events(db, device))
            except HikvisionError as exc:
                print(device["name"], "FAILED:", exc)

    @app.cli.command("send-daily-report")
    @click.option("--force", is_flag=True, help="send now even if already sent today")
    def send_daily_report_command(force):
        """Email the daily attendance summary if it is due (run from cron every 15 minutes)."""
        from .notify import NotifyError, send_daily_report
        try:
            sent = send_daily_report(dbmod.get_db(), force=force)
            print("Sent to " + ", ".join(sent) if sent else "Not due yet (or already sent today).")
        except NotifyError as exc:
            print("FAILED:", exc)

    @app.cli.command("backup")
    def backup_command():
        """Save a copy of the database into the data directory's backups folder."""
        print("Backup written to", dbmod.backup(app.config["DATABASE"], app.config["BACKUP_DIR"]))

    @app.cli.command("reset-admin")
    def reset_admin():
        """Reset (or recreate) the 'admin' account with password 'admin'."""
        from werkzeug.security import generate_password_hash
        db = dbmod.get_db()
        db.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin', ?, 'admin') "
                   "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash, role = 'admin'",
                   (generate_password_hash("admin"),))
        db.commit()
        print("admin password reset to 'admin'")


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _persistent_secret(instance_path):
    path = Path(instance_path) / "secret_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("main.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def staff_required(view):
    """Logged in as admin or viewer (not an employee self-service login)."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] not in ("admin", "viewer"):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            flash("Only administrators can do that.", "error")
            return redirect(request.referrer or url_for("main.dashboard"))
        return view(*args, **kwargs)
    return wrapped
