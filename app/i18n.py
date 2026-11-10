"""Interface languages: English (built in) and Hindi (app/translations/hi.json).

Templates call ``_("English text")``; when the current language has no
translation for a string, the English text is shown, so a missing entry never
breaks a page.
"""
import json
from pathlib import Path

from flask import Blueprint, g, redirect, request, url_for

from . import db as dbmod

LANGUAGES = {"en": "English", "hi": "हिन्दी"}
_TRANSLATIONS = {
    code: json.loads((Path(__file__).parent / "translations" / f"{code}.json").read_text(encoding="utf-8"))
    for code in LANGUAGES if code != "en"
}

bp = Blueprint("i18n", __name__)


def current_language():
    user = g.get("user")
    if user is not None and user["language"] in LANGUAGES:
        return user["language"]
    if request.cookies.get("lang") in LANGUAGES:
        return request.cookies["lang"]
    default = dbmod.get_setting(dbmod.get_db(), "default_language")
    return default if default in LANGUAGES else "en"


def gettext(text):
    lang = g.get("lang") or "en"
    if lang == "en" or not text:
        return text
    return _TRANSLATIONS.get(lang, {}).get(str(text), text)


@bp.route("/lang/<code>")
def set_language(code):
    """Switch language for this browser (and remember it for the signed-in user)."""
    if code not in LANGUAGES:
        code = "en"
    if g.get("user") is not None:
        db = dbmod.get_db()
        db.execute("UPDATE users SET language = ? WHERE id = ?", (code, g.user["id"]))
        db.commit()
    target = request.referrer or url_for("main.dashboard")
    if not target.startswith(request.host_url):
        target = url_for("main.dashboard")
    resp = redirect(target)
    resp.set_cookie("lang", code, max_age=365 * 24 * 3600, samesite="Lax")
    return resp
