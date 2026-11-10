"""Installable app (PWA) pieces."""
import json
from pathlib import Path


def test_manifest_service_worker_and_offline_are_public(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.mimetype == "application/manifest+json"
    data = json.loads(m.data)
    assert data["display"] == "standalone" and data["start_url"].startswith("/")
    sizes = {i["sizes"] for i in data["icons"]}
    assert {"192x192", "512x512"} <= sizes and any(i.get("purpose") == "maskable" for i in data["icons"])
    static = Path(__file__).parent.parent / "app"
    for icon in data["icons"]:
        assert (static / icon["src"].lstrip("/")).exists()
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["Service-Worker-Allowed"] == "/" and b"__VERSION__" not in sw.data
    assert b"You are offline" in client.get("/offline").data
    assert client.get("/.well-known/assetlinks.json").get_json() == []
    assert client.get("/favicon.ico").status_code == 200


def test_pages_link_the_manifest_and_register_the_worker(logged_in, client):
    for c, url in ((client, "/login"), (logged_in, "/")):
        html = c.get(url).get_data(as_text=True)
        assert 'rel="manifest"' in html and "serviceWorker.register" in html and "apple-touch-icon" in html


def test_service_worker_never_caches_pages():
    js = (Path(__file__).parent.parent / "app" / "static" / "sw.js").read_text()
    assert "req.mode === 'navigate'" in js and "fetch(req).catch(() => caches.match('/offline'))" in js


def test_employee_and_2fa_pending_users_can_load_the_app_shell(logged_in, conn, app):
    from tests.conftest import csrf_from
    conn.execute("INSERT INTO employees (employee_no, name) VALUES ('1', 'Asha')")
    conn.commit()
    logged_in.post("/employees/1/login", data={"username": "asha", "password": "secret1", "csrf_token": logged_in.csrf})
    c = app.test_client()
    token = csrf_from(c.get("/login").get_data(as_text=True))
    c.post("/login", data={"username": "asha", "password": "secret1", "csrf_token": token})
    assert c.get("/manifest.webmanifest").status_code == 200 and c.get("/sw.js").status_code == 200
    logged_in.post("/settings", data={"form": "general", "require_2fa_admins": "on", "csrf_token": logged_in.csrf})
    assert logged_in.get("/manifest.webmanifest").status_code == 200  # not redirected to 2FA setup


def test_asset_links_setting(logged_in, client):
    t = logged_in.csrf
    links = '[{"relation": ["delegate_permission/common.handle_all_urls"], "target": {"namespace": "android_app", ' \
            '"package_name": "com.example.attendance", "sha256_cert_fingerprints": ["AB:CD"]}}]'
    logged_in.post("/settings", data={"form": "mobile", "android_assetlinks": links, "csrf_token": t})
    assert client.get("/.well-known/assetlinks.json").get_json()[0]["target"]["package_name"] == "com.example.attendance"
    resp = logged_in.post("/settings", data={"form": "mobile", "android_assetlinks": "{not json", "csrf_token": t},
                          follow_redirects=True)
    assert "JSON list".encode() in resp.data
    assert client.get("/.well-known/assetlinks.json").get_json()[0]["target"]["package_name"] == "com.example.attendance"
