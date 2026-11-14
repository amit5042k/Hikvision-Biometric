from datetime import timedelta

from qrserver import db as dbmod

from .conftest import expire


def make(client, auth, type_="url", data=None, **extra):
    return client.post("/api/codes", headers=auth, json={"type": type_, "data": data or {"url": "example.com"}, **extra})


def test_register_starts_seven_day_trial(client, conn):
    resp = client.post("/api/register", json={"email": "New@Example.com", "password": "secret123"})
    assert resp.status_code == 201
    acct = resp.get_json()["account"]
    assert acct["email"] == "new@example.com" and acct["active"]
    until = dbmod.parse(conn.execute("SELECT access_until FROM users").fetchone()[0])
    assert timedelta(days=6, hours=23) < until - dbmod.utcnow() <= timedelta(days=7)


def test_register_validation_and_duplicates(client, auth):
    assert client.post("/api/register", json={"email": "bad", "password": "secret123"}).status_code == 400
    assert client.post("/api/register", json={"email": "a@b.co", "password": "short"}).status_code == 400
    resp = client.post("/api/register", json={"email": "USER@example.com", "password": "secret123"})
    assert resp.status_code == 400 and "already exists" in resp.get_json()["error"]


def test_login_logout(client, auth):
    assert client.post("/api/login", json={"email": "user@example.com", "password": "nope"}).status_code == 401
    resp = client.post("/api/login", json={"email": "user@example.com", "password": "secret123"})
    token = {"Authorization": "Bearer " + resp.get_json()["token"]}
    assert client.get("/api/me", headers=token).status_code == 200
    assert client.post("/api/logout", headers=token).status_code == 200
    assert client.get("/api/me", headers=token).status_code == 401
    assert client.get("/api/me", headers=auth).status_code == 200  # other sessions stay logged in


def test_api_requires_token(client):
    assert client.get("/api/codes").status_code == 401
    assert client.get("/api/codes", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_create_list_update_delete(client, auth):
    code = make(client, auth).get_json()["code"]
    assert code["data"]["url"] == "https://example.com"
    assert code["label"] == "example.com"
    assert code["short_url"] == "http://localhost/r/" + code["slug"] and len(code["slug"]) == 7
    assert [c["id"] for c in client.get("/api/codes", headers=auth).get_json()["codes"]] == [code["id"]]

    resp = client.put(f"/api/codes/{code['id']}", headers=auth,
                      json={"type": "url", "data": {"url": "https://new.example"}, "label": "Menu"})
    updated = resp.get_json()["code"]
    assert updated["slug"] == code["slug"] and updated["label"] == "Menu"
    assert client.get("/r/" + code["slug"]).headers["Location"] == "https://new.example"

    assert client.delete(f"/api/codes/{code['id']}", headers=auth).status_code == 200
    assert client.get("/r/" + code["slug"]).status_code == 404


def test_cannot_touch_other_users_codes(client, auth):
    code = make(client, auth).get_json()["code"]
    other = client.post("/api/register", json={"email": "o@example.com", "password": "secret123"}).get_json()
    h = {"Authorization": "Bearer " + other["token"]}
    assert client.get("/api/codes", headers=h).get_json()["codes"] == []
    assert client.delete(f"/api/codes/{code['id']}", headers=h).status_code == 404
    assert client.put(f"/api/codes/{code['id']}", headers=h, json={"data": {"url": "https://evil.example"}}).status_code == 404


def test_content_validation(client, auth):
    assert make(client, auth, "url", {"url": "javascript:alert(1)"}).status_code == 400
    assert make(client, auth, "url", {"url": "ftp://x.example"}).status_code == 400
    assert make(client, auth, "nope", {}).status_code == 400
    assert make(client, auth, "text", {"text": "   "}).status_code == 400
    assert make(client, auth, "wifi", {"ssid": ""}).status_code == 400
    assert make(client, auth, "email", {"emailTo": "not-an-email"}).status_code == 400
    phone = make(client, auth, "phone", {"phone": "+91 (987) 654-3210"}).get_json()["code"]
    assert phone["data"] == {"phone": "+919876543210"}
    wifi = make(client, auth, "wifi", {"ssid": "Home", "security": "bogus", "extra": "dropped"}).get_json()["code"]
    assert wifi["data"]["security"] == "WPA" and "extra" not in wifi["data"]


def test_expired_trial_blocks_creating_and_editing(client, auth, conn):
    code = make(client, auth).get_json()["code"]
    expire(conn)
    me = client.get("/api/me", headers=auth).get_json()["account"]
    assert me["active"] is False and me["contact"] == "Call 12345."
    resp = make(client, auth)
    assert resp.status_code == 403 and "trial has ended" in resp.get_json()["error"]
    assert client.put(f"/api/codes/{code['id']}", headers=auth, json={"data": {"url": "x.example"}}).status_code == 403
    assert len(client.get("/api/codes", headers=auth).get_json()["codes"]) == 1  # still listed
