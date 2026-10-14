"""Moves data between the database and Hikvision terminals.

A device is reached in one of two ways (``devices.connection``):

* ``direct`` - the server can open http://<device-ip> itself (app hosted on the
  same LAN, or the terminal is port-forwarded / on a VPN).
* ``bridge`` - the app is hosted in the cloud and cannot see the office LAN.
  Actions are queued in ``device_jobs`` and carried out by the bridge agent
  (bridge/hik_bridge.py) running on a PC next to the terminal.
"""
import json
import logging
import threading

from . import db as dbmod
from . import device_actions as actions
from .device_actions import client_for, device_now  # noqa: F401  (re-exported)
from .hikvision import HikvisionError, parse_device_time

log = logging.getLogger(__name__)

# Access-control "minor" event codes that represent a successful verification.
SUCCESS_MINORS = {1, 38, 75}


def minors(db):
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


def store_events(db, device, events, source="device"):
    """Save events produced by ``device_actions.fetch_events``; returns ``{seen, added}``."""
    added, latest = 0, device["last_event_time"]
    for ev in events:
        emp, when = str(ev.get("employee_no") or "").strip(), ev.get("time")
        if not emp or not when or len(emp) > 32:
            continue
        if record_punch(db, emp, when, device["id"], source, ev.get("verify_mode"), ev.get("serial_no"),
                        name=ev.get("name")):
            added += 1
        if not latest or when > latest:
            latest = when
    db.execute("UPDATE devices SET last_event_time = ? WHERE id = ?", (latest, device["id"]))
    mark(db, device["id"], "ok", None)
    db.commit()
    return {"seen": len(events), "added": added}


def sync_events(db, device, start=None, end=None, client=None):
    """Pull access events straight from a reachable device and store them as punches.

    Without an explicit range it resumes a few minutes before the last event
    already stored for this device (duplicates are ignored by the UNIQUE key).
    """
    client = client or client_for(device)
    default_start, default_end = actions.event_window(device, dbmod.get_setting(db, "initial_sync_days"))
    try:
        events = actions.fetch_events(client, start or default_start, end or default_end, minors(db))
    except HikvisionError as exc:
        mark(db, device["id"], "error", str(exc))
        db.commit()
        raise
    return store_events(db, device, events)


def import_users(db, device, users):
    """Import a device user list; existing employees keep their local details."""
    created = existing = 0
    for user in users:
        emp = str(user.get("employeeNo", "")).strip()
        if not emp or len(emp) > 32:
            continue
        name = (user.get("name") or "").strip() or f"Employee {emp}"
        _, is_new = ensure_employee(db, emp, name)
        if is_new:
            created += 1
        else:
            db.execute("UPDATE employees SET name = ? WHERE employee_no = ? AND name LIKE 'Employee %'",
                       (name, emp))
            existing += 1
        if user.get("numOfFace"):
            db.execute("UPDATE employees SET has_face = 1 WHERE employee_no = ?", (emp,))
        gender = user.get("gender")
        if gender in ("male", "female"):
            db.execute("UPDATE employees SET gender = COALESCE(gender, ?) WHERE employee_no = ?", (gender, emp))
    mark(db, device["id"], "ok", None)
    db.commit()
    return {"created": created, "existing": existing}


def sync_users_from_device(db, device, client=None):
    _, data = actions.run(client or client_for(device), "sync-users", {})
    return import_users(db, device, data["users"])


def employee_payload(emp):
    return {"employee_no": emp["employee_no"], "name": emp["name"], "gender": emp["gender"],
            "card_no": emp["card_no"]}


def push_employee(device, employee, client=None):
    actions.push_employee(client or client_for(device), employee_payload(employee))


# ------------------------------------------------------------ dispatch ----
def dispatch(db, device, action, payload=None, user=None):
    """Run an action now (direct device) or queue it for the bridge agent.

    Returns ``(category, message)`` for a flash message.
    """
    if device["connection"] == "bridge":
        db.execute("INSERT INTO device_jobs (device_id, action, payload, created_by) VALUES (?, ?, ?, ?)",
                   (device["id"], action, json.dumps(payload or {}), user))
        db.commit()
        return "info", (f"Queued “{action}” for {device['name']}. The bridge agent will carry it out "
                        "within about a minute - see the job list on the Devices page.")
    try:
        if action == "sync-events":
            res = sync_events(db, device, payload.get("start"), payload.get("end")) if payload \
                else sync_events(db, device)
            return "success", f"{device['name']}: downloaded {res['seen']} events, {res['added']} new punches."
        message, data = actions.run(client_for(device), action, payload, device["tz_offset"])
        message = apply_result(db, device, action, payload, data) or message
        return "success", f"{device['name']}: {message}"
    except HikvisionError as exc:
        mark(db, device["id"], "error", str(exc))
        db.commit()
        return "error", f"{device['name']}: {exc}"


def apply_result(db, device, action, payload, data):
    """Server-side follow-up once a device action succeeded. May return a better message."""
    data = data or {}
    if action == "test":
        db.execute("UPDATE devices SET model = ?, serial_no = ?, firmware = ?, mac_address = ? WHERE id = ?",
                   (data.get("model"), data.get("serialNumber"), data.get("firmwareVersion"),
                    data.get("macAddress"), device["id"]))
    elif action == "sync-users":
        res = import_users(db, device, data.get("users", []))
        return f"Imported users: {res['created']} new, {res['existing']} already known."
    elif action == "upload-face":
        db.execute("UPDATE employees SET has_face = 1 WHERE employee_no = ?", ((payload or {}).get("employee_no"),))
    mark(db, device["id"], "ok", None)
    db.commit()
    return None


def handle_push_event(db, payload, remote_ip=None):
    """Store an event delivered by the device's HTTP listening (push) mode.

    Returns True if a new punch was recorded.
    """
    ev = payload.get("AccessControllerEvent") or {}
    emp = ev.get("employeeNoString") or ev.get("employeeNo")
    if emp in (None, "", 0):
        return False
    if ev.get("majorEventType") not in (None, 5):
        return False
    minor = ev.get("subEventType")
    allowed = set(minors(db))
    if minor is not None and 0 not in allowed and int(minor) not in allowed | SUCCESS_MINORS:
        return False
    when = payload.get("dateTime")
    if not when:
        return False
    ip = payload.get("ipAddress") or remote_ip
    device = db.execute("SELECT id FROM devices WHERE host = ?", (ip,)).fetchone() if ip else None
    punch_time = parse_device_time(when)
    added = record_punch(db, str(emp).strip(), punch_time, device["id"] if device else None, "push",
                         ev.get("currentVerifyMode"), ev.get("serialNo"), name=ev.get("name"))
    if device:
        db.execute("UPDATE devices SET last_sync_at = datetime('now','localtime'), last_status = 'ok', "
                   "last_event_time = MAX(COALESCE(last_event_time, ''), ?) WHERE id = ?",
                   (punch_time, device["id"]))
    db.commit()
    return added


def mark(db, device_id, status, error):
    db.execute("UPDATE devices SET last_sync_at = datetime('now','localtime'), last_status = ?, last_error = ? "
               "WHERE id = ?", (status, error, device_id))


class SyncScheduler(threading.Thread):
    """Background thread that periodically pulls events from every enabled direct device.

    Bridge devices are polled by the bridge agent instead.
    """

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
                        for device in conn.execute("SELECT * FROM devices WHERE enabled = 1 "
                                                   "AND connection = 'direct'").fetchall():
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
