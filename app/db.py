import sqlite3
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
}


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


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    schema = (Path(__file__).parent / "schema.sql").read_text()
    conn.executescript(schema)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    if not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            ("admin", generate_password_hash("admin")),
        )
    if not conn.execute("SELECT 1 FROM shifts LIMIT 1").fetchone():
        conn.execute("INSERT INTO shifts (name) VALUES ('General')")
    conn.commit()
    conn.close()


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
