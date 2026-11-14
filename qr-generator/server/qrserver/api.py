"""JSON API used by the website and the Android app. Auth: `Authorization: Bearer <token>`."""
import hashlib
import json
import re
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import Blueprint, abort, current_app, g, jsonify, request
from werkzeug.security import check_password_hash

from . import create_user
from . import db as dbmod
from .content import ContentError, clean, default_label

bp = Blueprint("api", __name__, url_prefix="/api")

SLUG_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alikes
MAX_CODES_PER_USER = 500

# Simple per-IP limit on login/registration attempts (per process).
_attempts = defaultdict(deque)
ATTEMPT_WINDOW, ATTEMPT_LIMIT = 600, 20


@bp.errorhandler(400)
@bp.errorhandler(401)
@bp.errorhandler(403)
@bp.errorhandler(404)
@bp.errorhandler(429)
def json_error(err):
    return jsonify(error=err.description), err.code


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _rate_limit():
    if current_app.config.get("TESTING"):
        return
    now, q = time.monotonic(), _attempts[request.remote_addr]
    while q and q[0] < now - ATTEMPT_WINDOW:
        q.popleft()
    if len(q) >= ATTEMPT_LIMIT:
        abort(429, "Too many attempts. Please wait a few minutes and try again.")
    q.append(now)


def _issue_token(user_id):
    token = secrets.token_urlsafe(32)
    db = dbmod.get_db()
    db.execute("INSERT INTO tokens (token_hash, user_id, created_at) VALUES (?, ?, ?)",
               (_hash(token), user_id, dbmod.now_str()))
    db.commit()
    return token


def auth_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        user = token and dbmod.get_db().execute(
            "SELECT u.* FROM tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
            (_hash(token),)).fetchone()
        if not user:
            abort(401, "Please log in.")
        g.user, g.token = user, token
        return view(*args, **kwargs)
    return wrapped


def _body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, "Expected a JSON body.")
    return data


def short_url(slug):
    base = current_app.config["PUBLIC_URL"] or request.host_url.rstrip("/")
    return f"{base}/r/{slug}"


def account_json(user):
    return {
        "email": user["email"],
        "name": user["name"],
        "is_admin": bool(user["is_admin"]),
        "active": dbmod.is_active(user),
        "disabled": bool(user["disabled"]),
        "access_until": user["access_until"].replace(" ", "T") + "Z",
        "contact": current_app.config["CONTACT_TEXT"],
    }


def code_json(row):
    return {
        "id": row["id"], "slug": row["slug"], "type": row["type"], "label": row["label"],
        "data": json.loads(row["data"]), "scans": row["scans"],
        "last_scan_at": row["last_scan_at"] and row["last_scan_at"].replace(" ", "T") + "Z",
        "created_at": row["created_at"].replace(" ", "T") + "Z",
        "short_url": short_url(row["slug"]),
    }


# ---------------------------------------------------------------- accounts

@bp.post("/register")
def register():
    _rate_limit()
    body = _body()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    name = str(body.get("name", "")).strip()[:80]
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(email) > 254:
        abort(400, "Enter a valid email address.")
    if len(password) < 8:
        abort(400, "Password must be at least 8 characters.")
    db = dbmod.get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        abort(400, "An account with this email already exists. Log in instead.")
    user_id = create_user(db, email, password, name, trial_days=current_app.config["TRIAL_DAYS"])
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return jsonify(token=_issue_token(user_id), account=account_json(user)), 201


@bp.post("/login")
def login():
    _rate_limit()
    body = _body()
    email = str(body.get("email", "")).strip().lower()
    user = dbmod.get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], str(body.get("password", ""))):
        abort(401, "Wrong email or password.")
    return jsonify(token=_issue_token(user["id"]), account=account_json(user))


@bp.post("/logout")
@auth_required
def logout():
    db = dbmod.get_db()
    db.execute("DELETE FROM tokens WHERE token_hash = ?", (_hash(g.token),))
    db.commit()
    return jsonify(ok=True)


@bp.get("/me")
@auth_required
def me():
    return jsonify(account=account_json(g.user))


# ---------------------------------------------------------------- codes

def _own_code(code_id):
    row = dbmod.get_db().execute("SELECT * FROM codes WHERE id = ? AND user_id = ?",
                                 (code_id, g.user["id"])).fetchone()
    if not row:
        abort(404, "QR code not found.")
    return row


def _require_active():
    if not dbmod.is_active(g.user):
        abort(403, "Your free trial has ended. " + current_app.config["CONTACT_TEXT"])


def _cleaned(body):
    try:
        return clean(str(body.get("type", "")), body.get("data"))
    except ContentError as exc:
        abort(400, str(exc))


@bp.get("/codes")
@auth_required
def list_codes():
    rows = dbmod.get_db().execute("SELECT * FROM codes WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
                                  (g.user["id"],)).fetchall()
    return jsonify(codes=[code_json(r) for r in rows])


@bp.post("/codes")
@auth_required
def create_code():
    _require_active()
    body = _body()
    data = _cleaned(body)
    db = dbmod.get_db()
    if db.execute("SELECT COUNT(*) FROM codes WHERE user_id = ?", (g.user["id"],)).fetchone()[0] >= MAX_CODES_PER_USER:
        abort(400, f"You can have at most {MAX_CODES_PER_USER} QR codes. Delete some first.")
    label = str(body.get("label", "")).strip()[:80] or default_label(body["type"], data)
    now = dbmod.now_str()
    for _ in range(10):
        slug = "".join(secrets.choice(SLUG_ALPHABET) for _ in range(7))
        if not db.execute("SELECT 1 FROM codes WHERE slug = ?", (slug,)).fetchone():
            break
    cur = db.execute(
        "INSERT INTO codes (user_id, slug, type, data, label, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], slug, body["type"], json.dumps(data), label, now, now))
    db.commit()
    return jsonify(code=code_json(_own_code(cur.lastrowid))), 201


@bp.put("/codes/<int:code_id>")
@auth_required
def update_code(code_id):
    _require_active()
    row = _own_code(code_id)
    body = _body()
    body.setdefault("type", row["type"])
    data = _cleaned(body)
    label = str(body.get("label", "")).strip()[:80] or default_label(body["type"], data)
    db = dbmod.get_db()
    db.execute("UPDATE codes SET type = ?, data = ?, label = ?, updated_at = ? WHERE id = ?",
               (body["type"], json.dumps(data), label, dbmod.now_str(), code_id))
    db.commit()
    return jsonify(code=code_json(_own_code(code_id)))


@bp.delete("/codes/<int:code_id>")
@auth_required
def delete_code(code_id):
    _own_code(code_id)
    db = dbmod.get_db()
    db.execute("DELETE FROM codes WHERE id = ?", (code_id,))
    db.commit()
    return jsonify(ok=True)
