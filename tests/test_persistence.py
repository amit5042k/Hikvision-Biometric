"""Updating the code must never reset logins or data."""
import sqlite3
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from app import create_app
from app import db as dbmod


def make_v1_database(path):
    """A database exactly as the first release created it (no user_version)."""
    conn = sqlite3.connect(path)
    conn.executescript((Path(dbmod.__file__).parent / "schema.sql").read_text())
    conn.execute("INSERT INTO users (username, password_hash) VALUES ('admin', ?)", (generate_password_hash("S3cret!"),))
    conn.execute("INSERT INTO settings (key, value) VALUES ('company_name', 'MGN Urban Estate')")
    conn.execute("INSERT INTO shifts (name) VALUES ('General')")
    conn.execute("INSERT INTO employees (employee_no, name) VALUES ('1', 'Asha')")
    conn.execute("INSERT INTO devices (name, host) VALUES ('Gate', '192.168.1.64')")
    conn.execute("INSERT INTO punches (employee_no, punch_time) VALUES ('1', '2026-10-01 09:00:00')")
    conn.commit()
    conn.close()


def test_upgrade_from_first_release_keeps_everything(tmp_path):
    db_path = tmp_path / "attendance.db"
    make_v1_database(db_path)
    create_app({"DATA_DIR": str(tmp_path), "DATABASE": str(db_path), "BACKUP_DIR": str(tmp_path / "backups"),
                "SECRET_KEY": "x"})
    conn = dbmod.connect(db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION
    user = conn.execute("SELECT * FROM users").fetchall()
    assert len(user) == 1 and check_password_hash(user[0]["password_hash"], "S3cret!")
    assert dbmod.get_setting(conn, "company_name") == "MGN Urban Estate"
    assert conn.execute("SELECT name FROM employees").fetchone()[0] == "Asha"
    assert conn.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 1
    assert conn.execute("SELECT connection FROM devices").fetchone()[0] == "direct"  # new column, default
    assert conn.execute("SELECT COUNT(*) FROM device_jobs").fetchone()[0] == 0       # new table
    # a safety backup of the old database was taken before migrating
    backups = list((tmp_path / "backups").glob("attendance-*.db"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    assert old.execute("PRAGMA user_version").fetchone()[0] == 0
    assert old.execute("SELECT COUNT(*) FROM punches").fetchone()[0] == 1


def test_restart_does_not_touch_data(tmp_path):
    cfg = {"DATA_DIR": str(tmp_path), "DATABASE": str(tmp_path / "a.db"), "BACKUP_DIR": str(tmp_path / "b")}
    create_app(dict(cfg))
    conn = dbmod.connect(cfg["DATABASE"])
    conn.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'", (generate_password_hash("changed1"),))
    token = dbmod.get_setting(conn, "bridge_token")
    conn.commit()
    conn.close()
    create_app(dict(cfg))  # simulates the restart after `git pull`
    conn = dbmod.connect(cfg["DATABASE"])
    assert check_password_hash(conn.execute("SELECT password_hash FROM users").fetchone()[0], "changed1")
    assert dbmod.get_setting(conn, "bridge_token") == token and len(token) > 20
    assert not Path(cfg["BACKUP_DIR"]).exists()  # nothing to migrate -> no backup needed


def test_secret_key_persists_in_data_dir(tmp_path):
    a = create_app({"DATA_DIR": str(tmp_path)})
    b = create_app({"DATA_DIR": str(tmp_path)})
    assert a.config["SECRET_KEY"] == b.config["SECRET_KEY"]
    assert (tmp_path / "secret_key").exists() and (tmp_path / "attendance.db").exists()


def test_backup_download(logged_in, app):
    resp = logged_in.get("/settings/backup")
    assert resp.status_code == 200 and resp.data[:15] == b"SQLite format 3"
    assert len(list(Path(app.config["BACKUP_DIR"]).glob("*.db"))) == 1


def test_migrations_are_append_only():
    versions = [v for v, _ in dbmod.MIGRATIONS]
    assert versions == sorted(versions) and versions[0] == 2 and len(set(versions)) == len(versions)
