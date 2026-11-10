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

from flask import Blueprint, abort, g, jsonify, request

from . import db as dbmod
from . import sync
from .db import get_db

bp = Blueprint("bridge", __name__, url_prefix="/api/bridge")

JOB_TIMEOUT_MIN = 10  # a job handed out but never answered is failed after this long
# Actions that must not run long after they were requested (e.g. a door opening hours
# later because the bridge PC was off when someone clicked "Open door").
EXPIRING_ACTIONS = {"open-door": 2, "set-time": 30}  # minutes a pending job stays valid


@bp.before_request
def check_token():
    """The main token serves every bridge device; an office's own token only that office's devices."""
    db = get_db()
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip().encode()
    expected = dbmod.get_setting(db, "bridge_token")
    if sent and expected and hmac.compare_digest(sent, expected.encode()):
        g.bridge_site = None
        return
    for site in db.execute("SELECT id, bridge_token FROM sites WHERE bridge_token IS NOT NULL"):
        if sent and hmac.compare_digest(sent, site["bridge_token"].encode()):
            g.bridge_site = site["id"]
            return
    abort(401)


def _scope():
    """SQL condition limiting devices to what this bridge may serve."""
    if g.bridge_site is None:
        return "connection = 'bridge'", []
    return "connection = 'bridge' AND site_id = ?", [g.bridge_site]


def _device(device_id):
    cond, args = _scope()
    dev = get_db().execute(f"SELECT * FROM devices WHERE id = ? AND {cond}", [device_id, *args]).fetchone()
    if not dev:
        abort(404)
    return dev


@bp.get("/poll")
def poll():
    db = get_db()
    cond, args = _scope()
    db.execute(f"UPDATE devices SET last_seen_at = datetime('now','localtime') WHERE {cond}", args)
    db.execute("UPDATE device_jobs SET status = 'failed', result = 'Bridge did not answer in time', "
               "finished_at = datetime('now','localtime') WHERE status = 'sent' "
               "AND COALESCE(sent_at, created_at) < datetime('now','localtime', ?)", (f"-{JOB_TIMEOUT_MIN} minutes",))
    for action, minutes in EXPIRING_ACTIONS.items():
        db.execute("UPDATE device_jobs SET status = 'failed', finished_at = datetime('now','localtime'), "
                   "result = 'Expired: the bridge was offline, so this was not carried out late' "
                   "WHERE status = 'pending' AND action = ? AND created_at < datetime('now','localtime', ?)",
                   (action, f"-{minutes} minutes"))
    devices = db.execute(f"SELECT * FROM devices WHERE {cond} AND enabled = 1", args).fetchall()
    ids = [d["id"] for d in devices]
    jobs = []
    if ids:
        jobs = db.execute(f"SELECT id, device_id, action, payload FROM device_jobs WHERE status = 'pending' "
                          f"AND device_id IN ({','.join('?' * len(ids))}) ORDER BY id LIMIT 20", ids).fetchall()
        for job in jobs:
            db.execute("UPDATE device_jobs SET status = 'sent', sent_at = datetime('now','localtime') "
                       "WHERE id = ?", (job["id"],))
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
    if job["status"] not in ("sent", "failed") or (job["status"] == "failed" and job["sent_at"] is None):
        return jsonify({"status": job["status"]})  # never handed out, or already answered
    body = request.get_json(force=True, silent=True) or {}
    dev = _device(job["device_id"])  # a bridge can only answer jobs for devices it serves
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
