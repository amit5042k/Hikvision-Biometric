# IFP Classroom Attendance

A standalone, self-hosted system for schools. It uses the camera on each
classroom **interactive flat panel (IFP)** to recognise students and teachers by
face, mark attendance, and record when each person **enters** and **leaves** the
room. It has its own database, logins and settings, and does not connect to any
other attendance software.

**Your Windows PC is the server**: it stores all data and runs all analysis.
**Every classroom's Android panel runs the IFP Classroom app as a client**, and
**each classroom has its own login** on the same server. The panel signs in
with it, and the class teacher uses it to see only that class's reports.

- **Android client app** (`android/`, ready-made APK served by the server at
  `/app`).
  - Recognises faces on the panel: no video leaves the room.
  - Works with a plain `http://` server (no Chrome settings needed).
  - Starts when the panel is switched on, keeps the screen awake, and has a
    PIN-protected settings screen.
  - Keeps records through network or server outages.
- **Windows server**: one installer script (`windows/`).
  - Starts with Windows and opens the firewall port.
  - Backs up the database daily.
- **Analysis on the server:**
  - Analytics: attendance trends, class-wise %, students below 75%, late
    arrivals, teacher time in class, room usage.
  - Class register (with periods), entry/exit log and monthly register.
  - Excel and CSV downloads.
- **Enrolment:** add students and teachers one by one or from a CSV. Add face
  photos by upload, from a camera, or in bulk with files named by admission
  number.
- **Logins:**
  - Admin.
  - Viewer: all classes, read-only.
  - One classroom login per room: its panel, plus its class teacher's reports.
  - Sign-in lockout and an audit log.

## Install

**Illustrated setup guide and user manual (PDF, every feature explained): [docs/IFP-Classroom-Guide.pdf](docs/IFP-Classroom-Guide.pdf).** A shorter text version is in [docs/SETUP.md](docs/SETUP.md).

1. **Server (Windows PC):**
   1. Install Python 3.10+ from python.org, ticking *Add Python to PATH*.
   2. Copy this folder to the PC.
   3. Run `windows\install_windows_server.bat` as administrator.
   4. Open `http://<PC-IP>:8000` and sign in as **admin / admin**. Change the
      password.
2. **Set up:** add classrooms and give each its own login (Classrooms → Manage
   classrooms → Create login). Add students, teachers and face photos.
3. **Each panel:**
   1. Open `http://<PC-IP>:8000/app` in the panel's browser and install the app.
   2. Sign in with the server address and that classroom's login.
   3. Tap **Sign in and start**.

Running on Linux or macOS instead:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CLASSROOM_DATA_DIR=./data python run.py      # http://0.0.0.0:8000
```

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Listen address |
| `CLASSROOM_DATA_DIR` | `instance/` (`data\` with the Windows installer) | Database, session key, backups, logs |
| `SECRET_KEY` | generated once in the data folder | Session signing key |
| `BEHIND_PROXY` / `HTTPS_ONLY` | off | Set to `1` behind an HTTPS reverse proxy |

```bash
flask --app classroom_app backup        # copy the database into <data dir>/backups
flask --app classroom_app reset-admin   # reset the admin password to "admin"
```

## Building the Android app

The APK in `classroom_app/static/app/ifp-classroom.apk` is ready to use. To
rebuild it, for example after changing the kiosk code:

1. Install Android Studio, or the Android SDK command-line tools with platform
   34 and build-tools 34.
2. Build:

   ```bash
   cd android
   ./gradlew assembleRelease -PIFP_KEYSTORE=/path/ifp-classroom-release.jks \
       -PIFP_KEYSTORE_PASSWORD=... -PIFP_KEY_ALIAS=ifpclassroom -PIFP_KEY_PASSWORD=...
   cp app/build/outputs/apk/release/app-release.apk ../classroom_app/static/app/ifp-classroom.apk
   ```

The kiosk screen (`classroom_app/static/classroom/` plus the face models) is
copied into the app at build time, so the app and the server's `/kiosk/` page
always run the same code.

Sign updates with the **same keystore**, or panels must uninstall the old app
first. Keep the keystore private and backed up; it is not stored in this
repository.

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
classroom_app/static/classroom/   kiosk and enrolment JavaScript (shared with the Android app)
classroom_app/static/app/   the Android APK, offered to panels at /app
android/                    Android client app (WebView + in-app secure origin, boot start, foreground service)
windows/                    Windows server installer, start-up task, backup, uninstaller
docs/guide/                 source of the PDF guide (guide.html + screenshots; rebuild with `node build_pdf.js`)
run.py                      production entry point (waitress)
```
