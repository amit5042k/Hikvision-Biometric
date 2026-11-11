"""Regression tests for the second review round."""
import os
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

from app import db as dbmod
from tests.conftest import csrf_from


def login(client, username, password, **extra):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    client.csrf = token
    return client.post("/login", data={"username": username, "password": password, "csrf_token": token, **extra})


# ------------------------------------------------------------- time zone ---
def test_app_uses_company_time_zone_not_the_servers(app, conn):
    """Hosting servers run on UTC/US time; "today" must follow the company's zone."""
    assert os.environ.get("TZ") == "Asia/Kolkata"
    sql_now = conn.execute("SELECT datetime('now', 'localtime')").fetchone()[0]
    utc_now = conn.execute("SELECT datetime('now')").fetchone()[0]
    delta = datetime.strptime(sql_now, "%Y-%m-%d %H:%M:%S") - datetime.strptime(utc_now, "%Y-%m-%d %H:%M:%S")
    assert delta.total_seconds() == 5.5 * 3600                      # database timestamps
    assert abs((datetime.now() - datetime.strptime(sql_now, "%Y-%m-%d %H:%M:%S")).total_seconds()) < 5  # Python


def test_time_zone_setting_is_validated_and_applied(logged_in, conn):
    t = logged_in.csrf
    resp = logged_in.post("/settings", data={"form": "general", "timezone": "Mars/Olympus", "csrf_token": t},
                          follow_redirects=True)
    assert b"Time zone must be a name" in resp.data
    logged_in.post("/settings", data={"form": "general", "timezone": "Asia/Dubai", "csrf_token": t})
    logged_in.get("/settings")
    assert os.environ["TZ"] == "Asia/Dubai"
    logged_in.post("/settings", data={"form": "general", "timezone": "Asia/Kolkata", "csrf_token": t})
    logged_in.get("/settings")
    assert os.environ["TZ"] == "Asia/Kolkata"


# --------------------------------------------------------------- sessions ---
def test_password_reset_signs_out_other_sessions(logged_in, conn, app):
    t = logged_in.csrf
    logged_in.post("/settings", data={"form": "user", "username": "sam", "password": "sam12345", "role": "viewer", "csrf_token": t})
    sam = app.test_client()
    login(sam, "sam", "sam12345")
    assert sam.get("/").status_code == 200
    uid = conn.execute("SELECT id FROM users WHERE username = 'sam'").fetchone()[0]
    logged_in.post("/settings", data={"form": "reset_password", "user_id": uid, "new_password": "fresh123", "csrf_token": t})
    assert sam.get("/").status_code == 302                     # the old session is no longer valid
    assert login(app.test_client(), "sam", "fresh123").status_code == 302


def test_changing_or_resetting_own_password_keeps_this_session(logged_in, conn):
    t = logged_in.csrf
    logged_in.post("/settings", data={"form": "password", "current_password": "admin", "new_password": "newpass1",
                                      "confirm_password": "newpass1", "csrf_token": t})
    assert logged_in.get("/").status_code == 200
    me = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    logged_in.post("/settings", data={"form": "reset_password", "user_id": me, "new_password": "newpass2", "csrf_token": t})
    assert logged_in.get("/").status_code == 200


def test_deactivated_employee_cannot_use_or_start_a_session(logged_in, conn, app):
    conn.execute("INSERT INTO employees (employee_no, name) VALUES ('1', 'Asha')")
    conn.commit()
    logged_in.post("/employees/1/login", data={"username": "asha", "password": "secret1", "csrf_token": logged_in.csrf})
    asha = app.test_client()
    login(asha, "asha", "secret1")
    assert asha.get("/me/").status_code == 200
    conn.execute("UPDATE employees SET active = 0")
    conn.commit()
    assert asha.get("/me/").status_code == 302                 # signed out
    resp = login(app.test_client(), "asha", "secret1")
    assert resp.status_code == 200 and b"Invalid username or password" in resp.data


def test_keep_me_signed_in_sets_a_lasting_cookie(client):
    resp = login(client, "admin", "admin", keep="on")
    cookie = [h for h in resp.headers.getlist("Set-Cookie") if h.startswith("session=")][0]
    assert "Expires=" in cookie
    resp = login(client.application.test_client(), "admin", "admin")
    cookie = [h for h in resp.headers.getlist("Set-Cookie") if h.startswith("session=")][0]
    assert "Expires=" not in cookie


def test_failed_logins_from_elsewhere_do_not_lock_the_real_user_out(client, conn):
    for i in range(6):  # an attacker at another address keeps failing the admin's login
        conn.execute("INSERT INTO login_attempts (username, ip) VALUES ('admin', '203.0.113.9')")
    conn.commit()
    assert login(client, "admin", "admin").status_code == 302
    for i in range(25):  # ... but a flood is still stopped
        conn.execute("INSERT INTO login_attempts (username, ip) VALUES ('admin', '203.0.113.9')")
    conn.commit()
    assert login(client.application.test_client(), "admin", "admin").status_code == 429


# ----------------------------------------------------------------- others ---
def test_viewers_do_not_see_office_bridge_tokens(logged_in, conn, app):
    conn.execute("INSERT INTO sites (name, bridge_token) VALUES ('HQ', 'SECRET-TOKEN-123')")
    conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('v', ?, 'viewer')", (generate_password_hash("viewer1"),))
    conn.commit()
    assert b"SECRET-TOKEN-123" in logged_in.get("/sites").data
    v = app.test_client()
    login(v, "v", "viewer1")
    page = v.get("/sites")
    assert page.status_code == 200 and b"SECRET-TOKEN-123" not in page.data


def test_admin_cannot_record_overlapping_leave(logged_in, conn):
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('1', 'Asha', 1)")
    conn.commit()
    t = logged_in.csrf
    data = {"employee_id": "1", "start_date": "2026-12-01", "end_date": "2026-12-03", "leave_type": "Casual", "csrf_token": t}
    logged_in.post("/leaves", data=data)
    resp = logged_in.post("/leaves", data={**data, "start_date": "2026-12-03", "end_date": "2026-12-04"}, follow_redirects=True)
    assert b"already has leave" in resp.data
    assert conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0] == 1


def test_fractional_sync_settings_do_not_break_the_bridge(logged_in, client, conn):
    conn.execute("UPDATE settings SET value = '2.5' WHERE key IN ('sync_interval_min', 'initial_sync_days')")
    conn.commit()
    token = dbmod.get_setting(conn, "bridge_token")
    resp = client.get("/api/bridge/poll", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.get_json()["settings"]["sync_interval_min"] == 2
    logged_in.post("/settings", data={"form": "general", "sync_interval_min": "7.6", "csrf_token": logged_in.csrf})
    assert dbmod.get_setting(conn, "sync_interval_min") == "8"   # stored as a whole number


def test_two_factor_code_cannot_be_reused_for_new_recovery_codes(logged_in, conn):
    from tests.test_twofactor import current_code, enable_2fa
    secret, _ = enable_2fa(logged_in)
    conn.execute("UPDATE users SET totp_last_step = NULL")
    conn.commit()
    data = {"action": "new_codes", "password": "admin", "code": current_code(secret), "csrf_token": logged_in.csrf}
    first = logged_in.post("/account/2fa", data=data)
    assert b"Your recovery codes" in first.data
    again = logged_in.post("/account/2fa", data=data, follow_redirects=True)
    assert b"That code is not right" in again.data


WEIRD = ["0", "-1", "abc", "99999999999999999999", "2026-13-45", "'", "%", "<script>", ""]
PAGES = {
    "/": ["site"], "/employees": ["q", "department", "site", "status"], "/employees/1": ["month"],
    "/attendance": ["date", "department", "site", "status", "export"],
    "/reports/monthly": ["month", "department", "site", "view", "export"],
    "/punches": ["start", "end", "q", "export"], "/leaves": ["year", "status"], "/holidays": ["year"],
    "/shifts": ["edit"], "/roster": ["week", "department", "site"], "/sites": ["edit"], "/audit": ["q", "export"],
    "/requests": [], "/devices": [], "/settings": [], "/account/2fa": [],
}


@pytest.mark.parametrize("value", WEIRD)
def test_no_page_crashes_on_nonsense_url_parameters(logged_in, conn, value):
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('1', 'Asha', 1)")
    conn.commit()
    for url, params in PAGES.items():
        for p in params:
            resp = logged_in.get(url, query_string={p: value})
            assert resp.status_code in (200, 302), (url, p, value, resp.status_code)
        resp = logged_in.get(url, query_string={p: value for p in params})
        assert resp.status_code in (200, 302), (url, value)


def test_oversized_ids_in_forms_do_not_crash(logged_in, conn):
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('1', 'Asha', 1)")
    conn.commit()
    t, big = logged_in.csrf, "9" * 30
    for url, data in [("/bulk-shift", {"employee_id": [big, "1"], "shift_id": "1"}),
                      ("/roster/rotate", {"employee_id": [big], "pattern": ["1"], "start": "2026-11-09"}),
                      ("/employees/1/push", {"device_id": [big]}),
                      ("/leaves", {"employee_id": big, "start_date": "2026-12-01", "end_date": "2026-12-01"}),
                      ("/punches/add", {"employee_id": big, "punch_time": "2026-12-01T09:00"})]:
        assert logged_in.post(url, data={**data, "csrf_token": t}).status_code == 302, url
