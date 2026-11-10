import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from classroom_app import create_app  # noqa: E402
from classroom_app import db as dbmod  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "DATABASE": str(tmp_path / "test.db"),
                       "BACKUP_DIR": str(tmp_path / "backups"), "SECRET_KEY": "test"})


@pytest.fixture
def conn(app):
    c = dbmod.connect(app.config["DATABASE"])
    yield c
    c.close()


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_from(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login_as(client, username, password):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login", data={"username": username, "password": password, "csrf_token": token})
    client.csrf = token
    return resp


@pytest.fixture
def logged_in(client):
    assert login_as(client, "admin", "admin").status_code == 302
    return client
