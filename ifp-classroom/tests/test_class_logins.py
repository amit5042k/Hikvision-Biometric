"""One login per classroom: panel app sign-in and class-only reports."""
import json

import pytest
from werkzeug.security import generate_password_hash

from conftest import login_as


@pytest.fixture
def two_rooms(conn):
    conn.execute("INSERT INTO classrooms (name, kiosk_token) VALUES ('8-A', 'tok8a')")
    conn.execute("INSERT INTO classrooms (name, kiosk_token) VALUES ('9-B', 'tok9b')")
    conn.execute("INSERT INTO class_persons (person_no, name, class_name, classroom_id) VALUES ('S1', 'Asha', '8-A', 1)")
    conn.execute("INSERT INTO class_persons (person_no, name, class_name, classroom_id) VALUES ('S2', 'Zoya', '9-B', 2)")
    conn.execute("INSERT INTO class_persons (person_no, name, role) VALUES ('T1', 'Mrs Iyer', 'teacher')")
    for pid, room in ((1, 1), (2, 2), (3, 1)):
        conn.execute("INSERT INTO class_sightings (classroom_id, person_id, seen_at) VALUES (?, ?, datetime('now', 'localtime'))",
                     (room, pid))
    conn.execute("INSERT INTO users (username, password_hash, role, classroom_id) VALUES ('class8a', ?, 'class', 1)",
                 (generate_password_hash("secret8a"),))
    conn.commit()
    return conn


def test_admin_creates_and_updates_class_login(logged_in, conn):
    t = logged_in.csrf
    logged_in.post("/classroom/rooms", data={"name": "7-C", "csrf_token": t})
    logged_in.post("/classroom/rooms/1/login", data={"username": "class7c", "password": "short", "csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM users WHERE role = 'class'").fetchone()[0] == 0
    logged_in.post("/classroom/rooms/1/login", data={"username": "class7c", "password": "secret77", "csrf_token": t})
    row = conn.execute("SELECT username, classroom_id FROM users WHERE role = 'class'").fetchone()
    assert tuple(row) == ("class7c", 1)
    logged_in.post("/classroom/rooms/1/login", data={"username": "room7c", "password": "", "csrf_token": t})
    assert conn.execute("SELECT username FROM users WHERE role = 'class'").fetchone()[0] == "room7c"
    assert conn.execute("SELECT COUNT(*) FROM users WHERE role = 'class'").fetchone()[0] == 1   # still one login
    page = logged_in.post("/classroom/rooms/1/login", data={"username": "admin", "password": "", "csrf_token": t},
                          follow_redirects=True).get_data(as_text=True)
    assert "already used" in page
    assert "room7c" in logged_in.get("/classroom").get_data(as_text=True)


def test_panel_login_returns_kiosk_link(client, two_rooms):
    r = client.post("/api/panel/login", json={"username": "class8a", "password": "secret8a"})
    data = r.get_json()
    assert r.status_code == 200 and data["ok"] and data["classroom"]["name"] == "8-A"
    assert data["kiosk_path"] == "/kiosk/tok8a"
    assert client.get("/api/class/tok8a/roster").status_code == 200


def test_panel_login_rejects_wrong_or_non_class_logins(client, two_rooms):
    assert client.post("/api/panel/login", json={"username": "class8a", "password": "nope"}).status_code == 401
    r = client.post("/api/panel/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 403 and "classroom's own login" in r.get_json()["error"]
    two_rooms.execute("UPDATE classrooms SET enabled = 0 WHERE id = 1")
    two_rooms.commit()
    assert client.post("/api/panel/login", json={"username": "class8a", "password": "secret8a"}).status_code == 403


def test_panel_login_lockout(client, two_rooms):
    for _ in range(10):
        client.post("/api/panel/login", json={"username": "class8a", "password": "bad"})
    assert client.post("/api/panel/login", json={"username": "class8a", "password": "secret8a"}).status_code == 429


def test_class_login_sees_only_its_room(app, two_rooms):
    c = app.test_client()
    assert login_as(c, "class8a", "secret8a").status_code == 302
    assert c.get("/").status_code == 302 and c.get("/classroom").headers["Location"].endswith("/classroom/register")
    reg = c.get("/classroom/register?classroom=2").get_data(as_text=True)
    assert "8-A" in reg and "Asha" in reg and "Zoya" not in reg and "9-B" not in reg
    mv = c.get("/classroom/movements?classroom=2").get_data(as_text=True)
    assert "Asha" in mv and "Mrs Iyer" in mv and "Zoya" not in mv
    assert "Zoya" not in c.get("/classroom/movements?export=1").get_data(as_text=True)
    assert "Zoya" not in c.get("/classroom/monthly?classroom=2").get_data(as_text=True)
    an = c.get("/classroom/analytics").get_data(as_text=True)
    assert "Asha" in an and "Zoya" not in an and "Mrs Iyer" in an and "9-B" not in an
    assert "Zoya" not in c.get("/classroom/analytics?export=students").get_data(as_text=True)
    assert c.get("/classroom/people/1").status_code == 200
    assert c.get("/classroom/people/3").status_code == 200       # teacher seen in this room
    assert c.get("/classroom/people/2").status_code == 404       # another class's student


def test_class_login_cannot_reach_admin_pages(app, two_rooms):
    c = app.test_client()
    login_as(c, "class8a", "secret8a")
    for url in ("/classroom/people", "/classroom/enroll", "/holidays", "/audit", "/settings/backup",
                "/classroom/people/export.csv"):
        resp = c.get(url)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/classroom/register"), url
    assert c.post("/classroom/rooms", data={"name": "X", "csrf_token": c.csrf}).status_code == 403
    assert c.post("/classroom/people/1/faces", data={"descriptor": json.dumps([0.0] * 128),
                                                     "csrf_token": c.csrf}).status_code == 403
    # but may change its own password
    c.post("/settings", data={"form": "password", "current_password": "secret8a", "new_password": "newpass8",
                              "confirm_password": "newpass8", "csrf_token": c.csrf})
    assert login_as(app.test_client(), "class8a", "newpass8").status_code == 302


def test_class_logins_listed_in_settings_but_role_fixed(logged_in, two_rooms):
    page = logged_in.get("/settings").get_data(as_text=True)
    assert "class8a" in page and "Classroom" in page
    uid = two_rooms.execute("SELECT id FROM users WHERE username = 'class8a'").fetchone()[0]
    logged_in.post("/settings", data={"form": "set_role", "user_id": uid, "role": "admin", "csrf_token": logged_in.csrf})
    assert two_rooms.execute("SELECT role FROM users WHERE id = ?", (uid,)).fetchone()[0] == "class"


def test_deleting_room_removes_its_login(logged_in, two_rooms):
    logged_in.post("/classroom/rooms/1/delete", data={"csrf_token": logged_in.csrf})
    assert two_rooms.execute("SELECT COUNT(*) FROM users WHERE username = 'class8a'").fetchone()[0] == 0
