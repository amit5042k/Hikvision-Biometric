"""Daily attendance summary by email (SMTP).

Sent automatically at Settings -> Email -> "Daily report time" by run.py's
background thread, or by cron with ``flask --app app send-daily-report`` on
hosting without background threads (cPanel).
"""
import logging
import smtplib
import ssl
import threading
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import escape

from . import attendance as att
from . import db as dbmod

log = logging.getLogger(__name__)


class NotifyError(Exception):
    pass


def build_daily_summary(db, day=None):
    """Return ``(subject, text, html)`` for one day's attendance."""
    day = day or date.today()
    settings = dbmod.get_settings(db)
    rows = att.compute(db, day, day, att.report_employees(db, day, day))
    groups = {"Absent": [], "Late": [], "On leave": [], "Missing punch": [], "Not in yet": []}
    present = 0
    for emp, days, _ in rows:
        d = days[0]
        label = f"{emp['name']} (#{emp['employee_no']})"
        if d.status in ("P", "HD", "MP"):
            present += 1
        if d.status == "A":
            groups["Absent"].append(label)
        elif d.status == "L":
            groups["On leave"].append(label)
        elif d.status == "MP":
            groups["Missing punch"].append(label)
        elif d.status == "-":
            groups["Not in yet"].append(label)
        if d.is_late:
            groups["Late"].append(f"{label} at {d.first_in:%H:%M} (+{d.late_minutes} min)")
    pending_leave = db.execute("SELECT COUNT(*) FROM leaves WHERE status = 'pending'").fetchone()[0]
    pending_punch = db.execute("SELECT COUNT(*) FROM punch_requests WHERE status = 'pending'").fetchone()[0]
    offline = [r["name"] for r in db.execute("SELECT name FROM devices WHERE enabled = 1 AND last_status = 'error'")]

    company = settings.get("company_name") or "Attendance"
    subject = (f"{company} attendance {day:%d %b %Y}: {present} present, {len(groups['Absent'])} absent, "
               f"{len(groups['Late'])} late")
    lines = [f"{company} - attendance for {day:%A, %d %B %Y}", "",
             f"Employees: {len(rows)}   Present: {present}   Absent: {len(groups['Absent'])}   "
             f"Late: {len(groups['Late'])}   On leave: {len(groups['On leave'])}", ""]
    html = [f"<h2 style='font-family:sans-serif'>{escape(company)} &ndash; {day:%A, %d %B %Y}</h2>",
            "<table style='font-family:sans-serif;border-collapse:collapse'><tr>"]
    for k, v in (("Employees", len(rows)), ("Present", present), ("Absent", len(groups["Absent"])),
                 ("Late", len(groups["Late"])), ("On leave", len(groups["On leave"]))):
        html.append(f"<td style='padding:6px 14px;border:1px solid #ddd'><b style='font-size:20px'>{v}</b>"
                    f"<br>{k}</td>")
    html.append("</tr></table>")
    for title, names in groups.items():
        if names:
            lines += [f"{title} ({len(names)}):"] + [f"  - {n}" for n in names] + [""]
            html.append(f"<h3 style='font-family:sans-serif'>{title} ({len(names)})</h3><ul style='font-family:sans-serif'>"
                        + "".join(f"<li>{escape(n)}</li>" for n in names) + "</ul>")
    if pending_leave or pending_punch:
        msg = f"Waiting for approval: {pending_leave} leave request(s), {pending_punch} correction request(s)."
        lines += [msg, ""]
        html.append(f"<p style='font-family:sans-serif'><b>{msg}</b></p>")
    if offline:
        msg = "Device problem: " + ", ".join(offline) + " - see the Devices page."
        lines += [msg, ""]
        html.append(f"<p style='font-family:sans-serif;color:#c62828'><b>{escape(msg)}</b></p>")
    return subject, "\n".join(lines), "".join(html)


def send_email(settings, recipients, subject, text, html=None):
    host = (settings.get("smtp_host") or "").strip()
    if not host:
        raise NotifyError("Set up the email server in Settings -> Email first.")
    if not recipients:
        raise NotifyError("Add at least one recipient in Settings -> Email.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.get("smtp_from") or settings.get("smtp_user") or f"attendance@{host}"
    msg["To"] = ", ".join(recipients)
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    port = int(settings.get("smtp_port") or 587)
    security = settings.get("smtp_security") or "starttls"
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
        with server:
            if settings.get("smtp_user"):
                server.login(settings["smtp_user"], settings.get("smtp_password") or "")
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise NotifyError(f"Could not send email via {host}:{port}: {exc}") from exc


def recipients_of(settings):
    return [r.strip() for r in (settings.get("report_recipients") or "").split(",") if r.strip()]


def due(settings, now=None):
    """True when today's report time has passed and it has not been sent today."""
    now = now or datetime.now()
    hm = att.parse_hm(settings.get("report_time"))
    if not hm or not settings.get("smtp_host") or not recipients_of(settings):
        return False
    return (now.hour, now.minute) >= hm and settings.get("report_last_sent") != now.date().isoformat()


def send_daily_report(db, force=False, now=None):
    """Send today's summary if due (or always with ``force``); returns the recipients or []."""
    settings = dbmod.get_settings(db)
    now = now or datetime.now()
    if not force and not due(settings, now):
        return []
    recipients = recipients_of(settings)
    subject, text, html = build_daily_summary(db, now.date())
    send_email(settings, recipients, subject, text, html)
    dbmod.set_setting(db, "report_last_sent", now.date().isoformat())
    db.commit()
    return recipients


class ReportScheduler(threading.Thread):
    """Checks once a minute whether the daily summary is due."""

    def __init__(self, database):
        super().__init__(daemon=True, name="daily-report")
        self.database = database
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        retry_after = None
        while not self._stop_event.wait(60):
            if retry_after and datetime.now() < retry_after:
                continue
            retry_after = None
            try:
                conn = dbmod.connect(self.database)
                try:
                    sent = send_daily_report(conn)
                    if sent:
                        log.info("Daily summary sent to %s", ", ".join(sent))
                finally:
                    conn.close()
            except NotifyError as exc:
                log.warning("Daily summary not sent (retrying in 30 minutes): %s", exc)
                retry_after = datetime.now() + timedelta(minutes=30)
            except Exception:
                log.exception("Daily summary failed")
