"""Installable web app (PWA): manifest, service worker, offline page, Play Store link file.

Installing from the browser gives a home-screen icon that opens the app
full-screen on Android and iPhone. The service worker never stores
attendance pages; it only caches the look of the app (CSS, icons) and shows
a friendly page when the phone is offline.
"""
import json
from pathlib import Path

from flask import Blueprint, Response, current_app, render_template, send_from_directory, url_for

from . import db as dbmod
from .db import get_db

bp = Blueprint("pwa", __name__)
VERSION = "1"  # bump to make phones refresh the cached app shell


@bp.route("/manifest.webmanifest")
def manifest():
    name = dbmod.get_setting(get_db(), "company_name") or "Attendance"
    data = {
        "name": f"{name} Attendance",
        "short_name": "Attendance",
        "description": "Attendance, leave and reports for Hikvision face terminals",
        "start_url": "/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#161b26",
        "theme_color": "#c8102e",
        "icons": [
            {"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": url_for("static", filename="icons/maskable-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Today's attendance", "url": "/attendance"},
            {"name": "My attendance", "url": "/me/"},
        ],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json")


@bp.route("/sw.js")
def service_worker():
    js = (Path(current_app.static_folder) / "sw.js").read_text().replace("__VERSION__", VERSION)
    return Response(js, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@bp.route("/offline")
def offline():
    return render_template("offline.html")


@bp.route("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links for publishing the app on Google Play as a Trusted Web Activity.

    Paste the JSON that PWABuilder / Bubblewrap gives you into Settings -> Mobile app.
    """
    raw = dbmod.get_setting(get_db(), "android_assetlinks") or "[]"
    try:
        json.loads(raw)
    except ValueError:
        raw = "[]"
    return Response(raw, mimetype="application/json")


@bp.route("/favicon.ico")
def favicon():
    return send_from_directory(Path(current_app.static_folder) / "icons", "favicon-32.png", mimetype="image/png")
