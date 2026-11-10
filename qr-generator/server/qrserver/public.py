"""What people see when they scan a code: /r/<slug>."""
import json
import unicodedata
from urllib.parse import quote

from flask import Blueprint, Response, current_app, redirect, render_template

from . import db as dbmod
from .content import TYPE_NAMES, vcard

bp = Blueprint("public", __name__)


def _lookup(slug):
    """Returns (code, data) for a code whose owner's access is still valid; renders the right error otherwise."""
    db = dbmod.get_db()
    row = db.execute("SELECT c.*, u.disabled, u.access_until FROM codes c JOIN users u ON u.id = c.user_id "
                     "WHERE c.slug = ?", (slug,)).fetchone()
    if not row:
        return None, render_template("message.html", title="QR code not found",
                                     message="This QR code doesn't exist or has been deleted."), 404
    if not dbmod.is_active(row):
        return None, render_template("message.html", title="This QR code has expired",
                                     message="The owner's free trial has ended, so this code no longer works.",
                                     owner_note=True), 410
    return row, json.loads(row["data"]), 200


@bp.get("/r/<slug>")
def scan(slug):
    row, data, status = _lookup(slug)
    if row is None:
        return data, status
    db = dbmod.get_db()
    db.execute("UPDATE codes SET scans = scans + 1, last_scan_at = ? WHERE id = ?", (dbmod.now_str(), row["id"]))
    db.commit()
    if row["type"] == "url":
        resp = redirect(data["url"], code=302)
        resp.headers["Cache-Control"] = "no-store"  # so extending/expiring takes effect immediately
        return resp
    return render_template("landing.html", code=row, d=data, type_name=TYPE_NAMES[row["type"]])


@bp.get("/r/<slug>.vcf")
def scan_vcard(slug):
    row, data, status = _lookup(slug)
    if row is None:
        return data, status
    if row["type"] != "vcard":
        return render_template("message.html", title="QR code not found", message="No contact here."), 404
    return Response(vcard(data), mimetype="text/vcard",
                    headers={"Content-Disposition": _attachment(row["label"], ".vcf")})


def _attachment(label, ext):
    """Content-Disposition for a download named after the code's label.

    HTTP headers must be Latin-1, so non-English names (e.g. Hindi) go in the RFC 6266
    `filename*` field, with an ASCII-only `filename` for old clients.
    """
    # keep letters, combining marks (Indic vowel signs), digits, spaces, - and _
    name = " ".join("".join(ch for ch in label if unicodedata.category(ch)[0] in "LMN" or ch in " -_").split())
    name = name[:60] or "contact"
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().strip(" -_") or "contact"
    return f'attachment; filename="{ascii_name}{ext}"; filename*=UTF-8\'\'{quote(name + ext)}'


@bp.app_context_processor
def inject_contact():
    return {"contact_text": current_app.config["CONTACT_TEXT"]}
