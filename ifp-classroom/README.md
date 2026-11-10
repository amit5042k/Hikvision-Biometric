# IFP Classroom Attendance

A standalone, self-hosted app for schools. It uses the camera on each classroom
**interactive flat panel (IFP)** to recognise students and teachers by face,
mark attendance, and record when each person **enters** and **leaves** the
room. You view and download the records from any PC on the school network.

It is a separate app with its own database, logins and settings. It does not
connect to any other attendance software.

- **Works on Android-only panels** (no OPS PC needed). Nothing is installed on
  the panel except Chrome.
- **Face recognition runs on the panel**, inside the browser. No video leaves
  the classroom. The panel sends only "person seen at this time".
- **Entry / exit:** entry is the first time a person is seen. Exit is the last
  time they were seen, once they have been out of view for the minutes you set
  per room.
- **Reports on your PC:**
  - Class register per room and day, with per-period attendance.
  - Entry/exit log.
  - Monthly register with attendance %.
  - Excel, CSV and print.
- **Enrolment:**
  - Add students and teachers one by one or from a CSV.
  - Add face photos by upload, from a camera, or in bulk with files named by
    admission number.
- **Reliable on the panel:** records are kept through network outages, the
  panel's clock is corrected by the server, and slow panels switch to a faster
  detector by themselves.
- **Logins:** admin and viewer (read-only) logins, sign-in lockout and an audit
  log.

## Installing the server

Use a PC or server that stays on during school hours. The panels and your PC
must be able to reach it over the network. It needs Python 3.10 or later.

**Windows:**
1. Install Python from python.org and tick *Add Python to PATH*.
2. Copy this `ifp-classroom` folder to the PC.
3. Double-click `start_server_windows.bat`.

**Linux / macOS:**

```bash
cd ifp-classroom
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CLASSROOM_DATA_DIR=./data python run.py      # serves on http://0.0.0.0:8000
```

Then:

1. Open `http://<server-ip>:8000` on your PC and sign in as **admin / admin**.
2. Change the password straight away under **Settings**.
3. Follow **[docs/SETUP.md](docs/SETUP.md)**: classrooms, people, face photos,
   Android panel setup, accuracy tips and privacy notes.

Give the server PC a fixed IP address, so that the panels' kiosk links keep
working.

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Listen address |
| `CLASSROOM_DATA_DIR` | `instance/` | Folder for the database, session key and backups |
| `SECRET_KEY` | generated once in the data folder | Session signing key |
| `BEHIND_PROXY` / `HTTPS_ONLY` | off | Set to `1` behind an HTTPS reverse proxy |

Commands:

```bash
flask --app classroom_app backup        # copy the database into <data dir>/backups
flask --app classroom_app reset-admin   # reset the admin password to "admin"
```

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

```
classroom_app/engine.py     sightings -> entry/exit sessions, class & monthly registers
classroom_app/views.py      classroom pages, enrolment, kiosk API (/kiosk/<link>, /api/class/<link>/...)
classroom_app/admin.py      sign-in, users, school settings, holidays, audit log, backup
classroom_app/db.py         database setup and migrations (schema.sql is the baseline)
classroom_app/static/faceapi/     face-api (MIT, vladmandic) and its models, served locally
classroom_app/static/classroom/   kiosk and enrolment JavaScript
run.py                      production entry point (waitress)
```
