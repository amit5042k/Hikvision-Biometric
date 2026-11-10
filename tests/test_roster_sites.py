"""Shift roster / rotation and multiple offices."""
from datetime import date, datetime

import pytest

from app import attendance as att
from app import db as dbmod
from app.sync import record_punch

NOW = datetime(2026, 11, 30, 12, 0)


@pytest.fixture
def setup(conn):
    conn.execute("UPDATE shifts SET name='Day', start_time='09:00', end_time='18:00', full_day_hours=8, "
                 "half_day_hours=4, weekly_off='6'")
    conn.execute("INSERT INTO shifts (name, start_time, end_time, full_day_hours, half_day_hours, weekly_off) "
                 "VALUES ('Night', '21:00', '07:00', 8, 4, '5')")
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('7', 'Asha', 1)")
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('8', 'Ravi', 1)")
    conn.commit()


def days_of(conn, start, end, emp_id=1):
    (_, days, s), = att.compute(conn, start, end, att.load_employees(conn, employee_id=emp_id), now=NOW)
    return days, s


def test_roster_overrides_default_shift(conn, setup):
    conn.execute("INSERT INTO roster VALUES (1, '2026-11-10', 2)")  # Tuesday on nights
    record_punch(conn, "7", "2026-11-10 21:05:00")
    record_punch(conn, "7", "2026-11-11 07:02:00")
    conn.commit()
    days, _ = days_of(conn, date(2026, 11, 10), date(2026, 11, 10))
    d = days[0]
    assert d.status == "P" and d.shift_name == "Night" and d.late_minutes == 0
    assert d.first_in == datetime(2026, 11, 10, 21, 5) and d.last_out == datetime(2026, 11, 11, 7, 2)


def test_night_then_day_shift_does_not_count_a_punch_twice(conn, setup):
    conn.execute("INSERT INTO roster VALUES (1, '2026-11-10', 2)")  # night, then default day shift on 11th
    for t in ("2026-11-10 20:55:00", "2026-11-11 07:05:00", "2026-11-11 08:55:00", "2026-11-11 18:02:00"):
        record_punch(conn, "7", t)
    conn.commit()
    days, _ = days_of(conn, date(2026, 11, 10), date(2026, 11, 11))
    night, day = days
    assert (night.first_in.hour, night.last_out.hour) == (20, 7) and night.punch_count == 2
    assert (day.first_in.hour, day.last_out.hour) == (8, 18) and day.punch_count == 2
    assert night.status == day.status == "P"


def test_rostered_rest_day_and_working_on_usual_weekly_off(conn, setup):
    conn.execute("INSERT INTO roster VALUES (1, '2026-11-10', NULL)")   # Tuesday: rest day
    conn.execute("INSERT INTO roster VALUES (1, '2026-11-15', 1)")      # Sunday: rostered to work
    conn.commit()
    days, s = days_of(conn, date(2026, 11, 10), date(2026, 11, 15))
    by_day = {d.date.day: d for d in days}
    assert by_day[10].status == "WO" and "Rest day" in by_day[10].remarks[0]
    assert by_day[15].status == "A"  # rostered Sunday with no punches is absent, not a weekly off


def test_roster_page_save_and_reset(logged_in, conn, setup):
    t = logged_in.csrf
    resp = logged_in.get("/roster?week=2026-11-11")
    assert resp.status_code == 200 and b"Week of 09 November 2026" in resp.data
    logged_in.post("/roster", data={"week": "2026-11-09", "r_1_2026-11-09": "2", "r_1_2026-11-10": "off",
                                    "r_2_2026-11-09": "", "r_1_2026-11-11": "999", "csrf_token": t})
    rows = [tuple(r) for r in conn.execute("SELECT employee_id, date, shift_id FROM roster ORDER BY date")]
    assert rows == [(1, "2026-11-09", 2), (1, "2026-11-10", None)]
    logged_in.post("/roster", data={"week": "2026-11-09", "r_1_2026-11-09": "", "csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0] == 1


def test_rotation_and_copy(logged_in, conn, setup):
    t = logged_in.csrf
    logged_in.post("/roster/rotate", data={"start": "2026-11-11", "weeks": "2", "pattern": ["1", "2"],
                                           "employee_id": ["1", "2"], "stagger": "on", "csrf_token": t})
    get = lambda e, d: conn.execute("SELECT shift_id FROM roster WHERE employee_id = ? AND date = ?", (e, d)).fetchone()
    assert get(1, "2026-11-09")[0] == 1 and get(1, "2026-11-16")[0] == 2      # Day then Night
    assert get(2, "2026-11-09")[0] == 2 and get(2, "2026-11-16")[0] == 1      # staggered
    assert get(1, "2026-11-15")[0] is None                                    # Day shift's Sunday off
    assert get(2, "2026-11-14")[0] is None                                    # Night shift's Saturday off
    logged_in.post("/roster/copy", data={"week": "2026-11-23", "csrf_token": t})
    assert get(1, "2026-11-23")[0] == 2


def test_viewer_cannot_change_roster(client, conn, setup):
    from werkzeug.security import generate_password_hash
    from tests.conftest import csrf_from
    conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('v', ?, 'viewer')", (generate_password_hash("viewer1"),))
    conn.commit()
    token = csrf_from(client.get("/login").get_data(as_text=True))
    client.post("/login", data={"username": "v", "password": "viewer1", "csrf_token": token})
    assert client.get("/roster").status_code == 200
    client.post("/roster", data={"week": "2026-11-09", "r_1_2026-11-09": "2", "csrf_token": token})
    assert conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0] == 0


# ---------------------------------------------------------------- offices --
def test_offices_filter_reports(logged_in, conn, setup):
    t = logged_in.csrf
    logged_in.post("/sites", data={"name": "Head Office", "csrf_token": t})
    logged_in.post("/sites", data={"name": "Site B", "csrf_token": t})
    logged_in.post("/bulk-shift", data={"employee_id": ["2"], "site_id": "2", "csrf_token": t})
    assert conn.execute("SELECT site_id FROM employees WHERE id = 2").fetchone()[0] == 2
    summary = logged_in.get("/reports/monthly?month=2026-11&site=2&export=summary").get_data(as_text=True)
    assert "Ravi" in summary and "Asha" not in summary
    for url in ("/sites", "/?site=2", "/employees?site=2", "/attendance?site=2", "/roster?site=2", "/devices"):
        assert logged_in.get(url).status_code == 200, url
    resp = logged_in.post("/sites", data={"name": "Site B", "csrf_token": t}, follow_redirects=True)
    assert b"already exists" in resp.data


def test_office_bridge_token_only_serves_its_own_devices(client, conn):
    conn.execute("INSERT INTO sites (name, bridge_token) VALUES ('A', 'tok-a'), ('B', 'tok-b')")
    conn.execute("INSERT INTO devices (name, host, connection, site_id) VALUES ('Gate A', '10.0.0.1', 'bridge', 1)")
    conn.execute("INSERT INTO devices (name, host, connection, site_id) VALUES ('Gate B', '10.0.0.2', 'bridge', 2)")
    conn.execute("INSERT INTO device_jobs (device_id, action, payload) VALUES (1, 'test', '{}'), (2, 'test', '{}')")
    conn.commit()
    auth = lambda tok: {"Authorization": f"Bearer {tok}"}
    work = client.get("/api/bridge/poll", headers=auth("tok-a")).get_json()
    assert [d["name"] for d in work["devices"]] == ["Gate A"] and [j["device_id"] for j in work["jobs"]] == [1]
    # B's bridge cannot upload punches for, or answer jobs of, A's terminal
    assert client.post("/api/bridge/punches", headers=auth("tok-b"), json={"device_id": 1, "events": []}).status_code == 404
    assert client.post("/api/bridge/jobs/1", headers=auth("tok-b"), json={"ok": True}).status_code == 404
    assert client.get("/api/bridge/poll", headers=auth("wrong")).status_code == 401
    main = dbmod.get_setting(conn, "bridge_token")
    assert len(client.get("/api/bridge/poll", headers=auth(main)).get_json()["devices"]) == 2


def test_office_token_management(logged_in, conn):
    t = logged_in.csrf
    logged_in.post("/sites", data={"name": "Head Office", "csrf_token": t})
    logged_in.post("/sites/1/token", data={"csrf_token": t})
    token = conn.execute("SELECT bridge_token FROM sites").fetchone()[0]
    assert token and len(token) > 20
    logged_in.post("/sites/1/token", data={"remove": "1", "csrf_token": t})
    assert conn.execute("SELECT bridge_token FROM sites").fetchone()[0] is None
    logged_in.post("/sites/1/delete", data={"csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0
