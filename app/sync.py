"""Moves data between the database and Hikvision terminals."""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db as dbmod
from .hikvision import HikvisionClient, HikvisionError, parse_device_time

log = logging.getLogger(__name__)

# Access-control "minor" event codes that represent a successful verification.
SUCCESS_MINORS = {1, 38, 75}


def device_now(tz_offset):
    """Current wall-clock time at the device, from its configured UTC offset."""
    sign = -1 if tz_offset.startswith("-") else 1
    hours, _, minutes = tz_offset.lstrip("+-").partition(":")
    offset = timedelta(hours=int(hours or 0), minutes=int(minutes or 0)) * sign
    return (datetime.now(timezone.utc) + offset).replace(tzinfo=None, microsecond=0)


def client_for(device, **kwargs):
    return HikvisionClient.from_row(device, **kwargs)


def _minors(db):
    raw = dbmod.get_setting(db, "event_minors") or "75"
    return [int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()] or [75]


def ensure_employee(db, employee_no, name=None):
    row = db.execute("SELECT id, name FROM employees WHERE employee_no = ?", (employee_no,)).fetchone()
    if row:
        return row["id"], False
    cur = db.execute(
        "INSERT INTO employees (employee_no, name, shift_id) VALUES (?, ?, (SELECT MIN(id) FROM shifts))",
        (employee_no, name or f"Employee {employee_no}"))
    return cur.lastrowid, True


def record_punch(db, employee_no, punch_time, device_id=None, source="device",
                 verify_mode=None, serial_no=None, note=None, name=None):
    """Insert a punch; returns True if it was new."""
    ensure_employee(db, employee_no, name)
    cur = db.execute(
        "INSERT OR IGNORE INTO punches (employee_no, punch_time, device_id, source, verify_mode, serial_no, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (employee_no, punch_time, device_id, source, verify_mode, serial_no, note))
    return cur.rowcount > 0


def _event_employee(ev):
    emp = ev.get("employeeNoString") or ev.get("employeeNo")
    return str(emp).strip() if emp not in (None, "", 0) else None


def sync_events(db, device, start=None, end=None, client=None):
    """Pull access events from a device and store them as punches.

    Without an explicit range it resumes a few minutes before the last event
    already stored for this device (duplicates are ignored by the UNIQUE key).
    """
    client = client or client_for(device)
    now = device_now(device["tz_offset"])
    if start is None:
        if device["last_event_time"]:
            start = datetime.strptime(device["last_event_time"], "%Y-%m-%d %H:%M:%S") - timedelta(minutes=10)
        else:
            days = int(dbmod.get_setting(db, "initial_sync_days") or 30)
            start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0)
    end = end or now

    added, seen, latest = 0, 0, device["last_event_time"]
    try:
        for ev in client.iter_events(start, end, _minors(db)):
            emp = _event_employee(ev)
            if not emp or not ev.get("time"):
                continue
            seen += 1
            punch_time = parse_device_time(ev["time"])
            if record_punch(db, emp, punch_time, device["id"], "device",
                            ev.get("currentVerifyMode"), ev.get("serialNo"), name=ev.get("name")):
                added += 1
            if not latest or punch_time > latest:
                latest = punch_time
    except HikvisionError as exc:
        _mark(db, device["id"], "error", str(exc))
        db.commit()
        raise
    db.execute("UPDATE devices SET last_event_time = ? WHERE id = ?", (latest, device["id"]))
    _mark(db, device["id"], "ok", None)
    db.commit()
    return {"seen": seen, "added": added}


def sync_users_from_device(db, device, client=None):
    """Import the device's user list; existing employees keep their local details."""
    client = client or client_for(device)
    created = updated = 0
    for user in client.iter_users():
        emp = str(user.get("employeeNo", "")).strip()
        if not emp:
            continue
        name = (user.get("name") or "").strip() or f"Employee {emp}"
        _, is_new = ensure_employee(db, emp, name)
        if is_new:
            created += 1
        else:
            db.execute("UPDATE employees SET name = ? WHERE employee_no = ? AND name LIKE 'Employee %'",
                       (name, emp))
            updated += 1
        if user.get("numOfFace"):
            db.execute("UPDATE employees SET has_face = 1 WHERE employee_no = ?", (emp,))
        gender = user.get("gender")
        if gender in ("male", "female"):
            db.execute("UPDATE employees SET gender = COALESCE(gender, ?) WHERE employee_no = ?", (gender, emp))
    _mark(db, device["id"], "ok", None)
    db.commit()
    return {"created": created, "existing": updated}


def push_employee(device, employee, client=None):
    client = client or client_for(device)
    client.upsert_user(employee["employee_no"], employee["name"], employee["gender"])
    if employee["card_no"]:
        client.set_card(employee["employee_no"], employee["card_no"])


def handle_push_event(db, payload, remote_ip=None):
    """Store an event delivered by the device's HTTP listening (push) mode.

    Returns True if a new punch was recorded.
    """
    ev = payload.get("AccessControllerEvent") or {}
    emp = _event_employee(ev)
    if not emp:
        return False
    if ev.get("majorEventType") not in (None, 5):
        return False
    minor = ev.get("subEventType")
    allowed = set(_minors(db))
    if minor is not None and 0 not in allowed and int(minor) not in allowed | SUCCESS_MINORS:
        return False
    when = payload.get("dateTime")
    if not when:
        return False
    ip = payload.get("ipAddress") or remote_ip
    device = db.execute("SELECT id FROM devices WHERE host = ?", (ip,)).fetchone() if ip else None
    punch_time = parse_device_time(when)
    added = record_punch(db, emp, punch_time, device["id"] if device else None, "push",
                         ev.get("currentVerifyMode"), ev.get("serialNo"), name=ev.get("name"))
    if device:
        db.execute("UPDATE devices SET last_sync_at = datetime('now','localtime'), last_status = 'ok', "
                   "last_event_time = MAX(COALESCE(last_event_time, ''), ?) WHERE id = ?",
                   (punch_time, device["id"]))
    db.commit()
    return added


def _mark(db, device_id, status, error):
    db.execute("UPDATE devices SET last_sync_at = datetime('now','localtime'), last_status = ?, last_error = ? "
               "WHERE id = ?", (status, error, device_id))


class SyncScheduler(threading.Thread):
    """Background thread that periodically pulls events from every enabled device."""

    def __init__(self, database):
        super().__init__(daemon=True, name="hik-sync")
        self.database = database
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            interval = 5
            try:
                conn = dbmod.connect(self.database)
                try:
                    interval = int(dbmod.get_setting(conn, "sync_interval_min") or 0)
                    if interval > 0:
                        for device in conn.execute("SELECT * FROM devices WHERE enabled = 1").fetchall():
                            try:
                                res = sync_events(conn, device)
                                log.info("Synced %s: %s", device["name"], res)
                            except HikvisionError as exc:
                                log.warning("Sync failed for %s: %s", device["name"], exc)
                finally:
                    conn.close()
            except Exception:  # keep the thread alive no matter what
                log.exception("Scheduler iteration failed")
            self._stop_event.wait(max(interval, 1) * 60)
