import re
import threading

import pytest
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from app import create_app
from app import db as dbmod


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db"), "SECRET_KEY": "test"})
    yield app


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


@pytest.fixture
def logged_in(client):
    page = client.get("/login").get_data(as_text=True)
    token = csrf_from(page)
    resp = client.post("/login", data={"username": "admin", "password": "admin", "csrf_token": token})
    assert resp.status_code == 302
    client.csrf = token
    return client


class FakeTerminal:
    """A tiny stand-in for a DS-K1T320 ISAPI endpoint."""

    def __init__(self):
        self.users = [{"employeeNo": str(i), "name": f"User {i}", "numOfFace": 1 if i % 2 else 0}
                      for i in range(1, 36)]
        self.events = []
        self.requests = []
        app = Flask("fake")

        @app.get("/ISAPI/System/deviceInfo")
        def info():
            return ("<DeviceInfo xmlns='http://www.isapi.org/ver20/XMLSchema'><model>DS-K1T320EFWX</model>"
                    "<serialNumber>FAKE123</serialNumber><firmwareVersion>V3.2.0</firmwareVersion>"
                    "<macAddress>aa:bb</macAddress></DeviceInfo>")

        @app.post("/ISAPI/AccessControl/UserInfo/Search")
        def users():
            cond = request.get_json(force=True)["UserInfoSearchCond"]
            pos, n = cond["searchResultPosition"], cond["maxResults"]
            page = self.users[pos:pos + n]
            more = pos + n < len(self.users)
            return jsonify({"UserInfoSearch": {"searchID": cond["searchID"], "numOfMatches": len(page),
                                               "totalMatches": len(self.users),
                                               "responseStatusStrg": "MORE" if more else "OK",
                                               "UserInfo": page}})

        @app.post("/ISAPI/AccessControl/AcsEvent")
        def events():
            cond = request.get_json(force=True)["AcsEventCond"]
            self.requests.append(cond)
            matching = [e for e in self.events if cond["minor"] in (0, e["minor"])]
            pos, n = cond["searchResultPosition"], cond["maxResults"]
            page = matching[pos:pos + n]
            more = pos + n < len(matching)
            return jsonify({"AcsEvent": {"searchID": cond["searchID"], "numOfMatches": len(page),
                                         "totalMatches": len(matching),
                                         "responseStatusStrg": "MORE" if more else ("OK" if page else "NO MATCH"),
                                         "InfoList": page}})

        @app.post("/ISAPI/AccessControl/UserInfo/Record")
        def add_user():
            user = request.get_json(force=True)["UserInfo"]
            if any(u["employeeNo"] == user["employeeNo"] for u in self.users):
                return jsonify({"statusCode": 6, "statusString": "Invalid Content",
                                "subStatusCode": "employeeNoAlreadyExist"}), 400
            self.users.append(user)
            return jsonify({"statusCode": 1, "statusString": "OK"})

        @app.put("/ISAPI/AccessControl/UserInfo/Modify")
        def modify_user():
            user = request.get_json(force=True)["UserInfo"]
            for u in self.users:
                if u["employeeNo"] == user["employeeNo"]:
                    u.update(user)
            return jsonify({"statusCode": 1, "statusString": "OK"})

        @app.put("/ISAPI/AccessControl/UserInfoDetail/Delete")
        def delete_user():
            nos = {e["employeeNo"] for e in request.get_json(force=True)["UserInfoDetail"]["EmployeeNoList"]}
            self.users = [u for u in self.users if u["employeeNo"] not in nos]
            return jsonify({"statusCode": 1, "statusString": "OK"})

        @app.put("/ISAPI/System/time")
        def set_time():
            return "<ResponseStatus><statusCode>1</statusCode></ResponseStatus>"

        self.server = make_server("127.0.0.1", 0, app)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def add_event(self, emp, when, minor=75, serial=None):
        self.events.append({"major": 5, "minor": minor, "time": when, "employeeNoString": emp,
                            "name": f"User {emp}", "serialNo": serial or len(self.events) + 1,
                            "currentVerifyMode": "face"})

    def close(self):
        self.server.shutdown()


@pytest.fixture
def terminal():
    t = FakeTerminal()
    yield t
    t.close()


@pytest.fixture
def device_row(conn, terminal):
    conn.execute("INSERT INTO devices (name, host, port, username, password) VALUES ('Gate', '127.0.0.1', ?, 'admin', 'x')",
                 (terminal.port,))
    conn.commit()
    return conn.execute("SELECT * FROM devices").fetchone()
