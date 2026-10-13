from datetime import date, datetime

from app import attendance as att
from app.sync import record_punch

NOW = datetime(2026, 10, 13, 20, 0)


def setup(conn, start="09:00", end="18:00", weekly_off="6", join=None):
    conn.execute("UPDATE shifts SET start_time = ?, end_time = ?, grace_in_min = 10, grace_out_min = 10, "
                 "half_day_hours = 4, full_day_hours = 8, weekly_off = ?", (start, end, weekly_off))
    conn.execute("INSERT INTO employees (employee_no, name, shift_id, join_date) VALUES ('7', 'Asha', 1, ?)", (join,))
    conn.commit()


def day(conn, d, now=NOW):
    (_, days, summary), = att.compute(conn, d, d, now=now)
    return days[0]


def punch(conn, *times):
    for t in times:
        record_punch(conn, "7", t)
    conn.commit()


def test_present_on_time(conn):
    setup(conn)
    punch(conn, "2026-10-12 08:55:00", "2026-10-12 13:00:00", "2026-10-12 18:05:00")
    d = day(conn, date(2026, 10, 12))
    assert d.status == "P"
    assert d.first_in.strftime("%H:%M") == "08:55" and d.last_out.strftime("%H:%M") == "18:05"
    assert d.work_minutes == 9 * 60 + 10
    assert d.late_minutes == 0 and d.early_minutes == 0 and d.ot_minutes == 0
    assert d.punch_count == 3


def test_late_beyond_grace_and_overtime(conn):
    setup(conn)
    punch(conn, "2026-10-12 09:25:00", "2026-10-12 19:00:00")
    d = day(conn, date(2026, 10, 12))
    assert d.status == "P" and d.late_minutes == 25 and d.ot_minutes == 60


def test_within_grace_is_not_late(conn):
    setup(conn)
    punch(conn, "2026-10-12 09:09:00", "2026-10-12 18:00:00")
    assert day(conn, date(2026, 10, 12)).late_minutes == 0


def test_half_day_and_short_hours(conn):
    setup(conn)
    punch(conn, "2026-10-12 09:00:00", "2026-10-12 14:00:00", "2026-10-09 09:00:00", "2026-10-09 10:00:00")
    hd = day(conn, date(2026, 10, 12))
    assert hd.status == "HD" and hd.early_minutes == 240
    assert day(conn, date(2026, 10, 9)).status == "A"


def test_absent_missing_punch_and_today_in_progress(conn):
    setup(conn)
    punch(conn, "2026-10-08 09:00:00", "2026-10-13 09:00:00")
    assert day(conn, date(2026, 10, 7)).status == "A"
    assert day(conn, date(2026, 10, 8)).status == "MP"
    today = day(conn, date(2026, 10, 13), now=datetime(2026, 10, 13, 11, 0))
    assert today.status == "P" and today.remarks == ["In"]


def test_today_before_shift_end_without_punch_is_pending(conn):
    setup(conn)
    assert day(conn, date(2026, 10, 13), now=datetime(2026, 10, 13, 10, 0)).status == "-"
    assert day(conn, date(2026, 10, 13), now=datetime(2026, 10, 13, 19, 0)).status == "A"


def test_future_and_before_joining_are_blank(conn):
    setup(conn, join="2026-10-05")
    assert day(conn, date(2026, 10, 4)).status == "-"
    assert day(conn, date(2026, 10, 20)).status == "-"


def test_weekly_off_holiday_and_leave(conn):
    setup(conn)
    conn.execute("INSERT INTO holidays (date, name) VALUES ('2026-10-02', 'Gandhi Jayanti')")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date, leave_type) VALUES (1, '2026-10-05', '2026-10-06', 'Sick')")
    conn.commit()
    assert day(conn, date(2026, 10, 4)).status == "WO"   # Sunday
    assert day(conn, date(2026, 10, 2)).status == "H"
    lv = day(conn, date(2026, 10, 6))
    assert lv.status == "L" and "Sick leave" in lv.remarks[0]


def test_work_on_weekly_off_counts_as_overtime(conn):
    setup(conn)
    punch(conn, "2026-10-11 10:00:00", "2026-10-11 14:00:00")
    d = day(conn, date(2026, 10, 11))
    assert d.status == "P" and d.ot_minutes == 240 and d.late_minutes == 0


def test_overnight_shift_groups_punches_across_midnight(conn):
    setup(conn, start="22:00", end="06:00", weekly_off="")
    punch(conn, "2026-10-12 21:50:00", "2026-10-13 06:10:00")
    d = day(conn, date(2026, 10, 12))
    assert d.status == "P"
    assert d.first_in == datetime(2026, 10, 12, 21, 50) and d.last_out == datetime(2026, 10, 13, 6, 10)
    assert d.work_minutes == 8 * 60 + 20


def test_monthly_summary(conn):
    setup(conn)
    punch(conn, "2026-09-01 09:00:00", "2026-09-01 18:00:00",
          "2026-09-02 09:30:00", "2026-09-02 18:00:00",
          "2026-09-03 09:00:00", "2026-09-03 13:30:00")
    conn.execute("INSERT INTO leaves (employee_id, start_date, end_date) VALUES (1, '2026-09-04', '2026-09-04')")
    conn.commit()
    (_, days, s), = att.compute(conn, date(2026, 9, 1), date(2026, 9, 30), now=NOW)
    assert len(days) == 30
    assert s.present == 2.5 and s.half_day == 1 and s.leave == 1 and s.late == 1
    assert s.weekly_off == 4  # Sundays in Sept 2026
    assert s.absent == 30 - 3 - 1 - 4
    assert s.payable == 2.5 + 1 + 4


def test_duplicate_punches_ignored(conn):
    setup(conn)
    assert record_punch(conn, "7", "2026-10-12 09:00:00")
    assert not record_punch(conn, "7", "2026-10-12 09:00:00")
