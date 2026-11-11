"""Two-factor sign-in with time-based one-time codes (TOTP, RFC 6238).

Works with any authenticator app (Google Authenticator, Microsoft
Authenticator, Authy...). Each user can turn it on under "Two-factor
sign-in"; admins can be required to use it (Settings -> General).
"""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

import segno
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

from . import db as dbmod
from . import login_required
from .db import get_db

bp = Blueprint("twofactor", __name__)

STEP = 30
DIGITS = 6


# ------------------------------------------------------------------ TOTP --
def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret):
    secret = secret.upper().replace(" ", "")
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def verify(secret, code, last_step=None, now=None, window=1):
    """Return the matched time step, or None. Codes already used (step <= last_step) are refused."""
    code = (code or "").replace(" ", "").strip()
    if not secret or len(code) != DIGITS or not code.isdigit():
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in range(current - window, current + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret, username, issuer):
    label = quote(f"{issuer}:{username}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"


def qr_svg(uri):
    return Markup(segno.make(uri, error="m").svg_inline(scale=5, dark="#161b26"))


# -------------------------------------------------------- recovery codes --
def new_recovery_codes(n=8):
    codes = ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(n)]  # e.g. 3fa1-9c0d
    return codes, json.dumps([generate_password_hash(c) for c in codes])


def use_recovery_code(db, user, code):
    code = (code or "").strip().lower()
    hashes = json.loads(user["recovery_codes"] or "[]")
    for h in hashes:
        if code and check_password_hash(h, code):
            hashes.remove(h)
            db.execute("UPDATE users SET recovery_codes = ? WHERE id = ?", (json.dumps(hashes), user["id"]))
            return True
    return False


def remaining_codes(user):
    return len(json.loads(user["recovery_codes"] or "[]"))


# ---------------------------------------------------------------- routes --
@bp.route("/login/2fa", methods=["GET", "POST"])
def login_code():
    """Second sign-in step, after a correct password."""
    from .routes import _safe_next, _too_many_attempts, LOCKOUT_MINUTES
    db = get_db()
    uid = session.get("2fa_user")
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone() if uid else None
    if not user or not user["totp_enabled"] or time.time() - session.get("2fa_at", 0) > 300:
        session.pop("2fa_user", None)
        flash("Please sign in again.", "warning")
        return redirect(url_for("main.login"))
    if request.method == "POST":
        if _too_many_attempts(db, user["username"]):
            flash(f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.", "error")
            return render_template("twofactor_login.html"), 429
        code = request.form.get("code", "")
        step = verify(user["totp_secret"], code, user["totp_last_step"])
        ok = step is not None
        if ok:
            db.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, user["id"]))
        elif use_recovery_code(db, user, code):
            ok = True
            flash(f"Recovery code used - {remaining_codes(db.execute('SELECT * FROM users WHERE id = ?', (user['id'],)).fetchone())} left. "
                  "Create new ones under Two-factor sign-in if you are running low.", "warning")
        if ok:
            from .routes import start_session
            nxt, keep = session.get("2fa_next"), session.get("2fa_keep")
            start_session(user, keep)
            db.execute("DELETE FROM login_attempts WHERE username = ?", (user["username"],))
            dbmod.audit(db, user["username"], "login", f"{request.remote_addr or ''} (two-factor)")
            db.commit()
            if user["role"] == "employee":
                return redirect(url_for("portal.my_attendance"))
            return redirect(_safe_next(nxt))
        db.execute("INSERT INTO login_attempts (username, ip) VALUES (?, ?)", (user["username"], request.remote_addr))
        db.commit()
        flash("That code is not right. Use the current 6-digit code from your app, or a recovery code.", "error")
    return render_template("twofactor_login.html")


@bp.route("/account/2fa", methods=["GET", "POST"])
@login_required
def manage():
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    codes = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "start" and not user["totp_enabled"]:
            session["2fa_pending_secret"] = new_secret()
        elif action == "confirm" and not user["totp_enabled"]:
            secret = session.get("2fa_pending_secret")
            step = verify(secret, request.form.get("code"))
            if step is None:
                flash("That code did not match. Check the time on your phone is correct and try again.", "error")
            else:
                codes, hashes = new_recovery_codes()
                db.execute("UPDATE users SET totp_secret = ?, totp_enabled = 1, totp_last_step = ?, "
                           "recovery_codes = ? WHERE id = ?", (secret, step, hashes, user["id"]))
                dbmod.audit(db, user["username"], "user.2fa_on", "")
                db.commit()
                session.pop("2fa_pending_secret", None)
                flash("Two-factor sign-in is on. Save the recovery codes below somewhere safe.", "success")
                user = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
                return render_template("twofactor.html", user_row=user, codes=codes, remaining=remaining_codes(user),
                                       required=_required(user))
        elif action in ("disable", "new_codes") and user["totp_enabled"]:
            if not check_password_hash(user["password_hash"], request.form.get("password", "")):
                flash("Your password is not right.", "error")
            elif (step := verify(user["totp_secret"], request.form.get("code"), user["totp_last_step"])) is None:
                flash("That code is not right.", "error")
            elif action == "disable":
                if _required(user):
                    flash("Administrators are required to use two-factor sign-in (Settings).", "error")
                else:
                    db.execute("UPDATE users SET totp_enabled = 0, totp_secret = NULL, recovery_codes = NULL, "
                               "totp_last_step = NULL WHERE id = ?", (user["id"],))
                    dbmod.audit(db, user["username"], "user.2fa_off", "")
                    db.commit()
                    flash("Two-factor sign-in is off.", "success")
            else:
                codes, hashes = new_recovery_codes()
                db.execute("UPDATE users SET recovery_codes = ?, totp_last_step = ? WHERE id = ?",
                           (hashes, step, user["id"]))  # the code just used cannot be used again
                dbmod.audit(db, user["username"], "user.2fa_codes", "new recovery codes")
                db.commit()
                user = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
                return render_template("twofactor.html", user_row=user, codes=codes, remaining=remaining_codes(user),
                                       required=_required(user))
        return redirect(url_for("twofactor.manage"))
    pending = session.get("2fa_pending_secret") if not user["totp_enabled"] else None
    issuer = dbmod.get_setting(db, "company_name") or "Attendance"
    qr = qr_svg(provisioning_uri(pending, user["username"], issuer)) if pending else None
    return render_template("twofactor.html", user_row=user, pending=pending, qr=qr, codes=codes,
                           remaining=remaining_codes(user), required=_required(user))


def _required(user):
    return user["role"] == "admin" and dbmod.get_setting(get_db(), "require_2fa_admins") == "1"


def setup_required(user):
    """True when this signed-in user must set up two-factor before doing anything else."""
    return user is not None and user["role"] == "admin" and not user["totp_enabled"] and \
        dbmod.get_setting(get_db(), "require_2fa_admins") == "1"
