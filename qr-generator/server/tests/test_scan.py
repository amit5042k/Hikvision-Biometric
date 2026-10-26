from .conftest import expire
from .test_api import make


def test_url_code_redirects_and_counts_scans(client, auth):
    code = make(client, auth, "url", {"url": "https://example.com/menu"}).get_json()["code"]
    for _ in range(2):
        resp = client.get("/r/" + code["slug"])
        assert resp.status_code == 302 and resp.headers["Location"] == "https://example.com/menu"
        assert resp.headers["Cache-Control"] == "no-store"
    assert client.get("/api/codes", headers=auth).get_json()["codes"][0]["scans"] == 2


def test_code_stops_working_when_trial_ends(client, auth, conn):
    code = make(client, auth).get_json()["code"]
    expire(conn)
    resp = client.get("/r/" + code["slug"])
    assert resp.status_code == 410
    body = resp.get_data(as_text=True)
    assert "This QR code has expired" in body and "Call 12345." in body
    assert client.get("/api/codes", headers=auth).get_json()["codes"][0]["scans"] == 0


def test_unknown_code(client):
    assert client.get("/r/zzzzzzz").status_code == 404


def test_landing_pages_escape_content(client, auth):
    text = make(client, auth, "text", {"text": "<script>alert(1)</script>"}).get_json()["code"]
    body = client.get("/r/" + text["slug"]).get_data(as_text=True)
    assert "&lt;script&gt;alert(1)" in body and "<script>alert(1)" not in body

    wifi = make(client, auth, "wifi", {"ssid": "Cafe", "security": "WPA", "wifiPassword": "p@ss"}).get_json()["code"]
    body = client.get("/r/" + wifi["slug"]).get_data(as_text=True)
    assert "Cafe" in body and "p@ss" in body

    sms = make(client, auth, "sms", {"smsNumber": "+91 98765", "smsBody": "Hi there"}).get_json()["code"]
    assert 'href="sms:+9198765?body=Hi%20there"' in client.get("/r/" + sms["slug"]).get_data(as_text=True)


def test_vcard_download(client, auth, conn):
    code = make(client, auth, "vcard", {"firstName": "Amit", "lastName": "Verma", "org": "MGN, Urban",
                                        "vPhone": "+91 98765"}).get_json()["code"]
    page = client.get("/r/" + code["slug"]).get_data(as_text=True)
    assert f'/r/{code["slug"]}.vcf' in page
    vcf = client.get(f'/r/{code["slug"]}.vcf')
    assert vcf.mimetype == "text/vcard"
    text = vcf.get_data(as_text=True)
    assert "FN:Amit Verma" in text and "ORG:MGN\\, Urban" in text and "TEL;TYPE=CELL:+91 98765" in text
    expire(conn)
    assert client.get(f'/r/{code["slug"]}.vcf').status_code == 410


def test_website_is_served(client):
    assert b"QR Generator" in client.get("/").data
    assert client.get("/app.js").status_code == 200
