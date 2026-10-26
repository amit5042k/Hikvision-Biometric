from qrserver import db as dbmod

from .conftest import csrf_from, expire
from .test_api import make


def _post(client, user_id, **form):
    page = client.get("/admin/").get_data(as_text=True)
    return client.post(f"/admin/users/{user_id}", data={"csrf_token": csrf_from(page), **form})


def _uid(conn, email="user@example.com"):
    return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()[0]


def test_admin_pages_need_admin_login(client, auth):
    assert client.get("/admin/").status_code == 302
    page = client.get("/admin/login").get_data(as_text=True)
    # a normal user can't log in to admin
    client.post("/admin/login", data={"email": "user@example.com", "password": "secret123", "csrf_token": csrf_from(page)})
    assert client.get("/admin/").status_code == 302


def test_admin_post_requires_csrf(admin_client, auth, conn):
    assert admin_client.post(f"/admin/users/{_uid(conn)}", data={"action": "end"}).status_code == 400


def test_extend_revives_expired_codes(admin_client, auth, conn):
    code = make(admin_client, auth).get_json()["code"]
    expire(conn)
    assert admin_client.get("/r/" + code["slug"]).status_code == 410
    assert "user@example.com" in admin_client.get("/admin/").get_data(as_text=True)

    resp = _post(admin_client, _uid(conn), action="extend", days="30")
    assert resp.status_code == 302
    until = dbmod.parse(conn.execute("SELECT access_until FROM users WHERE id = ?", (_uid(conn),)).fetchone()[0])
    assert 29 <= (until - dbmod.utcnow()).days <= 30  # counted from today, not from the old expiry
    assert admin_client.get("/r/" + code["slug"]).status_code == 302
    assert make(admin_client, auth).status_code == 201


def test_extend_adds_to_remaining_time(admin_client, auth, conn):
    _post(admin_client, _uid(conn), action="extend", days="7")
    until = dbmod.parse(conn.execute("SELECT access_until FROM users WHERE id = ?", (_uid(conn),)).fetchone()[0])
    assert 13 <= (until - dbmod.utcnow()).days <= 14


def test_end_and_disable(admin_client, auth, conn):
    code = make(admin_client, auth).get_json()["code"]
    _post(admin_client, _uid(conn), action="end")
    assert admin_client.get("/r/" + code["slug"]).status_code == 410

    _post(admin_client, _uid(conn), action="extend", days="30")
    _post(admin_client, _uid(conn), action="disable")
    assert admin_client.get("/r/" + code["slug"]).status_code == 410
    assert admin_client.get("/api/me", headers=auth).status_code == 401  # logged out everywhere
    _post(admin_client, _uid(conn), action="enable")
    assert admin_client.get("/r/" + code["slug"]).status_code == 302


def test_admin_cannot_disable_self(admin_client, conn):
    _post(admin_client, _uid(conn, "boss@example.com"), action="disable")
    assert conn.execute("SELECT disabled FROM users WHERE email = 'boss@example.com'").fetchone()[0] == 0


def test_bad_days_rejected(admin_client, auth, conn):
    before = conn.execute("SELECT access_until FROM users WHERE id = ?", (_uid(conn),)).fetchone()[0]
    _post(admin_client, _uid(conn), action="extend", days="-5")
    assert conn.execute("SELECT access_until FROM users WHERE id = ?", (_uid(conn),)).fetchone()[0] == before
