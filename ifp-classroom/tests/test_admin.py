"""Standalone app: sign-in, users, holidays, settings, audit, backup."""
from conftest import login_as


def test_login_required_and_home_redirect(client, logged_in):
    from conftest import login_as  # noqa: F401
    assert logged_in.get("/").headers["Location"].endswith("/classroom")
    assert logged_in.get("/classroom").status_code == 200


def test_anonymous_redirected_to_login(app):
    c = app.test_client()
    for url in ("/classroom", "/classroom/register", "/settings", "/holidays", "/audit"):
        resp = c.get(url)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"], url


def test_wrong_password_and_lockout(app):
    c = app.test_client()
    for _ in range(5):
        assert login_as(c, "admin", "wrong").status_code == 200
    assert login_as(c, "admin", "admin").status_code == 429


def test_csrf_required(client):
    assert client.post("/login", data={"username": "admin", "password": "admin"}).status_code == 400


def test_users_and_last_admin(logged_in, conn, app):
    t = logged_in.csrf
    logged_in.post("/settings", data={"form": "user", "username": "principal", "password": "secret1", "role": "viewer", "csrf_token": t})
    assert conn.execute("SELECT role FROM users WHERE username = 'principal'").fetchone()[0] == "viewer"
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    page = logged_in.post("/settings", data={"form": "set_role", "user_id": admin_id, "role": "viewer", "csrf_token": t},
                          follow_redirects=True).get_data(as_text=True)
    assert "At least one administrator must remain" in page
    v = app.test_client()
    assert login_as(v, "principal", "secret1").status_code == 302
    assert v.get("/classroom/register").status_code in (200, 302)
    v.post("/holidays", data={"date": "2026-11-14", "name": "X", "csrf_token": v.csrf})
    assert conn.execute("SELECT COUNT(*) FROM holidays").fetchone()[0] == 0
    assert v.get("/audit").status_code == 302


def test_school_name_holidays_audit_backup(logged_in, conn):
    t = logged_in.csrf
    logged_in.post("/settings", data={"form": "general", "school_name": "Green Valley School", "csrf_token": t})
    assert "Green Valley School" in logged_in.get("/classroom").get_data(as_text=True)
    logged_in.post("/holidays", data={"date": "2026-11-14", "name": "Children's Day", "csrf_token": t})
    assert "Children" in logged_in.get("/holidays?year=2026").get_data(as_text=True)
    assert "holiday.add" in logged_in.get("/audit?export=1").get_data(as_text=True)
    resp = logged_in.get("/settings/backup")
    assert resp.status_code == 200 and resp.data[:15] == b"SQLite format 3"


def test_change_password(logged_in, app):
    logged_in.post("/settings", data={"form": "password", "current_password": "admin", "new_password": "newpass1",
                                      "confirm_password": "newpass1", "csrf_token": logged_in.csrf})
    assert login_as(app.test_client(), "admin", "newpass1").status_code == 302
