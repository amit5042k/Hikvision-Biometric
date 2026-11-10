import sqlite3
from datetime import datetime
from pathlib import Path

from flask import current_app, g
from werkzeug.security import generate_password_hash

DEFAULT_SETTINGS = {
    "school_name": "My School",
    "class_match_threshold": "0.5",  # max face-descriptor distance for a match (lower = stricter)
    "class_detector": "ssd",         # ssd (accurate, small/far faces) | tiny (fast, weaker hardware)
    "class_weekly_off": "6",         # weekly-off weekdays, Mon=0 .. Sun=6, comma separated
    "class_late_grace": "10",        # minutes after the first period starts before an arrival is late
    "class_low_attendance": "75",    # % below which a student is flagged on the Analytics page
}

# schema.sql is the version-1 baseline and must never change. To change the
# database later, APPEND (version, sql) here; each runs once on start-up, so
# updating the code never resets existing data.
MIGRATIONS = [
    (2, """
        -- one login per classroom: used by that room's panel app and its class teacher
        ALTER TABLE users ADD COLUMN classroom_id INTEGER REFERENCES classrooms(id) ON DELETE CASCADE
    """),
]
SCHEMA_VERSION = MIGRATIONS[-1][0] if MIGRATIONS else 1


def connect(path):
    conn = sqlite3.connect(path, check_same_thread=False)
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
    """Create the database if needed and bring it up to date. Never touches existing rows."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version and version < SCHEMA_VERSION and backup_dir:
        conn.close()
        backup(path, backup_dir)
        conn = connect(path)
    if version == 0:
        conn.executescript((Path(__file__).parent / "schema.sql").read_text())
        conn.execute("PRAGMA user_version = 1")
        version = 1
    for v, sql in MIGRATIONS:
        if v > version:
            conn.execute("BEGIN")
            try:
                for statement in (s.strip() for s in sql.split(";")):
                    if statement:
                        conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {int(v)}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin', ?, 'admin')",
                     (generate_password_hash("admin"),))
    conn.commit()
    conn.close()


def backup(path, backup_dir, keep=30):
    """Consistent copy of the live database (safe while running); keeps the latest `keep`."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"classroom-{datetime.now():%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(path), sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    for old in sorted(backup_dir.glob("classroom-*.db"))[:-keep]:
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
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def audit(db, username, action, details=""):
    """Record who did what. The caller commits."""
    db.execute("INSERT INTO audit_log (username, action, details) VALUES (?, ?, ?)",
               (username, action, (details or "")[:1000]))
