import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    disabled      INTEGER NOT NULL DEFAULT 0,
    access_until  TEXT NOT NULL,                 -- UTC; codes scan only before this
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash    TEXT PRIMARY KEY,              -- sha256 of the bearer token
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    slug          TEXT NOT NULL UNIQUE,
    type          TEXT NOT NULL,                 -- url | text | wifi | vcard | email | sms | phone
    data          TEXT NOT NULL,                 -- JSON of the form fields
    label         TEXT NOT NULL DEFAULT '',
    scans         INTEGER NOT NULL DEFAULT 0,
    last_scan_at  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_user ON codes (user_id);
"""

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def fmt(dt):
    return dt.strftime(TIME_FMT)


def parse(s):
    return datetime.strptime(s, TIME_FMT)


def now_str():
    return fmt(utcnow())


def days_from_now(days):
    return fmt(utcnow() + timedelta(days=days))


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


def init_db(path):
    conn = connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.close()


def is_active(user):
    """A user's codes scan (and new codes can be made) while access has not run out."""
    return not user["disabled"] and parse(user["access_until"]) > utcnow()
