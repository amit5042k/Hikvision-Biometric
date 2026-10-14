"""HTTP API used by the on-site bridge agent (bridge/hik_bridge.py).

The agent runs on a PC on the same network as the terminals. It only makes
outgoing HTTPS requests to this server, so no port forwarding is needed:

* ``GET  /api/bridge/poll``        devices to serve + queued jobs (marks jobs as sent)
* ``POST /api/bridge/punches``     upload events read from a terminal
* ``POST /api/bridge/jobs/<id>``   report the outcome of a queued job
* ``POST /api/bridge/status``      report that a terminal could not be reached

Every request carries ``Authorization: Bearer <bridge token>`` (Settings page).
"""
import hmac
import json

from flask import Blueprint, abort, jsonify, request

from . import db as dbmod
from . import sync
from .db import get_db

bp = Blueprint("bridge", __name__, url_prefix="/api/bridge")

JOB_TIMEOUT_MIN = 10  # a job handed out but never answered is failed after this long


@bp.before_request
def check_token():
    expected = dbmod.get_setting(get_db(), "bridge_token")
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        abort(401)


def _device(device_id):
    dev = get_db().execute("SELECT * FROM devices WHERE id = ? AND connection = 'bridge'",
                           (device_id,)).fetchone()
    if not dev:
        abort(404)
    return dev


@bp.get("/poll")
def poll():
    db = get_db()
    db.execute("UPDATE devices SET last_seen_at = datetime('now','localtime') WHERE connection = 'bridge'")
    db.execute("UPDATE device_jobs SET status = 'failed', result = 'Bridge did not answer in time', "
               "finished_at = datetime('now','localtime') WHERE status = 'sent' "
               "AND created_at < datetime('now','localtime', ?)", (f"-{JOB_TIMEOUT_MIN} minutes",))
    devices = db.execute("SELECT * FROM devices WHERE connection = 'bridge' AND enabled = 1").fetchall()
    ids = [d["id"] for d in devices]
    jobs = []
    if ids:
        jobs = db.execute(f"SELECT id, device_id, action, payload FROM device_jobs WHERE status = 'pending' "
                          f"AND device_id IN ({','.join('?' * len(ids))}) ORDER BY id LIMIT 20", ids).fetchall()
        for job in jobs:
            db.execute("UPDATE device_jobs SET status = 'sent' WHERE id = ?", (job["id"],))
    db.commit()
    settings = dbmod.get_settings(db)
    return jsonify({
        "devices": [{k: d[k] for k in ("id", "name", "host", "port", "scheme", "username", "password",
                                       "door_no", "tz_offset", "last_event_time")} for d in devices],
        "jobs": [{"id": j["id"], "device_id": j["device_id"], "action": j["action"],
                  "payload": json.loads(j["payload"] or "{}")} for j in jobs],
        "settings": {"event_minors": sync.minors(db),
                     "initial_sync_days": int(settings.get("initial_sync_days") or 30),
                     "sync_interval_min": int(settings.get("sync_interval_min") or 0)},
    })


@bp.post("/punches")
def punches():
    body = request.get_json(force=True, silent=True) or {}
    dev = _device(body.get("device_id"))
    res = sync.store_events(get_db(), dev, body.get("events") or [])
    return jsonify(res)


@bp.post("/status")
def status():
    body = request.get_json(force=True, silent=True) or {}
    dev = _device(body.get("device_id"))
    db = get_db()
    sync.mark(db, dev["id"], "ok" if body.get("ok") else "error", body.get("error"))
    db.commit()
    return jsonify({"status": "ok"})


@bp.post("/jobs/<int:job_id>")
def job_result(job_id):
    db = get_db()
    job = db.execute("SELECT * FROM device_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        abort(404)
    body = request.get_json(force=True, silent=True) or {}
    dev = db.execute("SELECT * FROM devices WHERE id = ?", (job["device_id"],)).fetchone()
    message = body.get("message") or ""
    if body.get("ok"):
        payload = json.loads(job["payload"] or "{}")
        message = sync.apply_result(db, dev, job["action"], payload, body.get("data")) or message
        status = "done"
    else:
        sync.mark(db, dev["id"], "error", message)
        status = "failed"
    db.execute("UPDATE device_jobs SET status = ?, result = ?, finished_at = datetime('now','localtime'), "
               "payload = CASE WHEN action = 'upload-face' THEN NULL ELSE payload END WHERE id = ?",
               (status, message[:1000], job_id))
    db.commit()
    return jsonify({"status": status})
