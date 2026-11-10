"""IFP Classroom Attendance: face-recognition attendance from classroom panel cameras.

A standalone app with its own database, logins and settings.
"""
import os
import secrets
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, request, session, url_for

from . import db as dbmod


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    data_dir = (test_config or {}).get("DATA_DIR") or os.environ.get("CLASSROOM_DATA_DIR") or app.instance_path
    app.config.from_mapping(
        DATA_DIR=data_dir,
        DATABASE=os.environ.get("CLASSROOM_DB") or os.path.join(data_dir, "classroom.db"),
        BACKUP_DIR=os.path.join(data_dir, "backups"),
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        SESSION_COOKIE_NAME="classroom_session",
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

    from . import admin, views
    app.register_blueprint(admin.bp)
    app.register_blueprint(views.bp)

    app.jinja_env.filters["hm"] = fmt_minutes
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        if "user_id" in session:
            g.user = dbmod.get_db().execute("SELECT id, username, role, classroom_id FROM users WHERE id = ?",
                                            (session["user_id"],)).fetchone()
            if g.user is None:
                session.pop("user_id", None)
        # A classroom login sees only its own room's pages.
        if g.user is not None and g.user["role"] == "class" and request.endpoint not in CLASS_ENDPOINTS:
            if request.method == "POST":
                abort(403)
            return redirect(url_for("classroom.register"))
        # /api/ endpoints are called by the classroom panels and use the kiosk link secret instead
        if request.method == "POST" and not request.path.startswith("/api/"):
            expected = session.get("csrf_token")
            if not app.config.get("TESTING_NO_CSRF") and (not expected or request.form.get("csrf_token") != expected):
                abort(400, "Invalid or missing CSRF token - reload the page and try again.")

    @app.context_processor
    def inject_globals():
        return {"school_name": dbmod.get_setting(dbmod.get_db(), "school_name"), "user": g.get("user")}

    register_cli(app)
    return app


# Pages a classroom login may use; each one limits itself to that login's room.
CLASS_ENDPOINTS = {
    "static", "admin.login", "admin.logout", "admin.settings",
    "classroom.register", "classroom.movements", "classroom.monthly", "classroom.analytics",
    "classroom.person_detail", "classroom.panel_app",
    "classroom.kiosk", "classroom.kiosk_roster", "classroom.kiosk_sightings", "classroom.panel_login",
}


def register_cli(app):
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


def fmt_minutes(minutes):
    if not minutes:
        return ""
    return f"{minutes // 60}:{minutes % 60:02d}"


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _persistent_secret(data_dir):
    path = Path(data_dir) / "secret_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("admin.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


staff_required = login_required  # every login may view reports (a class login only its own room)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            if request.path.endswith("/faces") or request.path.endswith("/by-number"):
                return {"ok": False, "error": "Only administrators can do that."}, 403
            flash("Only administrators can do that.", "error")
            return redirect(request.referrer or url_for("classroom.overview"))
        return view(*args, **kwargs)
    return wrapped
