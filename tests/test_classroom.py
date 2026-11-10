"""Classroom face-recognition attendance: kiosk API, entry/exit sessions, registers, exports."""
import io
import json
from datetime import date, datetime, timedelta

import pytest
from openpyxl import load_workbook

from app import classroom as cl
from tests.conftest import csrf_from

VEC = [0.01 * i for i in range(128)]


@pytest.fixture
def school(conn):
    conn.execute("INSERT INTO classrooms (name, kiosk_token, absence_minutes) VALUES ('8-A', 'tok8a', 10)")
    conn.execute("INSERT INTO classrooms (name, kiosk_token, absence_minutes) VALUES ('Lab', 'toklab', 10)")
    conn.execute("INSERT INTO class_persons (person_no, name, role, class_name, classroom_id) "
                 "VALUES ('S1', 'Asha', 'student', '8-A', 1)")
    conn.execute("INSERT INTO class_persons (person_no, name, role, class_name, classroom_id) "
                 "VALUES ('S2', 'Ravi', 'student', '8-A', 1)")
    conn.execute("INSERT INTO class_persons (person_no, name, role) VALUES ('T1', 'Mrs Iyer', 'teacher')")
    conn.execute("INSERT INTO face_samples (person_id, descriptor) VALUES (1, ?)", (json.dumps(VEC),))
    conn.commit()
    return conn


def seen(conn, person, at, room=1):
    conn.execute("INSERT INTO class_sightings (classroom_id, person_id, seen_at) VALUES (?, ?, ?)",
                 (room, person, at))
    conn.commit()


def room(conn, rid=1):
    return conn.execute("SELECT * FROM classrooms WHERE id = ?", (rid,)).fetchone()


# ------------------------------------------------------------- sessions ---
def test_sessions_split_on_absence_and_open_session():
    t = datetime(2026, 11, 10, 9, 0)
    times = [t, t + timedelta(minutes=3), t + timedelta(minutes=8), t + timedelta(minutes=40), t + timedelta(minutes=45)]
    done = cl.build_sessions(times, 10, now=t + timedelta(hours=5))
    assert [(s.entry.minute, s.exit.minute, s.minutes) for s in done] == [(0, 8, 8), (40, 45, 5)]
    live = cl.build_sessions(times, 10, now=t + timedelta(minutes=50))
    assert live[-1].open and live[-1].exit is None and not live[0].open


def test_register_marks_present_absent_and_visitors(school):
    day = date(2026, 11, 9)  # a Monday
    for m in (0, 2, 5):
        seen(school, 1, f"2026-11-09 09:{m:02d}:00")
    seen(school, 3, "2026-11-09 10:00:00")                       # visiting teacher
    rows, _, kind = cl.register(school, room(school), day, now=datetime(2026, 11, 10, 9))
    by = {r.person["person_no"]: r for r in rows}
    assert kind == "" and by["S1"].status == "P" and by["S2"].status == "A" and by["T1"].status == "P"
    assert by["S1"].first_in.strftime("%H:%M") == "09:00" and by["S1"].last_out.strftime("%H:%M") == "09:05"
    assert rows[0].person["role"] == "teacher"                   # teachers first


def test_register_periods_and_holidays(school):
    school.execute("INSERT INTO class_periods (name, start_time, end_time) VALUES ('P1', '09:00', '09:40')")
    school.execute("INSERT INTO class_periods (name, start_time, end_time) VALUES ('P2', '09:40', '10:20')")
    school.execute("INSERT INTO holidays (date, name) VALUES ('2026-11-11', 'Fest')")
    school.commit()
    seen(school, 1, "2026-11-09 09:10:00")
    rows, plist, _ = cl.register(school, room(school), date(2026, 11, 9), now=datetime(2026, 11, 10))
    by = {r.person["person_no"]: r for r in rows}
    assert len(plist) == 2 and by["S1"].periods == ["P", "A"] and by["S2"].periods == ["A", "A"]
    rows, _, kind = cl.register(school, room(school), date(2026, 11, 11), now=datetime(2026, 11, 12))
    assert kind == "H" and all(r.status == "H" for r in rows)
    rows, _, kind = cl.register(school, room(school), date(2026, 11, 15), now=datetime(2026, 11, 16))
    assert kind == "WO"                                           # Sunday is the default weekly off


def test_movements_entry_and_exit(school):
    for at in ("09:00", "09:05", "11:00"):
        seen(school, 1, f"2026-11-09 {at}:00")
    ev = cl.movements(school, date(2026, 11, 9), now=datetime(2026, 11, 10))
    assert [(e["type"], e["time"].strftime("%H:%M")) for e in sorted(ev, key=lambda e: e["time"])] == \
        [("Entry", "09:00"), ("Exit", "09:05"), ("Entry", "11:00"), ("Exit", "11:00")]


def test_monthly_counts_any_classroom(school):
    seen(school, 1, "2026-11-02 09:00:00")
    seen(school, 1, "2026-11-03 09:00:00", room=2)  # seen only in the lab: still present at school
    rows, days = cl.monthly(school, date(2026, 11, 1), date(2026, 11, 30), now=datetime(2026, 11, 4, 12))
    asha = next(r for r in rows if r["person"]["person_no"] == "S1")
    assert len(days) == 30 and asha["marks"][:4] == ["WO", "P", "P", "A"] and asha["marks"][10] == "-"
    assert asha["present"] == 2 and asha["absent"] == 1 and asha["percent"] == 66.7
    assert all(r["person"]["role"] == "student" for r in rows)


# ------------------------------------------------------------ kiosk API ---
def test_kiosk_roster_and_sightings(client, school):
    r = client.get("/api/class/tok8a/roster").get_json()
    assert r["classroom"]["name"] == "8-A" and r["threshold"] == 0.5
    assert [p["name"] for p in r["persons"]] == ["Asha"] and len(r["persons"][0]["descriptors"][0]) == 128
    assert school.execute("SELECT last_seen_at FROM classrooms WHERE id = 1").fetchone()[0]
    resp = client.post("/api/class/tok8a/sightings", json={"sightings": [
        {"person_id": 1, "age_seconds": 120, "distance": 0.31},
        {"person_id": 1, "age_seconds": 115},            # within 15 s of the previous: dropped
        {"person_id": 1, "age_seconds": 0},
        {"person_id": 99, "age_seconds": 0},             # unknown person
        {"person_id": 2, "age_seconds": -5},             # from the future
        {"person_id": "x"}, "junk"]})
    assert resp.get_json() == {"ok": True, "stored": 2}
    times = [datetime.strptime(t[0], cl.FMT) for t in school.execute("SELECT seen_at FROM class_sightings ORDER BY seen_at")]
    assert 115 <= (times[1] - times[0]).total_seconds() <= 125   # server time minus age
    present = client.get("/api/class/tok8a/roster").get_json()["present"]
    assert [p["person_id"] for p in present] == [1]


def test_kiosk_requires_valid_enabled_token(client, school):
    assert client.get("/kiosk/nope").status_code == 404
    assert client.get("/api/class/nope/roster").status_code == 404
    assert client.post("/api/class/nope/sightings", json={}).status_code == 404
    school.execute("UPDATE classrooms SET enabled = 0 WHERE id = 1")
    school.commit()
    assert client.get("/kiosk/tok8a").status_code == 404
    page = client.get("/kiosk/toklab").get_data(as_text=True)
    assert "Lab" in page and "/api/class/toklab/roster" in page and "faceapi/face-api.js" in page


def test_face_models_are_served(client):
    for path in ("faceapi/face-api.js", "faceapi/model/face_recognition_model-weights_manifest.json",
                 "faceapi/model/ssd_mobilenetv1_model.bin", "faceapi/model/tiny_face_detector_model.bin",
                 "classroom/kiosk.js", "classroom/faces.js"):
        assert client.get(f"/static/{path}").status_code == 200, path


# ------------------------------------------------------------- admin UI ---
def test_admin_flow_rooms_people_faces(logged_in, conn):
    c, t = logged_in, logged_in.csrf
    assert c.post("/classroom/rooms", data={"name": "9-B", "absence_minutes": "15", "csrf_token": t}).status_code == 302
    rid = conn.execute("SELECT id FROM classrooms WHERE name = '9-B'").fetchone()[0]
    page = c.get("/classroom").get_data(as_text=True)
    assert "9-B" in page and "/kiosk/" in page
    c.post("/classroom/people/new", data={"person_no": "S9", "name": "Meena", "role": "student", "class_name": "9-B",
                                          "classroom_id": str(rid), "active": "1", "csrf_token": t})
    pid = conn.execute("SELECT id FROM class_persons WHERE person_no = 'S9'").fetchone()[0]
    bad = c.post(f"/classroom/people/{pid}/faces", data={"descriptor": "[1,2]", "csrf_token": t})
    assert bad.status_code == 400 and not bad.get_json()["ok"]
    ok = c.post(f"/classroom/people/{pid}/faces", data={"descriptor": json.dumps(VEC), "csrf_token": t,
                                                        "thumbnail": "data:image/jpeg;base64,AAAA"})
    assert ok.get_json() == {"ok": True}
    bulk = c.post("/classroom/enroll/by-number", data={"person_no": "S9", "descriptor": json.dumps(VEC), "csrf_token": t})
    assert bulk.get_json()["name"] == "Meena"
    assert c.post("/classroom/enroll/by-number", data={"person_no": "ZZ", "descriptor": json.dumps(VEC),
                                                       "csrf_token": t}).status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM face_samples WHERE person_id = ?", (pid,)).fetchone()[0] == 2
    assert "Meena" in c.get(f"/classroom/people/{pid}").get_data(as_text=True)
    # face posts need the CSRF token like every other form
    assert c.post(f"/classroom/people/{pid}/faces", data={"descriptor": json.dumps(VEC)}).status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'face.add'").fetchone()[0] == 2


def test_face_limit_per_person(logged_in, school):
    for _ in range(9):
        school.execute("INSERT INTO face_samples (person_id, descriptor) VALUES (1, ?)", (json.dumps(VEC),))
    school.commit()
    r = logged_in.post("/classroom/people/1/faces", data={"descriptor": json.dumps(VEC), "csrf_token": logged_in.csrf})
    assert r.status_code == 400 and "already has 10" in r.get_json()["error"]


def test_csv_import_and_export(logged_in, school):
    csv_text = "Number,Name,Role,Class,Classroom,Active\nS1,Asha R,student,8-A,8-A,1\nT5,Mr Das,teacher,,Lab,1\n" \
               "S7,Kiran,student,8-A,Nowhere,1\n,Missing,student,,,\n"
    r = logged_in.post("/classroom/people/import", data={"csrf_token": logged_in.csrf,
                       "file": (io.BytesIO(csv_text.encode()), "people.csv")}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "2 added, 1 updated" in page and "unknown classroom" in page
    assert school.execute("SELECT name FROM class_persons WHERE person_no = 'S1'").fetchone()[0] == "Asha R"
    assert school.execute("SELECT role, classroom_id FROM class_persons WHERE person_no = 'T5'").fetchone()[:] == ("teacher", 2)
    out = logged_in.get("/classroom/people/export.csv").get_data(as_text=True)
    assert out.splitlines()[0] == "Number,Name,Role,Class,Classroom,Active" and "Mr Das" in out


def test_report_pages_and_exports(logged_in, school):
    today = date.today()
    seen(school, 1, f"{today} 00:00:01")
    for url in ("/classroom", "/classroom/register", "/classroom/movements", "/classroom/monthly",
                "/classroom/people", "/classroom/enroll", "/classroom/people/1"):
        assert logged_in.get(url).status_code == 200, url
    reg = logged_in.get(f"/classroom/register?classroom=1&date={today}").get_data(as_text=True)
    assert "Asha" in reg and "Ravi" in reg
    csv_out = logged_in.get(f"/classroom/register?classroom=1&date={today}&export=csv").get_data(as_text=True)
    assert "First entry" in csv_out and "S1,Asha" in csv_out
    wb = load_workbook(io.BytesIO(logged_in.get(f"/classroom/register?classroom=1&date={today}&export=xlsx").data))
    assert wb.active["B4"].value in ("Asha", "Ravi")
    mv = logged_in.get(f"/classroom/movements?date={today}&export=1").get_data(as_text=True)
    assert "Entry" in mv and "Asha" in mv
    wb = load_workbook(io.BytesIO(logged_in.get(f"/classroom/monthly?month={today:%Y-%m}&export=xlsx").data))
    assert wb.active["A3"].value == "Number"


def test_settings_and_periods(logged_in, conn):
    t = logged_in.csrf
    logged_in.post("/classroom/settings", data={"class_match_threshold": "0.9", "class_detector": "tiny",
                                                "off_5": "on", "off_6": "on", "csrf_token": t})
    s = dict(conn.execute("SELECT key, value FROM settings WHERE key LIKE 'class_%'").fetchall())
    assert s == {"class_match_threshold": "0.70", "class_detector": "tiny", "class_weekly_off": "5,6"}
    logged_in.post("/classroom/periods", data={"name": "P1", "start_time": "10:00", "end_time": "09:00", "csrf_token": t})
    logged_in.post("/classroom/periods", data={"name": "P1", "start_time": "09:00", "end_time": "09:40", "csrf_token": t})
    assert conn.execute("SELECT name, start_time, end_time FROM class_periods").fetchall()[0][:] == ("P1", "09:00", "09:40")


def test_new_kiosk_link_invalidates_old(logged_in, school):
    logged_in.post("/classroom/rooms/1/token", data={"csrf_token": logged_in.csrf})
    assert logged_in.get("/api/class/tok8a/roster").status_code == 404


def test_viewer_can_view_but_not_change(app, logged_in, conn):
    from tests.test_features import login_as
    from werkzeug.security import generate_password_hash
    conn.execute("INSERT INTO users (username, password_hash, role) VALUES ('v', ?, 'viewer')",
                 (generate_password_hash("secret1"),))
    conn.commit()
    v = app.test_client()
    login_as(v, "v", "secret1")
    assert v.get("/classroom").status_code == 200
    v.post("/classroom/rooms", data={"name": "X", "csrf_token": v.csrf})
    assert conn.execute("SELECT COUNT(*) FROM classrooms").fetchone()[0] == 0
    anon = app.test_client()
    assert anon.get("/classroom/register").status_code == 302
