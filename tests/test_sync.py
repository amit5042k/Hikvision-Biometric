from datetime import datetime

from app import sync
from app.hikvision import format_device_time, parse_device_time


def test_time_helpers():
    assert parse_device_time("2026-10-12T09:01:02+05:30") == "2026-10-12 09:01:02"
    assert format_device_time("2026-10-12 09:00:00", "+05:30") == "2026-10-12T09:00:00+05:30"
    assert format_device_time(datetime(2026, 1, 2, 3, 4, 5), "-03:00") == "2026-01-02T03:04:05-03:00"


def test_device_info(device_row):
    info = sync.client_for(device_row).device_info()
    assert info["model"] == "DS-K1T320EFWX" and info["serialNumber"] == "FAKE123"


def test_import_users_paginates(conn, device_row):
    res = sync.sync_users_from_device(conn, device_row)
    assert res == {"created": 35, "existing": 0}
    assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 35
    assert conn.execute("SELECT has_face FROM employees WHERE employee_no = '1'").fetchone()[0] == 1
    # a second import keeps local data and creates nothing
    conn.execute("UPDATE employees SET name = 'Renamed' WHERE employee_no = '1'")
    assert sync.sync_users_from_device(conn, device_row)["created"] == 0
    assert conn.execute("SELECT name FROM employees WHERE employee_no = '1'").fetchone()[0] == "Renamed"


def test_sync_events_paginates_and_is_incremental(conn, device_row, terminal):
    for i in range(40):
        terminal.add_event("5", f"2026-10-{1 + i // 4:02d}T{8 + i % 4:02d}:00:00+05:30")
    terminal.add_event("6", "2026-10-12T09:00:00+05:30", minor=1)  # card swipe
    terminal.add_event("", "2026-10-12T09:00:00+05:30")             # stranger, ignored

    start, end = datetime(2026, 10, 1), datetime(2026, 10, 13)
    res = sync.sync_events(conn, device_row, start, end)
    assert res == {"seen": 41, "added": 41}
    assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 41
    # unknown employees are created automatically
    assert conn.execute("SELECT name FROM employees WHERE employee_no = '6'").fetchone()[0] == "User 6"
    # the query uses device-local time with offset, one search per configured minor code
    assert {r["minor"] for r in terminal.requests} == {75, 1, 38}
    assert terminal.requests[0]["startTime"] == "2026-10-01T00:00:00+05:30"

    device = conn.execute("SELECT * FROM devices").fetchone()
    assert device["last_event_time"] == "2026-10-12 09:00:00"
    assert device["last_status"] == "ok"

    terminal.requests.clear()
    res = sync.sync_events(conn, device)  # resumes from the last event
    assert res["added"] == 0
    assert terminal.requests[0]["startTime"] == "2026-10-12T08:50:00+05:30"


def test_sync_error_is_recorded(conn, terminal, device_row):
    terminal.close()
    try:
        sync.sync_events(conn, device_row, datetime(2026, 1, 1), datetime(2026, 1, 2))
    except sync.HikvisionError:
        pass
    else:
        raise AssertionError("expected failure")
    assert conn.execute("SELECT last_status FROM devices").fetchone()[0] == "error"


def test_push_user_create_then_update(conn, device_row, terminal):
    conn.execute("INSERT INTO employees (employee_no, name) VALUES ('900', 'New Person')")
    emp = conn.execute("SELECT * FROM employees WHERE employee_no = '900'").fetchone()
    sync.push_employee(device_row, emp)
    assert any(u["employeeNo"] == "900" for u in terminal.users)
    conn.execute("UPDATE employees SET name = 'Changed' WHERE employee_no = '900'")
    emp = conn.execute("SELECT * FROM employees WHERE employee_no = '900'").fetchone()
    sync.push_employee(device_row, emp)  # already exists -> Modify
    assert [u["name"] for u in terminal.users if u["employeeNo"] == "900"] == ["Changed"]


def test_handle_push_event(conn, device_row):
    payload = {"ipAddress": "127.0.0.1", "dateTime": "2026-10-12T09:03:00+05:30", "eventType": "AccessControllerEvent",
               "AccessControllerEvent": {"majorEventType": 5, "subEventType": 75, "employeeNoString": "12",
                                         "name": "Ravi", "currentVerifyMode": "face", "serialNo": 99}}
    assert sync.handle_push_event(conn, payload)
    assert not sync.handle_push_event(conn, payload)  # duplicate
    row = conn.execute("SELECT * FROM punches").fetchone()
    assert row["punch_time"] == "2026-10-12 09:03:00" and row["source"] == "push" and row["device_id"] == device_row["id"]
    # door / alarm events without an employee are ignored
    assert not sync.handle_push_event(conn, {"dateTime": "2026-10-12T09:04:00+05:30",
                                             "AccessControllerEvent": {"majorEventType": 5, "subEventType": 21}})
