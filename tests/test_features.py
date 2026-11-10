"""Leave balances & approval, employee self-service, corrections, users, audit, Excel, email."""
import io
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from app import attendance as att
from app import leave as lv
from app import notify
from app.sync import record_punch
from tests.conftest import csrf_from


@pytest.fixture
def staff(conn):
    conn.execute("UPDATE shifts SET start_time='09:00', end_time='18:00', full_day_hours=8, half_day_hours=4, weekly_off='6'")
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('7', 'Asha Rao', 1)")
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('8', 'Ravi Kumar', 1)")
    conn.commit()


def login_as(client, username, password):
    token = csrf_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login", data={"username": username, "password": password, "csrf_token": token})
    client.csrf = token
    return resp


@pytest.fixture
def asha(logged_in, conn, staff, app):
    """Admin creates a self-service login for Asha, then a second client signs in as her."""
    logged_in.post("/employees/1/login", data={"username": "asha", "password": "secret1", "csrf_token": logged_in.csrf})
    c = app.test_client()
    resp = login_as(c, "asha", "secret1")
    assert resp.headers["Location"].endswith("/me/")
    return c


# ------------------------------------------------------------- balances ---
def test_leave_days_skip_holidays_and_weekly_offs(conn, staff):
    conn.execute("INSERT INTO holidays (date, name) VALUES ('2026-10-02', 'Gandhi Jayanti')")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type) VALUES (1, '2026-10-01', '2026-10-07', 'Casual')")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, half_day) VALUES (1, '2026-10-09', '2026-10-09', 'Sick', 1)")
    conn.commit()
    b = lv.balances(conn, 2026, [1])[1]
    assert b["Casual"]["used"] == 5  # 7 days minus a holiday and a Sunday
    assert b["Casual"]["left"] == 7 and b["Sick"]["used"] == 0.5 and b["Unpaid"]["left"] is None


def test_balance_check_counts_pending_requests(conn, staff):
    conn.execute("UPDATE leave_types SET yearly_quota = 3 WHERE name = 'Casual'")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, status) "
                 "VALUES (1, '2026-10-05', '2026-10-06', 'Casual', 'pending')")
    conn.commit()
    assert lv.check_balance(conn, 1, "Casual", date(2026, 10, 7), date(2026, 10, 7), 0) is None
    assert "only 1 remain" in lv.check_balance(conn, 1, "Casual", date(2026, 10, 7), date(2026, 10, 8), 0)


def test_unpaid_leave_is_not_payable(conn, staff):
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type) VALUES (1, '2026-10-12', '2026-10-13', 'Unpaid')")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type) VALUES (1, '2026-10-14', '2026-10-14', 'Casual')")
    conn.commit()
    (_, _, s), = att.compute(conn, date(2026, 10, 12), date(2026, 10, 14), att.load_employees(conn, employee_id=1),
                             now=datetime(2026, 10, 20))
    assert s.leave == 3 and s.unpaid_leave == 2 and s.payable == 1


def test_only_approved_leave_affects_attendance(conn, staff):
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, status) "
                 "VALUES (1, '2026-10-12', '2026-10-12', 'Casual', 'pending')")
    conn.commit()
    day = lambda: att.compute(conn, date(2026, 10, 12), date(2026, 10, 12), att.load_employees(conn, employee_id=1),
                              now=datetime(2026, 10, 20))[0][1][0].status
    assert day() == "A"
    conn.execute("UPDATE leaves SET status = 'approved'")
    conn.commit()
    assert day() == "L"


# --------------------------------------------------------- self-service ---
def test_employee_sees_only_own_pages(asha):
    assert b"Hello, Asha" in asha.get("/me/").data
    for url in ("/", "/employees", "/employees/2", "/reports/monthly", "/attendance", "/punches", "/devices",
                "/requests", "/audit", "/leaves", "/settings/backup", "/employees/export.csv"):
        resp = asha.get(url)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/me/"), url
    assert asha.post("/punches/add", data={"employee_id": "1", "punch_time": "2026-10-12T09:00",
                                           "csrf_token": asha.csrf}).status_code == 403
    assert asha.post("/settings", data={"form": "general", "company_name": "x", "csrf_token": asha.csrf}).status_code == 302
    assert b"Change your password" in asha.get("/settings").data and b"Users" not in asha.get("/settings").data


def test_employee_leave_request_flow(asha, logged_in, conn):
    t = asha.csrf
    asha.post("/me/leave", data={"leave_type": "Casual", "start_date": "2026-12-01", "end_date": "2026-12-02",
                                 "reason": "Wedding", "csrf_token": t})
    row = conn.execute("SELECT * FROM leaves").fetchone()
    assert row["status"] == "pending" and row["employee_id"] == 1 and row["requested_by"] == "asha"
    # overlapping and over-quota requests are refused
    asha.post("/me/leave", data={"leave_type": "Sick", "start_date": "2026-12-02", "end_date": "2026-12-02", "csrf_token": t})
    asha.post("/me/leave", data={"leave_type": "Casual", "start_date": "2026-11-02", "end_date": "2026-11-30", "csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0] == 1
    # admin sees it, approves it
    page = logged_in.get("/requests").get_data(as_text=True)
    assert "Asha Rao" in page and "Wedding" in page and "12 of 12 left" in page
    logged_in.post(f"/requests/leave/{row['id']}/approve", data={"note": "Enjoy", "csrf_token": logged_in.csrf})
    row = conn.execute("SELECT * FROM leaves").fetchone()
    assert row["status"] == "approved" and row["decided_by"] == "admin" and row["decision_note"] == "Enjoy"
    assert b"approved" in asha.get("/me/leave").data
    # a decided request cannot be withdrawn; a pending one can
    asha.post(f"/me/leave/{row['id']}/cancel", data={"csrf_token": t})
    assert conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0] == 1


def test_employee_cannot_touch_other_employees_requests(asha, conn):
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, status) "
                 "VALUES (2, '2026-12-01', '2026-12-01', 'Casual', 'pending')")
    conn.commit()
    asha.post("/me/leave/1/cancel", data={"csrf_token": asha.csrf})
    assert conn.execute("SELECT COUNT(*) FROM leaves").fetchone()[0] == 1
    assert b"2026-12-01" not in asha.get("/me/leave").data


def test_correction_request_approved_adds_punch(asha, logged_in, conn):
    asha.post("/me/corrections", data={"punch_time": "2026-10-12T18:05", "reason": "Forgot to punch out",
                                       "csrf_token": asha.csrf})
    req = conn.execute("SELECT * FROM punch_requests").fetchone()
    assert req["status"] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 0
    logged_in.post(f"/requests/punch/{req['id']}/approve", data={"csrf_token": logged_in.csrf})
    p = conn.execute("SELECT * FROM punches").fetchone()
    assert p["employee_no"] == "7" and p["punch_time"] == "2026-10-12 18:05:00" and p["source"] == "manual"
    assert "approved by admin" in p["note"]
    # deciding twice does nothing
    resp = logged_in.post(f"/requests/punch/{req['id']}/reject", data={"csrf_token": logged_in.csrf}, follow_redirects=True)
    assert b"already handled" in resp.data


def test_rejected_correction_adds_nothing_and_future_is_refused(asha, logged_in, conn):
    asha.post("/me/corrections", data={"punch_time": "2099-01-01T09:00", "reason": "x", "csrf_token": asha.csrf})
    assert conn.execute("SELECT COUNT(*) FROM punch_requests").fetchone()[0] == 0
    asha.post("/me/corrections", data={"punch_time": "2026-10-12T09:00", "reason": "late bus", "csrf_token": asha.csrf})
    logged_in.post("/requests/punch/1/reject", data={"note": "No", "csrf_token": logged_in.csrf})
    assert conn.execute("SELECT status FROM punch_requests").fetchone()[0] == "rejected"
    assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 0


def test_viewer_cannot_decide_requests(client, conn, staff):
    from werkzeug.security import generate_password_hash
    conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('v', ?, 'viewer')", (generate_password_hash("viewer1"),))
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, status) VALUES (1, '2026-12-01', '2026-12-01', 'Casual', 'pending')")
    conn.commit()
    login_as(client, "v", "viewer1")
    assert client.get("/requests").status_code == 200
    client.post("/requests/leave/1/approve", data={"csrf_token": client.csrf})
    assert conn.execute("SELECT status FROM leaves").fetchone()[0] == "pending"


# ---------------------------------------------------------------- users ---
def test_user_management_and_last_admin_protection(logged_in, conn, app):
    t = logged_in.csrf
    logged_in.post("/settings", data={"form": "user", "username": "boss", "password": "boss123", "role": "admin", "csrf_token": t})
    boss = conn.execute("SELECT id FROM users WHERE username = 'boss'").fetchone()[0]
    logged_in.post("/settings", data={"form": "set_role", "user_id": boss, "role": "viewer", "csrf_token": t})
    assert conn.execute("SELECT role FROM users WHERE id = ?", (boss,)).fetchone()[0] == "viewer"
    logged_in.post("/settings", data={"form": "reset_password", "user_id": boss, "new_password": "newpass1", "csrf_token": t})
    assert login_as(app.test_client(), "boss", "newpass1").status_code == 302
    # the only admin cannot demote themselves
    me = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    logged_in.post("/settings", data={"form": "set_role", "user_id": me, "role": "viewer", "csrf_token": t})
    assert conn.execute("SELECT role FROM users WHERE id = ?", (me,)).fetchone()[0] == "admin"
    # employee logins need an employee, one each
    logged_in.post("/settings", data={"form": "user", "username": "e1", "password": "secret1", "role": "employee", "csrf_token": t})
    assert not conn.execute("SELECT 1 FROM users WHERE username = 'e1'").fetchone()


def test_deleted_user_is_signed_out(logged_in, conn, app):
    t = logged_in.csrf
    logged_in.post("/settings", data={"form": "user", "username": "temp", "password": "temp123", "role": "viewer", "csrf_token": t})
    c = app.test_client()
    login_as(c, "temp", "temp123")
    assert c.get("/").status_code == 200
    uid = conn.execute("SELECT id FROM users WHERE username = 'temp'").fetchone()[0]
    logged_in.post("/settings", data={"form": "delete_user", "user_id": uid, "csrf_token": t})
    assert c.get("/").status_code == 302


# ---------------------------------------------------------------- audit ---
def test_audit_log_records_admin_actions(logged_in, conn, staff):
    t = logged_in.csrf
    logged_in.post("/punches/add", data={"employee_id": "1", "punch_time": "2026-10-12T09:00", "note": "fix", "csrf_token": t})
    logged_in.post("/holidays", data={"date": "2026-11-08", "name": "Diwali", "csrf_token": t})
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
    assert actions[:1] == ["login"] and "punch.add" in actions and "holiday.save" in actions
    page = logged_in.get("/audit?q=Diwali").get_data(as_text=True)
    assert "holiday.save" in page and "punch.add" not in page


# -------------------------------------------------------- leave types -----
def test_leave_types_crud(logged_in, conn, staff):
    t = logged_in.csrf
    logged_in.post("/leave-types", data={"name": "Study", "yearly_quota": "5", "csrf_token": t})
    assert tuple(conn.execute("SELECT yearly_quota, paid FROM leave_types WHERE name = 'Study'").fetchone()) == (5, 0)
    logged_in.post("/leaves", data={"employee_id": "1", "start_date": "2026-10-12", "end_date": "2026-10-12",
                                    "leave_type": "Study", "csrf_token": t})
    sid = conn.execute("SELECT id FROM leave_types WHERE name = 'Study'").fetchone()[0]
    resp = logged_in.post(f"/leave-types/{sid}/delete", data={"csrf_token": t}, follow_redirects=True)
    assert b"cannot be deleted" in resp.data
    resp = logged_in.post("/leaves", data={"employee_id": "1", "start_date": "2026-10-12", "end_date": "2026-10-12",
                                           "leave_type": "Nonsense", "csrf_token": t}, follow_redirects=True)
    assert b"Choose a leave type" in resp.data


# ---------------------------------------------------------------- excel ---
def test_excel_export(logged_in, conn, staff):
    record_punch(conn, "7", "2026-10-12 09:00:00")
    record_punch(conn, "7", "2026-10-12 18:00:00")
    conn.execute("UPDATE employees SET name = '=cmd()' WHERE employee_no = '8'")
    conn.commit()
    resp = logged_in.get("/reports/monthly?month=2026-10&export=xlsx")
    assert resp.mimetype.endswith("spreadsheetml.sheet")
    wb = load_workbook(io.BytesIO(resp.data))
    assert wb.sheetnames == ["Muster roll", "Summary"]
    muster = wb["Muster roll"]
    assert muster.cell(4, 2).value == "Asha Rao" and muster.cell(4, 3 + 12).value == "P"
    assert muster.cell(5, 2).value == "'=cmd()"
    assert wb["Summary"].cell(4, 4).value >= 1


# ---------------------------------------------------------------- email ---
class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_daily_summary_email(conn, staff, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    for k, v in {"smtp_host": "smtp.test", "report_recipients": "hr@x.com, boss@x.com", "report_time": "19:00",
                 "company_name": "Demo Co"}.items():
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", (v, k))
    record_punch(conn, "7", "2026-10-12 09:40:00")
    record_punch(conn, "7", "2026-10-12 18:00:00")
    conn.commit()
    assert notify.send_daily_report(conn, now=datetime(2026, 10, 12, 18, 0)) == []  # not 19:00 yet
    assert notify.send_daily_report(conn, now=datetime(2026, 10, 12, 19, 5)) == ["hr@x.com", "boss@x.com"]
    assert notify.send_daily_report(conn, now=datetime(2026, 10, 12, 20, 0)) == []  # once a day
    msg = FakeSMTP.sent[0]
    body = msg.get_body(("plain",)).get_content()
    assert "Demo Co attendance 12 Oct 2026: 1 present, 1 absent, 1 late" == msg["Subject"]
    assert "Ravi Kumar (#8)" in body and "Asha Rao (#7) at 09:40 (+40 min)" in body


def test_email_settings_and_send_now(logged_in, conn, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    t = logged_in.csrf
    resp = logged_in.post("/settings", data={"form": "send_report", "csrf_token": t}, follow_redirects=True)
    assert b"Set up the email server" in resp.data
    logged_in.post("/settings", data={"form": "email", "smtp_host": "smtp.test", "smtp_port": "587",
                                      "smtp_security": "starttls", "smtp_password": "pw",
                                      "report_recipients": "hr@x.com", "report_time": "18:30", "csrf_token": t})
    logged_in.post("/settings", data={"form": "email", "smtp_host": "smtp.test", "smtp_port": "587",
                                      "smtp_security": "starttls", "smtp_password": "",
                                      "report_recipients": "hr@x.com", "report_time": "18:30", "csrf_token": t})
    assert conn.execute("SELECT value FROM settings WHERE key = 'smtp_password'").fetchone()[0] == "pw"  # kept
    resp = logged_in.post("/settings", data={"form": "send_report", "csrf_token": t}, follow_redirects=True)
    assert b"Daily summary sent to hr@x.com" in resp.data and len(FakeSMTP.sent) == 1
    resp = logged_in.post("/settings", data={"form": "email", "report_recipients": "not-an-email",
                                             "report_time": "18:30", "csrf_token": t}, follow_redirects=True)
    assert b"must be email addresses" in resp.data


def test_new_pages_render(logged_in, conn, staff):
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type, status, requested_by) "
                 "VALUES (1, '2026-12-01', '2026-12-01', 'Casual', 'pending', 'asha')")
    conn.execute("INSERT INTO punch_requests (employee_id, punch_time, reason) VALUES (1, '2026-10-12 18:00:00', 'x')")
    conn.commit()
    for url in ("/requests", "/audit", "/leaves?year=2026", "/leaves?status=pending", "/settings", "/employees/1", "/"):
        resp = logged_in.get(url)
        assert resp.status_code == 200, url
    assert b"<b>2</b> employee request(s) waiting" in logged_in.get("/").data
