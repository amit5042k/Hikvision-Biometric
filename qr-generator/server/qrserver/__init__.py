"""QR Generator server: accounts with a free trial, dynamic QR codes and an admin page.

Every QR code points at /r/<slug> on this server. A code redirects (or shows its
content) only while its owner's access is valid, so codes stop scanning when the
free trial ends and start working again as soon as an admin extends the account.
"""
import os
import secrets
from pathlib import Path

from flask import Flask, abort, request, send_from_directory, session
from werkzeug.security import generate_password_hash

from . import db as dbmod

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_app(test_config=None):
    data_dir = (test_config or {}).get("DATA_DIR") or os.environ.get("QR_DATA_DIR") or "data"
    web_dir = os.environ.get("QR_WEB_DIR") or str(WEB_DIR)
    app = Flask(__name__, static_folder=web_dir, static_url_path="")
    app.config.from_mapping(
        DATA_DIR=data_dir,
        DATABASE=os.path.join(data_dir, "qr.db"),
        TRIAL_DAYS=int(os.environ.get("TRIAL_DAYS", "7")),
        # Shown to users whose trial ended and on expired code pages, e.g. "WhatsApp +91 98765 43210".
        CONTACT_TEXT=os.environ.get("CONTACT_TEXT", "Contact the administrator to extend your account."),
        # Used to build short links; defaults to the address the request came in on.
        PUBLIC_URL=os.environ.get("PUBLIC_URL", "").rstrip("/"),
        MAX_CONTENT_LENGTH=256 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HTTPS_ONLY", "").lower() in ("1", "true", "yes"),
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or _persistent_secret(app.config["DATA_DIR"])
    if os.environ.get("BEHIND_PROXY", "").lower() in ("1", "true", "yes"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    dbmod.init_db(app.config["DATABASE"])
    app.teardown_appcontext(dbmod.close_db)
    _bootstrap_admin(app)

    from . import admin, api, public
    app.register_blueprint(api.bp)
    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)

    @app.route("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.before_request
    def check_csrf():
        # The JSON API authenticates with bearer tokens; the admin pages use a session cookie.
        if request.method == "POST" and request.path.startswith("/admin"):
            expected = session.get("csrf_token")
            if not expected or request.form.get("csrf_token") != expected:
                abort(400, "Invalid or missing CSRF token - reload the page and try again.")

    app.jinja_env.globals["csrf_token"] = csrf_token
    register_cli(app)
    return app


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def _persistent_secret(data_dir):
    path = Path(data_dir) / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


def create_user(conn, email, password, name="", is_admin=False, trial_days=7):
    cur = conn.execute(
        "INSERT INTO users (email, name, password_hash, is_admin, access_until, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (email.strip().lower(), name.strip(), generate_password_hash(password), int(is_admin),
         dbmod.days_from_now(36500 if is_admin else trial_days), dbmod.now_str()))
    conn.commit()
    return cur.lastrowid


def _bootstrap_admin(app):
    """Creates the first admin from ADMIN_EMAIL / ADMIN_PASSWORD if that account doesn't exist yet."""
    email, password = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if not (email and password):
        return
    conn = dbmod.connect(app.config["DATABASE"])
    try:
        if not conn.execute("SELECT 1 FROM users WHERE email = ?", (email.strip().lower(),)).fetchone():
            create_user(conn, email, password, "Admin", is_admin=True)
    finally:
        conn.close()


def register_cli(app):
    import click

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.password_option()
    def create_admin(email, password):
        """Create an admin account, or make an existing account an admin."""
        conn = dbmod.get_db()
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email.lower(),)).fetchone():
            conn.execute("UPDATE users SET is_admin = 1, access_until = ? WHERE email = ?",
                         (dbmod.days_from_now(36500), email.lower()))
            conn.commit()
        else:
            create_user(conn, email, password, "Admin", is_admin=True)
        click.echo(f"{email} is now an admin.")
