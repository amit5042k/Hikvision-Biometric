"""Hindi interface."""
import json
import re
from pathlib import Path

from tests.conftest import csrf_from

PAGES = ["/", "/employees", "/employees/new", "/employees/1", "/attendance", "/reports/monthly",
         "/reports/monthly?view=summary", "/punches", "/leaves", "/requests", "/roster", "/shifts", "/holidays",
         "/devices", "/devices/new", "/sites", "/settings", "/audit", "/account/2fa"]


def test_switch_to_hindi_everywhere(logged_in, conn):
    conn.execute("INSERT INTO employees (employee_no, name, shift_id) VALUES ('1', 'Asha', 1)")
    conn.commit()
    resp = logged_in.get("/lang/hi", headers={"Referer": "http://localhost/employees"})
    assert resp.status_code == 302 and "lang=hi" in resp.headers.get("Set-Cookie", "")
    assert conn.execute("SELECT language FROM users WHERE username = 'admin'").fetchone()[0] == "hi"
    for url in PAGES:
        resp = logged_in.get(url)
        assert resp.status_code == 200, url
        html = resp.get_data(as_text=True)
        assert 'lang="hi"' in html and "डैशबोर्ड" in html, url
    assert "उपस्थिति" in logged_in.get("/attendance").get_data(as_text=True)


def test_language_follows_the_user_not_just_the_browser(logged_in, conn, app):
    logged_in.get("/lang/hi")
    other = app.test_client()  # a fresh browser: still English on the login page
    page = other.get("/login").get_data(as_text=True)
    assert "Sign in" in page and 'lang="en"' in page
    token = csrf_from(page)
    other.post("/login", data={"username": "admin", "password": "admin", "csrf_token": token})
    assert "डैशबोर्ड" in other.get("/").get_data(as_text=True)  # the user's saved preference


def test_login_page_and_flash_in_hindi(client):
    client.get("/lang/hi")
    page = client.get("/login").get_data(as_text=True)
    assert "साइन इन करें" in page
    token = csrf_from(page)
    resp = client.post("/login", data={"username": "admin", "password": "wrong", "csrf_token": token})
    assert "उपयोगकर्ता नाम या पासवर्ड गलत है।" in resp.get_data(as_text=True)


def test_default_language_setting(logged_in, client, conn):
    logged_in.post("/settings", data={"form": "general", "default_language": "hi", "csrf_token": logged_in.csrf})
    assert conn.execute("SELECT value FROM settings WHERE key = 'default_language'").fetchone()[0] == "hi"
    assert "साइन इन करें" in client.get("/login").get_data(as_text=True)
    client.get("/lang/en")
    assert "Sign in" in client.get("/login").get_data(as_text=True)


def test_unknown_language_and_untranslated_text_fall_back(logged_in):
    logged_in.get("/lang/xx")
    assert "Dashboard" in logged_in.get("/").get_data(as_text=True)
    from app import i18n
    from flask import g
    with logged_in.application.test_request_context():
        g.lang = "hi"
        assert i18n.gettext("Some brand new text") == "Some brand new text"
        assert i18n.gettext("Present") == "उपस्थित"


def test_language_switch_does_not_redirect_offsite(client):
    resp = client.get("/lang/hi", headers={"Referer": "https://evil.example/"})
    assert resp.headers["Location"].endswith("/")


def test_every_template_string_has_a_translation():
    """Guard: a new English label added to a template must also get a Hindi entry."""
    hi = json.loads((Path(__file__).parent.parent / "app" / "translations" / "hi.json").read_text(encoding="utf-8"))
    technical = {"server_url", "token", "attendance@yourcompany.com", "hr@yourcompany.com, owner@yourcompany.com",
                 "smtp.example.com", "e.g. +05:30", "bridge", "direct", "Optional", "optional"}
    missing = set()
    for f in (Path(__file__).parent.parent / "app" / "templates").rglob("*.html"):
        for m in re.finditer(r'_\("((?:[^"\\]|\\.)*)"\)', f.read_text()):
            key = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            if key not in hi and key not in technical:
                missing.add(key)
    assert not missing, sorted(missing)
