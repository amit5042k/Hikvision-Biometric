"""Regression tests for bugs found in review."""
import io
import json
from datetime import date, datetime

from app import attendance as att
from app.sync import record_punch, store_events
from tests.conftest import csrf_from

NOW = datetime(2026, 10, 13, 20, 0)


def emp(conn, start="09:00", end="18:00", weekly_off="6"):
    conn.execute("UPDATE shifts SET start_time = ?, end_time = ?, grace_in_min = 10, grace_out_min = 10, "
                 "half_day_hours = 4, full_day_hours = 8, weekly_off = ?", (start, end, weekly_off))
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('7', 'Asha', 1)")
    conn.commit()


def one_day(conn, d, now=NOW):
    (_, days, summary), = att.compute(conn, d, d, now=now)
    return days[0], summary


# ---------------------------------------------------------- attendance ----
def test_leave_is_not_charged_on_holidays_or_weekly_offs(conn):
    emp(conn)
    conn.execute("INSERT INTO holidays (date, name) VALUES ('2026-10-02', 'Gandhi Jayanti')")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date) VALUES (1, '2026-10-01', '2026-10-05')")
    conn.commit()
    (_, days, s), = att.compute(conn, date(2026, 10, 1), date(2026, 10, 5), now=NOW)
    assert [d.status for d in days] == ["L", "H", "L", "WO", "L"]  # 4th is a Sunday
    assert s.leave == 3 and s.payable == 5


def test_half_day_leave_without_punches_is_half_leave_half_absent(conn):
    emp(conn)
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, half_day) VALUES (1, '2026-10-12', '2026-10-12', 1)")
    conn.commit()
    d, s = one_day(conn, date(2026, 10, 12))
    assert d.status == "L" and d.leave == 0.5
    assert s.leave == 0.5 and s.absent == 0.5 and s.payable == 0.5


def test_half_day_leave_plus_half_day_work_is_paid_in_full(conn):
    emp(conn)
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, half_day) VALUES (1, '2026-10-12', '2026-10-12', 1)")
    record_punch(conn, "7", "2026-10-12 13:00:00")
    record_punch(conn, "7", "2026-10-12 18:00:00")
    conn.commit()
    d, s = one_day(conn, date(2026, 10, 12))
    assert d.status == "HD" and s.present == 0.5 and s.leave == 0.5 and s.payable == 1


def test_short_visit_on_full_day_leave_stays_leave(conn):
    emp(conn)
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date) VALUES (1, '2026-10-12', '2026-10-12')")
    record_punch(conn, "7", "2026-10-12 11:00:00")
    record_punch(conn, "7", "2026-10-12 11:30:00")
    conn.commit()
    d, s = one_day(conn, date(2026, 10, 12))
    assert d.status == "L" and s.leave == 1 and s.absent == 0


def test_night_shift_after_midnight_is_still_in_progress(conn):
    emp(conn, "21:00", "07:00", "")
    record_punch(conn, "7", "2026-10-12 20:55:00")
    conn.commit()
    after_midnight = datetime(2026, 10, 13, 2, 0)
    d, _ = one_day(conn, date(2026, 10, 12), now=after_midnight)
    assert d.status == "P" and d.remarks == ["In"]  # was "MP" as soon as the date changed


def test_night_shift_not_absent_before_it_ends(conn):
    emp(conn, "21:00", "07:00", "")
    d, _ = one_day(conn, date(2026, 10, 12), now=datetime(2026, 10, 13, 2, 0))
    assert d.status == "-"  # was "A" while the shift was still running


def test_bad_shift_time_or_setting_does_not_break_reports(conn):
    emp(conn)
    conn.execute("UPDATE shifts SET start_time = '9', end_time = 'x'")
    conn.execute("UPDATE settings SET value = 'abc' WHERE key IN ('window_before_hours', 'ot_min_minutes')")
    conn.execute("INSERT INTO punches (employee_no, punch_time) VALUES ('7', 'garbage')")
    conn.commit()
    d, _ = one_day(conn, date(2026, 10, 12))
    assert d.status == "A"


def test_leavers_stay_in_reports_for_months_they_worked(logged_in, conn):
    emp(conn)
    record_punch(conn, "7", "2026-09-15 09:00:00")
    conn.execute("UPDATE employees SET active = 0")
    conn.commit()
    assert "Asha" in logged_in.get("/reports/monthly?month=2026-09&export=summary").get_data(as_text=True)
    assert "Asha" not in logged_in.get("/reports/monthly?month=2026-11&export=summary").get_data(as_text=True)


# --------------------------------------------------------------- web ------
def test_invalid_month_does_not_crash(logged_in, conn):
    emp(conn)
    for month in ("2026-13", "2026-00", "abc", "2026"):
        assert logged_in.get(f"/reports/monthly?month={month}").status_code == 200
        assert logged_in.get(f"/employees/1?month={month}").status_code == 200


def test_login_rejects_backslash_open_redirect(client):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login?next=/%5Cevil.com", data={"username": "admin", "password": "admin", "csrf_token": token})
    assert resp.headers["Location"] == "/"


def test_login_locks_out_after_repeated_failures(client):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    for _ in range(5):
        client.post("/login", data={"username": "admin", "password": "wrong", "csrf_token": token})
    resp = client.post("/login", data={"username": "admin", "password": "admin", "csrf_token": token})
    assert resp.status_code == 429 and b"Too many failed" in resp.data


def test_push_without_token_only_from_registered_terminal(client, conn):
    event = {"dateTime": "2026-10-12T09:03:00+05:30",
             "AccessControllerEvent": {"majorEventType": 5, "subEventType": 75, "employeeNoString": "8"}}
    assert client.post("/api/hik/event", json=event).status_code == 403  # anyone on the internet
    conn.execute("INSERT INTO devices (name, host) VALUES ('Gate', '127.0.0.1')")  # test client address
    conn.commit()
    assert client.post("/api/hik/event", json=event).get_json()["added"] == 1


def test_push_with_bad_values_is_ignored(client, conn):
    conn.execute("INSERT INTO devices (name, host) VALUES ('Gate', '127.0.0.1')")
    conn.commit()
    for bad in ({"dateTime": "nonsense", "AccessControllerEvent": {"employeeNoString": "8"}},
                {"dateTime": "2026-10-12T09:03:00+05:30",
                 "AccessControllerEvent": {"employeeNoString": "8", "subEventType": "x"}}):
        assert client.post("/api/hik/event", json=bad).status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 0


def test_csv_export_neutralises_formulas(logged_in, conn):
    conn.execute("INSERT INTO employees (employee_no, name) VALUES ('1', '=HYPERLINK(\"http://x\")')")
    conn.commit()
    text = logged_in.get("/employees/export.csv").get_data(as_text=True)
    assert "'=HYPERLINK" in text


def test_csv_import_accepts_excel_encoding_and_skips_bad_rows(logged_in, conn):
    data = "employee_no,name,join_date\n1,José,2026-01-05\nA B,Bad,\n2,Ravi,31/12/2025\n".encode("cp1252")
    resp = logged_in.post("/employees/import", data={"file": (io.BytesIO(data), "e.csv"), "csrf_token": logged_in.csrf},
                          content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200 and b"Skipped rows 3" in resp.data
    rows = [tuple(r) for r in conn.execute("SELECT employee_no, name, join_date FROM employees ORDER BY employee_no")]
    assert rows == [("1", "José", "2026-01-05"), ("2", "Ravi", None)]


def test_leave_for_unknown_employee_is_rejected(logged_in, conn):
    resp = logged_in.post("/leaves", data={"employee_id": "999", "start_date": "2026-10-01", "end_date": "2026-10-01",
                                           "csrf_token": logged_in.csrf})
    assert resp.status_code == 302
    assert conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0] == 0


def test_shift_and_settings_validation(logged_in, conn):
    t = logged_in.csrf
    logged_in.post("/shifts", data={"name": "Bad", "start_time": "9", "end_time": "18:00", "csrf_token": t})
    logged_in.post("/shifts", data={"name": "Bad2", "start_time": "09:00", "end_time": "18:00",
                                    "half_day_hours": "x", "full_day_hours": "8", "csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM shifts WHERE name LIKE 'Bad%'").fetchone()[0] == 1  # x -> 0 is fine
    logged_in.post("/settings", data={"form": "general", "window_before_hours": "abc", "csrf_token": t})
    assert conn.execute("SELECT value FROM settings WHERE key = 'window_before_hours'").fetchone()[0] == "4"
    resp = logged_in.post("/shifts", data={"name": "General", "start_time": "09:00", "end_time": "18:00",
                                           "csrf_token": t}, follow_redirects=True)
    assert b"already exists" in resp.data


def test_employee_number_used_by_terminal_cannot_change(logged_in, conn):
    conn.execute("INSERT INTO employees (employee_no, name) VALUES ('5', 'Asha')")
    record_punch(conn, "5", "2026-10-12 09:00:00", source="device")
    conn.commit()
    logged_in.post("/employees/1/edit", data={"employee_no": "6", "name": "Asha", "csrf_token": logged_in.csrf})
    assert conn.execute("SELECT employee_no FROM employees").fetchone()[0] == "5"


def test_duplicate_card_number_rejected(logged_in, conn):
    conn.execute("INSERT INTO employees (employee_no, name, card_no) VALUES ('5', 'Asha', '123')")
    conn.commit()
    logged_in.post("/employees/new", data={"employee_no": "6", "name": "Ravi", "card_no": "123",
                                           "csrf_token": logged_in.csrf})
    assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 1


# --------------------------------------------------------------- bridge ---
def test_bridge_garbage_times_are_not_stored(conn):
    conn.execute("INSERT INTO devices (name, host) VALUES ('Gate', 'x')")
    dev = conn.execute("SELECT * FROM devices").fetchone()
    res = store_events(conn, dev, [{"employee_no": "1", "time": "yesterday"},
                                   {"employee_no": "1", "time": "2026-10-12 09:00:00"}])
    assert res == {"seen": 2, "added": 1}


def test_stale_open_door_job_expires_instead_of_running_late(client, conn):
    conn.execute("INSERT INTO devices (name, host, connection) VALUES ('Gate', 'x', 'bridge')")
    conn.execute("INSERT INTO device_jobs (device_id, action, created_at) "
                 "VALUES (1, 'open-door', datetime('now','localtime','-3 hours'))")
    conn.execute("INSERT INTO device_jobs (device_id, action) VALUES (1, 'open-door')")
    conn.commit()
    token = conn.execute("SELECT value FROM settings WHERE key = 'bridge_token'").fetchone()[0]
    jobs = client.get("/api/bridge/poll", headers={"Authorization": f"Bearer {token}"}).get_json()["jobs"]
    assert [j["id"] for j in jobs] == [2]
    old = conn.execute("SELECT status, result FROM device_jobs WHERE id = 1").fetchone()
    assert old["status"] == "failed" and "Expired" in old["result"]


def test_job_timeout_counts_from_pickup_not_queue_time(client, conn):
    conn.execute("INSERT INTO devices (name, host, connection) VALUES ('Gate', 'x', 'bridge')")
    conn.execute("INSERT INTO device_jobs (device_id, action, payload, created_at) "
                 "VALUES (1, 'sync-users', '{}', datetime('now','localtime','-1 hour'))")
    conn.commit()
    token = conn.execute("SELECT value FROM settings WHERE key = 'bridge_token'").fetchone()[0]
    auth = {"Authorization": f"Bearer {token}"}
    assert len(client.get("/api/bridge/poll", headers=auth).get_json()["jobs"]) == 1
    client.get("/api/bridge/poll", headers=auth)  # next poll while the bridge is still working on it
    assert conn.execute("SELECT status FROM device_jobs").fetchone()[0] == "sent"  # was "failed"
    client.post("/api/bridge/jobs/1", headers=auth, data=json.dumps({"ok": True, "data": {"users": []}}))
    assert conn.execute("SELECT status FROM device_jobs").fetchone()[0] == "done"
