-- IFP Classroom Attendance: standalone database (version 1 baseline; never edit,
-- add later changes as migrations in db.py).
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer',   -- admin | viewer
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    ip         TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_login_attempts ON login_attempts (username, created_at);
CREATE TABLE audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    username TEXT,
    action   TEXT NOT NULL,
    details  TEXT
);
CREATE INDEX idx_audit_at ON audit_log (at);
CREATE TABLE holidays (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);
CREATE TABLE classrooms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    kiosk_token     TEXT NOT NULL UNIQUE,         -- secret part of the kiosk URL
    absence_minutes INTEGER NOT NULL DEFAULT 10,  -- unseen this long = left the class
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_seen_at    TEXT,                         -- last kiosk check-in
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE class_persons (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    person_no    TEXT NOT NULL UNIQUE,            -- admission / staff number
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'student', -- student | teacher
    class_name   TEXT,                            -- e.g. 8-A
    classroom_id INTEGER REFERENCES classrooms(id) ON DELETE SET NULL,  -- home classroom
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE face_samples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  INTEGER NOT NULL REFERENCES class_persons(id) ON DELETE CASCADE,
    descriptor TEXT NOT NULL,                     -- JSON array of 128 floats
    thumbnail  TEXT,                              -- small JPEG data URL of the face
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX idx_face_samples_person ON face_samples (person_id);
CREATE TABLE class_sightings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    classroom_id INTEGER NOT NULL REFERENCES classrooms(id) ON DELETE CASCADE,
    person_id    INTEGER NOT NULL REFERENCES class_persons(id) ON DELETE CASCADE,
    seen_at      TEXT NOT NULL,                   -- server local time, YYYY-MM-DD HH:MM:SS
    distance     REAL
);
CREATE INDEX idx_sightings_room ON class_sightings (classroom_id, seen_at);
CREATE INDEX idx_sightings_person ON class_sightings (person_id, seen_at);
CREATE TABLE class_periods (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    start_time TEXT NOT NULL,                     -- HH:MM
    end_time   TEXT NOT NULL
);
