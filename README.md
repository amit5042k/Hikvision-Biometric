# Hikvision Attendance Management

A self-hosted web app for attendance, built for the **Hikvision DS-K1T320EFWX** face-recognition terminal. It also works with other Hikvision access-control terminals that support ISAPI (DS-K1T341, DS-K1T671, DS-K1T804 and similar).

It pulls punches from the terminal over the LAN, applies shifts, holidays and leave, and produces daily attendance, a monthly muster roll and payroll summaries.

## Features

- **Device integration (ISAPI, digest auth)**
  - Test the connection and read the model, serial number and firmware.
  - Download punches automatically on a schedule, or on demand for any date range. Paging and de-duplication are handled.
  - Real-time push: the terminal's "HTTP listening" mode can be configured from the app.
  - Import users from the terminal, or send employees and their card numbers to it.
  - Enrol a face photo remotely, remove users from the terminal, sync the clock, open the door.
  - Supports several terminals.
- **Employees**: departments, designations, shifts, cards, joining date, active/inactive, CSV import and export, bulk shift and department assignment.
- **Shifts**:
  - Start and end time, including overnight shifts.
  - Late and early-exit grace periods, unpaid break.
  - Half-day and full-day hour thresholds, weekly offs.
- **Attendance engine**: works out Present, Absent, Half Day, Missing Punch, Leave, Holiday and Weekly Off, plus late minutes, early-exit minutes, work hours and overtime.
  - Attendance is always derived from the raw punches. Correcting a punch, shift, holiday or leave updates every report straight away.
- **Reports**:
  - Daily attendance with filters.
  - Monthly muster roll and monthly summary with payable days.
  - Per-employee calendar.
  - Raw punch log.
  - Every report exports to CSV and has a print layout.
- **Leave and holiday management**, and manual punch correction with an audit note.
- **Users**: admin and read-only viewer roles, CSRF-protected forms, and a responsive layout that works on phones.

## Quick start

Requires Python 3.10 or later.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py                        # serves on http://0.0.0.0:5000
```

1. Open `http://<server-ip>:5000` and sign in as **admin / admin**. Change the password straight away under **Settings**.
2. **Devices → Add device**: enter the terminal's IP address, admin username and password, and its UTC offset (`+05:30` for India). Click **Test connection**.
3. Click **Import users from device**, or add employees in the app and push them to the terminal.
4. Set up **Shifts** and **Holidays**, then assign shifts to employees. You can select several employees on the Employees page and assign a shift in one step.
5. Punches are downloaded every 5 minutes, which you can change in Settings. For instant updates, open **Real-time push** on the device card and enter this server's LAN IP.

Configuration through environment variables (all optional):

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `5000` | Listen address |
| `ATTENDANCE_DATA_DIR` | `instance/` | Folder for the database, session key and backups. On a server, keep it **outside** the code folder |
| `ATTENDANCE_DB` | `<data dir>/attendance.db` | Override the database path only |
| `SECRET_KEY` | generated once in `<data dir>/secret_key` | Session signing key |
| `BEHIND_PROXY` / `HTTPS_ONLY` | off | Set to `1` behind nginx/Passenger and when served over HTTPS |

## Hosting online (GoDaddy etc.)

See **[docs/DEPLOY_GODADDY.md](docs/DEPLOY_GODADDY.md)** for step-by-step instructions for GoDaddy cPanel hosting and GoDaddy VPS. For a non-technical, illustrated overview (features, what is done, go-live checklist), see **[docs/Attendance-System-Guide.pdf](docs/Attendance-System-Guide.pdf)**.

- **The terminal stays on the office LAN.** A small **bridge agent** (`bridge/hik_bridge.py`) on an office PC relays punches and device actions over outgoing HTTPS. When adding the device, set its connection to *Through the bridge agent*.
- **Updates keep all data.** Run `deploy/update.sh` to update. It backs up the database, pulls the code, installs packages and restarts. Database changes ship as numbered migrations in `app/db.py`, which upgrade the existing database in place. Never edit `app/schema.sql`.

Other commands:

```bash
flask --app app sync          # download punches from all devices once (e.g. from cron)
flask --app app reset-admin   # reset the admin password to "admin"
flask --app app backup        # copy the database into <data dir>/backups
```

## Terminal preparation (DS-K1T320EFWX)

- Give the terminal a fixed IP on the same network as the server.
- Turn on ISAPI. In the terminal's web UI, go to *Configuration → Network → Network Service / Integration Protocol* and enable ISAPI.
- Set the terminal's time zone and time. The **Sync device clock** button sets the time from the server.
- The employee number in this app is the terminal's **Employee ID**. Keep them identical.
- Which events count as punches is set by **Settings → Event minor codes**:
  - The default is `75,1,38`: face verified, card verified, fingerprint verified.
  - Use `0` to accept all events, if your firmware reports successful verifications under other codes.

## How attendance is calculated

- **Which day a punch belongs to:** for each day, punches from *shift start − 4 h* to *24 h later* count toward that day. The 4 hours is configurable. This keeps overnight shifts on one day.
- **In and out times:** the first punch is IN and the last punch is OUT. Work time is OUT − IN, minus the unpaid break.
- **Day status:**
  - At least the full-day hours → **P**.
  - At least the half-day hours → **HD**.
  - Fewer hours → **A**.
  - A single punch on a past day → **MP** (missing punch). It counts as present until you correct it.
- **Late:** the first punch is after shift start + grace. The value shown is minutes after shift start.
- **Early exit:** the last punch is before shift end − grace.
- **Overtime:** time after shift end, once it reaches the minimum overtime setting. All hours worked on a holiday or weekly off count as overtime.
- **Payable days** = present (half day = 0.5) + leave + holidays + weekly offs.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The tests run against an in-process fake ISAPI terminal, so no hardware is needed.

Project layout:

```
app/hikvision.py   ISAPI client (users, cards, faces, events, time, door, push config)
app/device_actions.py  terminal operations shared by the server and the bridge agent
app/sync.py        device <-> database sync, job dispatch (direct / bridge), push receiver, scheduler
app/bridge_api.py  API used by the bridge agent
app/db.py          schema migrations, backups
bridge/            on-site bridge agent (Windows task / systemd)
deploy/            GoDaddy / VPS deployment, update and backup scripts
passenger_wsgi.py  entry point for cPanel "Setup Python App"
app/attendance.py  attendance rules engine
app/routes.py      web UI and CSV exports
app/templates/     Jinja templates
run.py             production entry point (waitress + scheduler)
```
