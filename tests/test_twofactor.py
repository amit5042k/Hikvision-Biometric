"""Two-factor sign-in (TOTP)."""
import base64
import re
import time

import pytest

from app import twofactor as tf
from tests.conftest import csrf_from


def test_totp_matches_rfc6238_vectors():
    secret = base64.b32encode(b"12345678901234567890").decode()
    for t, expected in [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")]:
        assert tf.code_at(secret, t // 30) == expected  # RFC 6238 SHA-1 vectors, last 6 digits


def test_verify_window_and_replay():
    s = tf.new_secret()
    now = 1_700_000_000
    step = int(now // 30)
    assert tf.verify(s, tf.code_at(s, step), now=now) == step
    assert tf.verify(s, tf.code_at(s, step - 1), now=now) == step - 1          # 30 s clock drift is fine
    assert tf.verify(s, tf.code_at(s, step - 3), now=now) is None
    assert tf.verify(s, tf.code_at(s, step), last_step=step, now=now) is None   # already used
    assert tf.verify(s, "12345", now=now) is None and tf.verify(s, "abcdef", now=now) is None


def current_code(secret):
    return tf.code_at(secret, int(time.time() // 30))


def enable_2fa(client):
    client.post("/account/2fa", data={"action": "start", "csrf_token": client.csrf})
    page = client.get("/account/2fa").get_data(as_text=True)
    assert "<svg" in page
    secret = re.search(r'word-break:break-all">([A-Z2-7]+)<', page).group(1)
    resp = client.post("/account/2fa", data={"action": "confirm", "code": current_code(secret), "csrf_token": client.csrf})
    codes = re.findall(r'text-align:center">([0-9a-f]{4}-[0-9a-f]{4})<', resp.get_data(as_text=True))
    assert len(codes) == 8
    return secret, codes


def password_step(client, password="admin"):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    client.csrf = token
    return client.post("/login", data={"username": "admin", "password": password, "csrf_token": token})


def test_full_two_factor_sign_in(logged_in, conn, app):
    secret, codes = enable_2fa(logged_in)
    assert conn.execute("SELECT totp_enabled FROM users WHERE username = 'admin'").fetchone()[0] == 1

    c = app.test_client()
    resp = password_step(c)
    assert resp.headers["Location"].endswith("/login/2fa")
    assert c.get("/").status_code == 302                       # password alone does not sign in
    resp = c.post("/login/2fa", data={"code": "000000", "csrf_token": c.csrf})
    assert b"not right" in resp.data
    # the code just used to turn 2FA on cannot be replayed
    resp = c.post("/login/2fa", data={"code": current_code(secret), "csrf_token": c.csrf})
    if resp.status_code == 200:  # same 30 s step as setup: refused as a replay
        assert b"not right" in resp.data
        conn.execute("UPDATE users SET totp_last_step = totp_last_step - 2")
        conn.commit()
        resp = c.post("/login/2fa", data={"code": current_code(secret), "csrf_token": c.csrf})
    assert resp.status_code == 302 and c.get("/").status_code == 200

    # recovery code works once
    c2 = app.test_client()
    password_step(c2)
    assert c2.post("/login/2fa", data={"code": codes[0], "csrf_token": c2.csrf}).status_code == 302
    c3 = app.test_client()
    password_step(c3)
    assert c3.post("/login/2fa", data={"code": codes[0], "csrf_token": c3.csrf}).status_code == 200


def test_two_factor_step_needs_password_first(client):
    resp = client.get("/login/2fa")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")


def test_admins_can_be_required_to_use_2fa(logged_in, conn, app):
    logged_in.post("/settings", data={"form": "general", "require_2fa_admins": "on", "csrf_token": logged_in.csrf})
    assert conn.execute("SELECT value FROM settings WHERE key = 'require_2fa_admins'").fetchone()[0] == "1"
    resp = logged_in.get("/employees")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/account/2fa")
    enable_2fa(logged_in)
    assert logged_in.get("/employees").status_code == 200


def test_admin_can_reset_another_users_2fa(logged_in, conn):
    conn.execute("INSERT INTO users (username, password_hash, role, totp_enabled, totp_secret) "
                 "VALUES ('v', 'x', 'viewer', 1, 'ABC')")
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE username = 'v'").fetchone()[0]
    logged_in.post("/settings", data={"form": "reset_2fa", "user_id": uid, "csrf_token": logged_in.csrf})
    assert tuple(conn.execute("SELECT totp_enabled, totp_secret FROM users WHERE id = ?", (uid,)).fetchone()) == (0, None)


def test_disable_needs_password_and_code(logged_in, conn):
    secret, _ = enable_2fa(logged_in)
    logged_in.post("/account/2fa", data={"action": "disable", "password": "wrong", "code": "123456",
                                         "csrf_token": logged_in.csrf})
    assert conn.execute("SELECT totp_enabled FROM users WHERE username = 'admin'").fetchone()[0] == 1
    conn.execute("UPDATE users SET totp_last_step = NULL")
    conn.commit()
    logged_in.post("/account/2fa", data={"action": "disable", "password": "admin", "code": current_code(secret),
                                         "csrf_token": logged_in.csrf})
    assert conn.execute("SELECT totp_enabled FROM users WHERE username = 'admin'").fetchone()[0] == 0
