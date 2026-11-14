"""Admin pages: list accounts, extend or end access, disable accounts."""
from datetime import timedelta
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from . import db as dbmod

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.before_app_request
def load_admin():
    g.admin = None
    if "admin_id" in session:
        g.admin = dbmod.get_db().execute("SELECT * FROM users WHERE id = ? AND is_admin = 1 AND disabled = 0",
                                         (session["admin_id"],)).fetchone()


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.admin:
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = dbmod.get_db().execute("SELECT * FROM users WHERE email = ? AND is_admin = 1 AND disabled = 0",
                                      (request.form.get("email", "").strip().lower(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["admin_id"] = user["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/admin") else url_for("admin.users"))
        flash("Wrong email or password, or this account is not an admin.")
    return render_template("admin_login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@bp.get("/")
@admin_required
def users():
    q = request.args.get("q", "").strip()
    sql = ("SELECT u.*, COUNT(c.id) AS code_count, COALESCE(SUM(c.scans), 0) AS scan_count "
           "FROM users u LEFT JOIN codes c ON c.user_id = u.id ")
    args = ()
    if q:
        sql += "WHERE u.email LIKE ? OR u.name LIKE ? "
        args = (f"%{q}%", f"%{q}%")
    sql += "GROUP BY u.id ORDER BY u.created_at DESC LIMIT 500"
    rows = dbmod.get_db().execute(sql, args).fetchall()
    now = dbmod.utcnow()
    view = []
    for r in rows:
        until = dbmod.parse(r["access_until"])
        view.append({"row": r, "active": dbmod.is_active(r), "until": until,
                     "days_left": max(0, (until - now).days + (1 if (until - now).seconds else 0)) if until > now else 0})
    return render_template("admin_users.html", users=view, q=q)


@bp.post("/users/<int:user_id>")
@admin_required
def update_user(user_id):
    db = dbmod.get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        abort(404)
    action = request.form.get("action")
    if action == "extend":
        try:
            days = int(request.form.get("days", "0"))
        except ValueError:
            days = 0
        if not 1 <= days <= 36500:
            flash("Enter a number of days between 1 and 36500.")
            return redirect(url_for("admin.users"))
        # Extend from today if access already ran out, otherwise from the current end date.
        start = max(dbmod.parse(user["access_until"]), dbmod.utcnow())
        until = start + timedelta(days=days)
        db.execute("UPDATE users SET access_until = ? WHERE id = ?", (dbmod.fmt(until), user_id))
        flash(f"{user['email']}: access extended to {until:%d %b %Y}. Their QR codes work again.")
    elif action == "end":
        db.execute("UPDATE users SET access_until = ? WHERE id = ?", (dbmod.now_str(), user_id))
        flash(f"{user['email']}: access ended. Their QR codes stopped working.")
    elif action in ("disable", "enable"):
        if user_id == g.admin["id"]:
            flash("You can't disable your own account.")
            return redirect(url_for("admin.users"))
        db.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(action == "disable"), user_id))
        if action == "disable":
            db.execute("DELETE FROM tokens WHERE user_id = ?", (user_id,))
        flash(f"{user['email']}: account {action}d.")
    else:
        abort(400)
    db.commit()
    return redirect(url_for("admin.users", q=request.args.get("q", "")))
