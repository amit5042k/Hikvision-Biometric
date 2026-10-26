import re

import pytest

from qrserver import create_app, create_user
from qrserver import db as dbmod


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "DATABASE": str(tmp_path / "test.db"),
                       "SECRET_KEY": "test", "CONTACT_TEXT": "Call 12345."})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    c = dbmod.connect(app.config["DATABASE"])
    yield c
    c.close()


@pytest.fixture
def auth(client):
    """Registers a user and returns request headers carrying their token."""
    resp = client.post("/api/register", json={"email": "user@example.com", "password": "secret123", "name": "U"})
    assert resp.status_code == 201
    return {"Authorization": "Bearer " + resp.get_json()["token"]}


@pytest.fixture
def admin_client(app, client, conn):
    create_user(conn, "boss@example.com", "adminpass", is_admin=True)
    page = client.get("/admin/login").get_data(as_text=True)
    client.post("/admin/login", data={"email": "boss@example.com", "password": "adminpass",
                                      "csrf_token": csrf_from(page)})
    return client


def csrf_from(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def expire(conn, email="user@example.com"):
    conn.execute("UPDATE users SET access_until = '2000-01-01 00:00:00' WHERE email = ?", (email,))
    conn.commit()
