import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash

DEFAULT_SETTINGS = {
    "company_name": "My Company",
    "sync_interval_min": "5",        # background event pull interval, 0 disables it
    "initial_sync_days": "30",       # how far back the first event pull goes
    "event_minors": "75,1,38",       # AcsEvent minor codes: 75 face, 1 card, 38 fingerprint; 0 = all
    "window_before_hours": "4",      # punches this long before shift start count for that day
    "ot_min_minutes": "30",          # overtime below this is ignored
    "push_token": "",                # required ?token= on the device push endpoint when set
    "bridge_token": "",              # shared secret for the on-site bridge agent (generated on first run)
    # daily email summary
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_security": "starttls",     # starttls | ssl | none
    "report_recipients": "",         # comma separated
    "report_time": "19:00",          # when the daily summary is sent (server local time)
    "report_last_sent": "",
    "require_2fa_admins": "0",       # 1 = administrators must set up two-factor sign-in
    "default_language": "en",        # en | hi
}

# Schema changes after the first release.
#
# schema.sql is the version-1 baseline and must never change. To change the
# database in a later release, APPEND a new (version, sql) entry here. Each
# migration runs once, inside a transaction, on the next start-up, so updating
# the code never resets existing data, users or passwords.
MIGRATIONS = [
    (2, """
        ALTER TABLE devices ADD COLUMN connection TEXT NOT NULL DEFAULT 'direct';  -- direct | bridge
        ALTER TABLE devices ADD COLUMN last_seen_at TEXT;                          -- last bridge check-in
        CREATE TABLE device_jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
            action      TEXT NOT NULL,
            payload     TEXT,
            status      TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | done | failed
            result      TEXT,
            created_by  TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            finished_at TEXT
        );
        CREATE INDEX idx_device_jobs_status ON device_jobs (status, device_id);
    """),
    (3, """
        ALTER TABLE device_jobs ADD COLUMN sent_at TEXT;          -- when the bridge picked the job up
        CREATE TABLE login_attempts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT NOT NULL,
            ip         TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX idx_login_attempts ON login_attempts (username, created_at);
    """),
    (4, """
        -- employee self-service logins: role 'employee' linked to one employee
        ALTER TABLE users ADD COLUMN employee_id INTEGER REFERENCES employees(id) ON DELETE CASCADE;
        -- leave approval workflow; leave recorded before this version counts as approved
        ALTER TABLE leaves ADD COLUMN status TEXT NOT NULL DEFAULT 'approved';  -- pending | approved | rejected
        ALTER TABLE leaves ADD COLUMN requested_by TEXT;
        ALTER TABLE leaves ADD COLUMN decided_by TEXT;
        ALTER TABLE leaves ADD COLUMN decided_at TEXT;
        ALTER TABLE leaves ADD COLUMN decision_note TEXT;
        CREATE TABLE leave_types (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            yearly_quota REAL NOT NULL DEFAULT 0,   -- days per calendar year; 0 = no limit
            paid         INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO leave_types (name, yearly_quota, paid) VALUES
            ('Casual', 12, 1), ('Sick', 12, 1), ('Earned', 15, 1), ('Comp Off', 0, 1),
            ('Maternity', 0, 1), ('Unpaid', 0, 0), ('Other', 0, 1);
        -- missed-punch correction requests from employees
        CREATE TABLE punch_requests (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id   INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            punch_time    TEXT NOT NULL,
            reason        TEXT,
            status        TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
            requested_by  TEXT,
            decided_by    TEXT,
            decided_at    TEXT,
            decision_note TEXT,
            created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE audit_log (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            username TEXT,
            action   TEXT NOT NULL,
            details  TEXT
        );
        CREATE INDEX idx_audit_at ON audit_log (at);
    """),
    (5, """
        -- several offices, each with its own terminals and (optionally) its own bridge
        CREATE TABLE sites (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL UNIQUE,
            address      TEXT,
            bridge_token TEXT UNIQUE
        );
        ALTER TABLE employees ADD COLUMN site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL;
        ALTER TABLE devices ADD COLUMN site_id INTEGER REFERENCES sites(id) ON DELETE SET NULL;
        -- shift roster: per-day shift overrides; shift_id NULL = rest day
        CREATE TABLE roster (
            employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
            date        TEXT NOT NULL,
            shift_id    INTEGER REFERENCES shifts(id) ON DELETE CASCADE,
            PRIMARY KEY (employee_id, date)
        );
        -- two-factor sign-in (TOTP) and interface language per user
        ALTER TABLE users ADD COLUMN totp_secret TEXT;
        ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE users ADD COLUMN recovery_codes TEXT;
        ALTER TABLE users ADD COLUMN totp_last_step INTEGER;
        ALTER TABLE users ADD COLUMN language TEXT;
    """),
]
SCHEMA_VERSION = MIGRATIONS[-1][0] if MIGRATIONS else 1


def connect(path):
    conn = sqlite3.connect(path, detect_types=0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path, backup_dir=None):
    """Create the database if needed and bring it up to the current schema.

    Safe to run on every start: existing rows are never touched.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    has_data = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
    if has_data and version < SCHEMA_VERSION and backup_dir:
        conn.close()
        backup(path, backup_dir)  # safety copy before changing an existing database
        conn = connect(path)
    if version == 0:
        # new database, or one created before versioning existed (identical to the baseline)
        conn.executescript((Path(__file__).parent / "schema.sql").read_text())
        conn.execute("PRAGMA user_version = 1")
        version = 1
    migrate(conn, version)
    seed(conn)
    conn.close()


def migrate(conn, current):
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        try:
            conn.execute("BEGIN")
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def _statements(sql):
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def seed(conn):
    """Insert defaults that are missing; never overwrites existing values."""
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            ("admin", generate_password_hash("admin")),
        )
    if not conn.execute("SELECT 1 FROM shifts LIMIT 1").fetchone():
        conn.execute("INSERT INTO shifts (name) VALUES ('General')")
    conn.execute("UPDATE settings SET value = ? WHERE key = 'bridge_token' AND COALESCE(value, '') = ''",
                 (secrets.token_urlsafe(24),))
    conn.commit()


def backup(path, backup_dir, keep=30):
    """Write a consistent copy of the live database and prune old copies.

    Uses SQLite's online backup API, so it is safe while the app is running.
    """
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"attendance-{datetime.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(path)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    for old in sorted(backup_dir.glob("attendance-*.db"))[:-keep]:
        old.unlink()
    return target


def get_setting(db, key):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else DEFAULT_SETTINGS.get(key, "")


def get_settings(db):
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")})
    return out


def set_setting(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def audit(db, username, action, details=""):
    """Record who did what. The caller commits."""
    db.execute("INSERT INTO audit_log (username, action, details) VALUES (?, ?, ?)",
               (username, action, (details or "")[:1000]))
