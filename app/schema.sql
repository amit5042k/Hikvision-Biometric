PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin',   -- admin | viewer
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS devices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    host            TEXT NOT NULL,
    port            INTEGER NOT NULL DEFAULT 80,
    scheme          TEXT NOT NULL DEFAULT 'http',
    username        TEXT NOT NULL DEFAULT 'admin',
    password        TEXT NOT NULL DEFAULT '',
    door_no         INTEGER NOT NULL DEFAULT 1,
    tz_offset       TEXT NOT NULL DEFAULT '+05:30',
    enabled         INTEGER NOT NULL DEFAULT 1,
    model           TEXT,
    serial_no       TEXT,
    firmware        TEXT,
    mac_address     TEXT,
    last_event_time TEXT,
    last_sync_at    TEXT,
    last_status     TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS departments (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS shifts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    start_time     TEXT NOT NULL DEFAULT '09:00',
    end_time       TEXT NOT NULL DEFAULT '18:00',
    grace_in_min   INTEGER NOT NULL DEFAULT 10,
    grace_out_min  INTEGER NOT NULL DEFAULT 10,
    break_minutes  INTEGER NOT NULL DEFAULT 0,
    half_day_hours REAL NOT NULL DEFAULT 4,
    full_day_hours REAL NOT NULL DEFAULT 8,
    weekly_off     TEXT NOT NULL DEFAULT '6'      -- comma separated weekdays, 0 = Monday
);

CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_no   TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    shift_id      INTEGER REFERENCES shifts(id) ON DELETE SET NULL,
    designation   TEXT,
    email         TEXT,
    phone         TEXT,
    card_no       TEXT,
    gender        TEXT,
    join_date     TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    has_face      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS punches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_no TEXT NOT NULL,
    punch_time  TEXT NOT NULL,                     -- device local time, 'YYYY-MM-DD HH:MM:SS'
    device_id   INTEGER REFERENCES devices(id) ON DELETE SET NULL,
    source      TEXT NOT NULL DEFAULT 'device',    -- device | push | manual
    verify_mode TEXT,
    serial_no   INTEGER,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (employee_no, punch_time)
);
CREATE INDEX IF NOT EXISTS idx_punches_time ON punches (punch_time);

CREATE TABLE IF NOT EXISTS holidays (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leaves (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    leave_type  TEXT NOT NULL DEFAULT 'Casual',
    half_day    INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_leaves_range ON leaves (start_date, end_date);
