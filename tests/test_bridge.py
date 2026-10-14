import json
import threading

import pytest
import requests
from werkzeug.serving import make_server

from app import db as dbmod
from bridge.hik_bridge import Bridge


@pytest.fixture
def token(conn):
    return dbmod.get_setting(conn, "bridge_token")


@pytest.fixture
def bridge_device(conn, terminal):
    conn.execute("INSERT INTO devices (name, host, port, connection) VALUES ('Office', '127.0.0.1', ?, 'bridge')",
                 (terminal.port,))
    conn.commit()
    return conn.execute("SELECT * FROM devices WHERE name = 'Office'").fetchone()


@pytest.fixture
def server(app):
    """The web app on a real port, as the bridge would see it on GoDaddy."""
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def no_proxy_session():
    s = requests.Session()
    s.trust_env = False
    return s


def test_api_requires_token(client, token):
    assert client.get("/api/bridge/poll").status_code == 401
    assert client.get("/api/bridge/poll", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/bridge/poll", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_web_actions_on_bridge_device_are_queued(logged_in, conn, bridge_device, terminal):
    logged_in.post(f"/devices/{bridge_device['id']}/open-door", data={"csrf_token": logged_in.csrf})
    logged_in.post(f"/devices/{bridge_device['id']}/sync-events",
                   data={"csrf_token": logged_in.csrf, "start": "2026-10-01", "end": "2026-10-02"})
    jobs = conn.execute("SELECT action, payload, status, created_by FROM device_jobs ORDER BY id").fetchall()
    assert [(j["action"], j["status"], j["created_by"]) for j in jobs] == \
        [("open-door", "pending", "admin"), ("sync-events", "pending", "admin")]
    assert json.loads(jobs[1]["payload"]) == {"start": "2026-10-01 00:00:00", "end": "2026-10-02 23:59:59"}
    assert terminal.requests == []  # the server itself never contacted the terminal
    assert b"Recent bridge jobs" in logged_in.get("/devices").data


def test_bridge_end_to_end(server, conn, token, bridge_device, terminal):
    """Cloud server + bridge agent + terminal: punches flow up, queued jobs flow down."""
    for day in (1, 2, 3):
        terminal.add_event("5", f"2026-10-0{day}T09:00:00+05:30")
        terminal.add_event("5", f"2026-10-0{day}T18:00:00+05:30")
    conn.execute("UPDATE devices SET last_event_time = '2026-09-30 00:00:00' WHERE id = ?", (bridge_device["id"],))
    conn.execute("INSERT INTO employees (employee_no, name, card_no) VALUES ('77', 'Ravi', '12345')")
    for action, payload in [("test", {}), ("sync-users", {}),
                            ("push-employees", {"employees": [{"employee_no": "77", "name": "Ravi",
                                                               "gender": None, "card_no": None}]}),
                            ("delete-employee", {"employee_no": "3"})]:
        conn.execute("INSERT INTO device_jobs (device_id, action, payload) VALUES (?, ?, ?)",
                     (bridge_device["id"], action, json.dumps(payload)))
    conn.commit()

    bridge = Bridge(server, token, session=no_proxy_session())
    bridge.cycle()

    jobs = conn.execute("SELECT action, status, result FROM device_jobs ORDER BY id").fetchall()
    assert [j["status"] for j in jobs] == ["done"] * 4, [tuple(j) for j in jobs]
    assert "Imported users: 34 new" in jobs[1]["result"] or "Imported users" in jobs[1]["result"]
    dev = conn.execute("SELECT * FROM devices WHERE id = ?", (bridge_device["id"],)).fetchone()
    assert dev["model"] == "DS-K1T320EFWX" and dev["last_seen_at"] and dev["last_status"] == "ok"
    assert any(u["employeeNo"] == "77" for u in terminal.users)
    assert not any(u["employeeNo"] == "3" for u in terminal.users)
    # punches were pulled through the bridge on its first cycle
    assert conn.execute("SELECT COUNT(*) FROM punches WHERE employee_no = '5'").fetchone()[0] == 6
    assert dev["last_event_time"] == "2026-10-03 18:00:00"


def test_bridge_reports_unreachable_terminal(server, conn, token, bridge_device, terminal):
    terminal.close()
    conn.execute("INSERT INTO device_jobs (device_id, action, payload) VALUES (?, 'open-door', '{}')",
                 (bridge_device["id"],))
    conn.commit()
    Bridge(server, token, session=no_proxy_session()).cycle()
    job = conn.execute("SELECT status, result FROM device_jobs").fetchone()
    assert job["status"] == "failed" and "Cannot reach device" in job["result"]
    assert conn.execute("SELECT last_status FROM devices WHERE id = ?", (bridge_device["id"],)).fetchone()[0] == "error"
