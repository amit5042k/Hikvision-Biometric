import os
from datetime import datetime
import secrets
from functools import wraps
from pathlib import Path

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

    from . import bridge_api, routes
    app.register_blueprint(routes.bp)
    app.register_blueprint(bridge_api.bp)

    app.jinja_env.filters["hm"] = fmt_minutes
    app.jinja_env.filters["dayname"] = lambda iso: datetime.strptime(iso, "%Y-%m-%d").strftime("%A")
    app.jinja_env.globals["STATUS_LABELS"] = STATUS_LABELS
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        if "user_id" in session:
            g.user = dbmod.get_db().execute("SELECT id, username, role FROM users WHERE id = ?",
                                            (session["user_id"],)).fetchone()
        # /api/ endpoints are called by devices and the bridge agent and use tokens instead
        if request.method == "POST" and not request.path.startswith("/api/"):
            expected = session.get("csrf_token")
            if not app.config.get("TESTING_NO_CSRF") and (not expected or request.form.get("csrf_token") != expected):
                abort(400, "Invalid or missing CSRF token - reload the page and try again.")

    @app.context_processor
    def inject_globals():
        company = dbmod.get_setting(dbmod.get_db(), "company_name")
        return {"company_name": company, "user": g.get("user")}

    register_cli(app)
    return app


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


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            flash("Only administrators can do that.", "error")
            return redirect(request.referrer or url_for("main.dashboard"))
        return view(*args, **kwargs)
    return wrapped
