"""Device-side operations shared by the web server (direct mode) and the bridge agent.

Nothing in here touches the database: every function talks to one terminal and
returns plain JSON-serialisable data, so the same code can run on the server
when it can reach the terminal, or on an on-site PC that relays the result to a
cloud-hosted server.
"""
import base64
from datetime import datetime, timedelta, timezone

from .hikvision import HikvisionClient, HikvisionError, parse_device_time

FMT = "%Y-%m-%d %H:%M:%S"


def device_now(tz_offset):
    """Current wall-clock time at the device, from its configured UTC offset."""
    tz_offset = tz_offset or "+00:00"
    sign = -1 if tz_offset.startswith("-") else 1
    hours, _, minutes = tz_offset.lstrip("+-").partition(":")
    offset = timedelta(hours=int(hours or 0), minutes=int(minutes or 0)) * sign
    return (datetime.now(timezone.utc) + offset).replace(tzinfo=None, microsecond=0)


def client_for(device, **kwargs):
    return HikvisionClient.from_row(device, **kwargs)


def event_window(device, initial_days):
    """Default range for an incremental pull: shortly before the last known event until now."""
    now = device_now(device["tz_offset"])
    if device["last_event_time"]:
        start = datetime.strptime(device["last_event_time"], FMT) - timedelta(minutes=10)
    else:
        start = (now - timedelta(days=int(initial_days or 30))).replace(hour=0, minute=0, second=0)
    return start, now


def fetch_events(client, start, end, minors):
    """Return access events as ``[{employee_no, time, verify_mode, serial_no, name}]``."""
    out = []
    for ev in client.iter_events(start, end, minors):
        emp = ev.get("employeeNoString") or ev.get("employeeNo")
        if emp in (None, "", 0) or not ev.get("time"):
            continue  # door/alarm events and unrecognised faces
        out.append({
            "employee_no": str(emp).strip(),
            "time": parse_device_time(ev["time"]),
            "verify_mode": ev.get("currentVerifyMode"),
            "serial_no": ev.get("serialNo"),
            "name": ev.get("name"),
        })
    return out


def push_employee(client, emp):
    client.upsert_user(emp["employee_no"], emp["name"], emp.get("gender"))
    if emp.get("card_no"):
        client.set_card(emp["employee_no"], emp["card_no"])


def run(client, action, payload, tz_offset="+00:00"):
    """Execute one action against a terminal. Returns ``(message, data)``.

    Raises HikvisionError when the terminal rejects the whole action.
    """
    payload = payload or {}
    if action == "test":
        info = client.device_info()
        return (f"Connected: {info.get('model', '?')} (S/N {info.get('serialNumber', '?')}, "
                f"firmware {info.get('firmwareVersion', '?')})."), info
    if action == "set-time":
        client.set_time(device_now(tz_offset))
        return "Device clock set to current time.", {}
    if action == "open-door":
        client.open_door()
        return "Door open command sent.", {}
    if action == "sync-users":
        users = [{"employeeNo": str(u.get("employeeNo", "")), "name": u.get("name"), "gender": u.get("gender"),
                  "numOfFace": u.get("numOfFace", 0)} for u in client.iter_users()]
        return f"Read {len(users)} users from the device.", {"users": users}
    if action == "push-employees":
        sent, errors = 0, []
        for emp in payload.get("employees", []):
            try:
                push_employee(client, emp)
                sent += 1
            except HikvisionError as exc:
                errors.append(f"{emp['employee_no']}: {exc}")
        if errors and not sent:
            raise HikvisionError("; ".join(errors[:5]))
        msg = f"Sent {sent} employee(s) to the device."
        if errors:
            msg += f" {len(errors)} failed: " + "; ".join(errors[:5])
        return msg, {"sent": sent, "errors": errors}
    if action == "delete-employee":
        client.delete_user(payload["employee_no"])
        return f"Removed {payload['employee_no']} from the device.", {}
    if action == "upload-face":
        client.upsert_user(payload["employee_no"], payload["name"], payload.get("gender"))
        client.upload_face(payload["employee_no"], base64.b64decode(payload["image_b64"]))
        return f"Face enrolled for {payload['name']}.", {"employee_no": payload["employee_no"]}
    if action == "configure-push":
        client.set_http_host(payload["ip"], payload["port"], payload["path"])
        return f"Device will push events to http://{payload['ip']}:{payload['port']}{payload['path']}", {}
    raise HikvisionError(f"Unknown action {action!r}")
