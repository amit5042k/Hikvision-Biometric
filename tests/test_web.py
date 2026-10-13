import json

from app.sync import record_punch


def test_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_bad_login(client):
    from tests.conftest import csrf_from
    token = csrf_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login", data={"username": "admin", "password": "nope", "csrf_token": token})
    assert resp.status_code == 200 and b"Invalid username or password" in resp.data


def test_csrf_enforced(logged_in):
    resp = logged_in.post("/holidays", data={"date": "2026-01-26", "name": "Republic Day"})
    assert resp.status_code == 400


def test_all_pages_render(logged_in, conn):
    conn.execute("INSERT INTO departments (name) VALUES ('Sales')")
    conn.execute("INSERT INTO employees (employee_no, name, shift_id, department_id) VALUES ('1', 'Asha', 1, 1)")
    conn.execute("INSERT INTO devices (name, host) VALUES ('Gate', '10.0.0.5')")
    conn.execute("INSERT INTO holidays (date, name) VALUES ('2026-10-20', 'Diwali')")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date) VALUES (1, '2026-10-01', '2026-10-01')")
    record_punch(conn, "1", "2026-10-12 09:00:00")
    conn.commit()
    for url in ["/", "/employees", "/employees/new", "/employees/1", "/employees/1/edit", "/devices",
                "/devices/new", "/devices/1/edit", "/attendance", "/attendance?date=2026-10-12&status=MP",
                "/punches?start=2026-10-01&end=2026-10-13", "/reports/monthly?month=2026-10",
                "/reports/monthly?month=2026-10&view=summary", "/leaves", "/holidays?year=2026", "/shifts",
                "/shifts?edit=1", "/settings", "/employees/1?month=2026-10"]:
        resp = logged_in.get(url)
        assert resp.status_code == 200, url


def test_csv_exports(logged_in, conn):
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('1', 'Asha', 1)")
    record_punch(conn, "1", "2026-10-12 09:00:00")
    record_punch(conn, "1", "2026-10-12 18:00:00")
    conn.commit()
    resp = logged_in.get("/reports/monthly?month=2026-10&export=summary")
    assert resp.mimetype == "text/csv" and "Asha" in resp.get_data(as_text=True)
    muster = logged_in.get("/reports/monthly?month=2026-10&export=muster").get_data(as_text=True)
    assert muster.splitlines()[1].split(",")[3 + 11] == "P"  # 12 Oct
    daily = logged_in.get("/attendance?date=2026-10-12&export=1").get_data(as_text=True)
    assert "09:00" in daily and "18:00" in daily
    assert "2026-10-12 09:00:00" in logged_in.get("/punches?start=2026-10-12&end=2026-10-12&export=1").get_data(as_text=True)


def test_employee_crud_and_manual_punch(logged_in, conn):
    t = logged_in.csrf
    resp = logged_in.post("/employees/new", data={"employee_no": "42", "name": "Meera", "shift_id": "1",
                                                  "active": "on", "csrf_token": t})
    assert resp.status_code == 302
    emp_id = conn.execute("SELECT id FROM employees WHERE employee_no = '42'").fetchone()[0]
    logged_in.post("/punches/add", data={"employee_id": emp_id, "punch_time": "2026-10-12T09:00",
                                         "note": "forgot", "csrf_token": t})
    row = conn.execute("SELECT * FROM punches WHERE employee_no = '42'").fetchone()
    assert row["source"] == "manual" and "forgot" in row["note"]
    # renumbering carries punches along
    logged_in.post(f"/employees/{emp_id}/edit", data={"employee_no": "43", "name": "Meera", "csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM punches WHERE employee_no = '43'").fetchone()[0] == 1
    # duplicate number rejected
    logged_in.post("/employees/new", data={"employee_no": "43", "name": "Dup", "csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 1
    # deactivate keeps history
    logged_in.post(f"/employees/{emp_id}/delete", data={"keep_history": "on", "csrf_token": t})
    assert conn.execute("SELECT active FROM employees").fetchone()[0] == 0


def test_csv_import(logged_in, conn):
    import io
    data = {"file": (io.BytesIO(b"employee_no,name,department\n1,Asha,Sales\n2,Ravi,\n"), "e.csv"),
            "csrf_token": logged_in.csrf}
    logged_in.post("/employees/import", data=data, content_type="multipart/form-data")
    rows = conn.execute("SELECT e.name, d.name FROM employees e LEFT JOIN departments d ON d.id = e.department_id "
                        "ORDER BY e.employee_no").fetchall()
    assert [tuple(r) for r in rows] == [("Asha", "Sales"), ("Ravi", None)]


def test_device_actions_against_fake_terminal(logged_in, conn, device_row, terminal):
    t = logged_in.csrf
    terminal.add_event("3", "2026-10-12T09:00:00+05:30")
    logged_in.post("/devices/1/test", data={"csrf_token": t})
    assert conn.execute("SELECT model FROM devices").fetchone()[0] == "DS-K1T320EFWX"
    logged_in.post("/devices/1/sync-users", data={"csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 35
    logged_in.post("/devices/1/sync-events", data={"csrf_token": t, "start": "2026-10-01", "end": "2026-10-13"})
    assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 1


def test_push_endpoint_with_token(client, conn):
    conn.execute("UPDATE settings SET value = 'sekret' WHERE key = 'push_token'")
    conn.commit()
    event = {"dateTime": "2026-10-12T09:03:00+05:30",
             "AccessControllerEvent": {"majorEventType": 5, "subEventType": 75, "employeeNoString": "8"}}
    assert client.post("/api/hik/event", json=event).status_code == 403
    # devices usually send multipart/form-data with the JSON in a form field
    resp = client.post("/api/hik/event?token=sekret", data={"event_log": json.dumps(event)})
    assert resp.get_json()["added"] == 1
    assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 1


def test_viewer_cannot_modify(client, conn):
    from werkzeug.security import generate_password_hash
    from tests.conftest import csrf_from
    conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('v', ?, 'viewer')",
                 (generate_password_hash("viewer1"),))
    conn.commit()
    token = csrf_from(client.get("/login").get_data(as_text=True))
    client.post("/login", data={"username": "v", "password": "viewer1", "csrf_token": token})
    client.post("/holidays", data={"date": "2026-01-26", "name": "X", "csrf_token": token})
    client.post("/employees/new", data={"employee_no": "1", "name": "X", "csrf_token": token})
    assert conn.execute("SELECT COUNT(*) FROM holidays").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0
    assert client.get("/reports/monthly").status_code == 200
