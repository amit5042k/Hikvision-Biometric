# IFP Classroom Attendance — setup guide

```
 Classroom panels (Android app)               Your Windows PC = the server               Any PC / phone
 ┌───────────────────────────────┐  "seen"    ┌──────────────────────────────┐  browser  ┌──────────────┐
 │ IFP Classroom app             │ ─────────▶ │ stores entries / exits       │ ◀──────── │ reports,     │
 │ camera → face recognition     │ ◀───────── │ registers, analytics, Excel  │           │ analytics,   │
 │ (runs on the panel)           │  face data │ http://<PC-IP>:8000          │           │ downloads    │
 └───────────────────────────────┘            └──────────────────────────────┘           └──────────────┘
```

* **Server: your Windows PC** (the IT administrator's PC). It holds all data and
  runs all analysis: registers, entry/exit log, monthly register and
  analytics.
* **Client: the IFP Classroom Android app** on every classroom panel. It
  recognises faces on the panel and sends only "person X seen at time T". No
  video leaves the room and nothing is recorded.
* **One login per classroom, same server.** Each classroom has its own username
  and password, e.g. `class8a`. That classroom's panel app signs in with it,
  and the class teacher can use it in a browser to see **only that class's**
  register, entry/exit log, monthly register and analytics. Your admin login
  sees and manages everything.
* **Entry** is the first time a person is seen. **Exit** is the last time they
  were seen, once they have been out of view for the minutes set for that room
  (default 10).
* If the network or the server PC is down, the panel keeps its records and
  sends them later. The server corrects the panel's clock if it is wrong.

## 1. Install the server on your Windows PC (once)

1. Install **Python 3.10 or newer** from python.org. During installation,
   tick **Add Python to PATH**.
2. Copy the `ifp-classroom` folder to the PC, e.g. `C:\IFPClassroom`.
3. Open `windows`, right-click **`install_windows_server.bat`** and choose
   **Run as administrator**. The script:
   - installs the app;
   - makes the server start automatically whenever Windows starts, even
     before anyone signs in;
   - opens TCP port **8000** in Windows Firewall, for private and domain
     networks;
   - schedules a daily database backup at 17:30 into `data\backups`.
4. When it finishes, it shows the PC's address, e.g. `http://192.168.1.10:8000`.
   Open it in a browser and sign in as **admin / admin**. **Change the password
   straight away** under Settings.
5. **Give the PC a fixed IP address** (DHCP reservation in the router, or a
   static IP in Windows). The panels use this address to find the server.
6. Keep the PC switched on during school hours. Set Windows **Sleep** to
   *Never* when plugged in. If the PC is off, the panels keep their records
   and send them when it is back.

All data lives in `ifp-classroom\data`: the database, backups and logs. To
uninstall, run `windows\uninstall_windows_server.bat` as administrator. The
`data` folder is kept.

If the panels cannot connect, check that the Windows network profile is
**Private**, not Public. Any other firewall or antivirus product must also
allow port 8000.

## 2. Set up classrooms and people (in the browser on your PC)

1. Open **Classrooms** and click **Add classroom** for every classroom, e.g.
   `Class 8-A`.
2. For each classroom, click **Edit**, enter a username and password (e.g.
   `class8a`), and click **Create login**. Note them down for the panel and
   the class teacher. **Update login** changes the username, or the password
   when you type a new one.
3. Optional: add **timetable periods**. The register then shows attendance per
   period, and Analytics counts **late arrivals**: first seen more than *N*
   minutes after the first period starts.
4. In **Recognition settings**, tick the school's **weekly off** days. Add
   holidays on the **Holidays** page.
5. Open **Students & Teachers**. Add people one at a time, or **Import from CSV**
   with these columns:

   | Number | Name | Role | Class | Classroom | Active |
   |---|---|---|---|---|---|
   | S1023 | Asha Verma | student | 8-A | Class 8-A | 1 |
   | T014 | Mrs Iyer | teacher | | | 1 |

   *Classroom* is the home room. Students are marked **absent** in that room's
   register when the camera does not see them. Teachers can be left without a
   room: they appear in the register of any room they are seen in.

6. **Add face photos.** A panel only recognises people who have at least one
   face photo.
   - **Bulk:** open **Bulk face photos** and select many photos at once. Each
     file is named after the person's number: `S1023.jpg`. Extra photos of the
     same person are named `S1023_2.jpg`. ID-card photos work.
   - **One person:** on their page, use **Upload photos** or **Use camera**.
   - 2–5 photos per person gives the best results.

   Photos are processed in the browser. The server stores only the face data
   (128 numbers) and a small thumbnail.

## 3. Install the app on each Android panel (about 5 minutes per panel)

1. On the panel, open the panel's browser and go to `http://<PC-IP>:8000/app`,
   e.g. `http://192.168.1.10:8000/app`. Tap **Download the app**.
2. Open the downloaded file. If Android asks, allow **Install unknown apps**
   for the browser or file manager, then tap **Install**.
3. Open **IFP Classroom** and sign in:
   - **Server address:** your PC's address, e.g. `192.168.1.10:8000`. Just
     `192.168.1.10` also works; port 8000 is assumed.
   - **Username / password:** **that classroom's login** from step 2.2.
4. Set the options:
   - **Open automatically when the panel is switched on** (recommended).
   - **Keep recognising while another app is on screen**: see *During lessons*
     below.
   - **Settings PIN**: stops students from changing the room.
5. Tap **Sign in and start**, then allow the camera. The panel now records
   attendance for that classroom. The password is not stored on the panel.

On Android 10 and later, also tap **Allow "Display over other apps"** in the
setup screen. Android needs this before an app can open itself at power-on.
Many panels also have their own *Boot app / Start-up app* setting; set it to
IFP Classroom if available.

To sign the panel in to another classroom, tap **⚙ App settings** on the
kiosk screen, enter the PIN if one is set, and sign in with the other
classroom's login. **New link** on the Classrooms page signs that room's panel
out; sign it in again with the login. To update the app, download and install it again
from `/app`. Settings are kept.

### During lessons

Android limits camera use by an app that is not on screen. The app asks
Android to keep the camera running, using a foreground service with a
"Classroom attendance is running" notification. How much Android allows
depends on the panel's Android version and manufacturer, so **test it on your
panel model**:

1. Open the app and let it recognise someone.
2. Switch to the whiteboard app for 5 minutes and walk in front of the camera.
3. Open **Entry / Exit Log** on the server PC and check that you were recorded.

If that doesn't work on your panel model, use one of these:

* **Split screen or floating window.** Show the app beside the lesson; tap
  **Compact** for a smaller layout.
* **Roll call.** Keep the app on screen for the first 2–3 minutes of each
  period. Set the room's *Exit after unseen for* to the length of a period
  (e.g. 40 minutes) so students are not marked as leaving mid-lesson.

### Camera and accuracy tips

* A panel camera (1080p or 4K, wide-angle) reliably recognises faces up to
  about 5–6 metres. Back rows of a large room may be missed. Watch the boxes
  on the kiosk screen to check coverage.
* On an older panel, set **Face detector** to *Fast*. The app also switches
  itself to *Fast* when the accurate detector is too slow for that panel.
* If two people get mixed up, lower **Match strictness** (e.g. 0.45) and add
  more photos of both. If people are often not recognised, raise it slightly
  (0.55) or add a photo taken by that panel.
* Panels pick up new people, photos and settings within 5 minutes.

## 4. Analysing the data (on the server)

| Page | What it shows | Download |
|---|---|---|
| **Analytics** | average attendance, daily attendance chart, class-wise %, students below the threshold (default 75%), late arrivals, teacher time in classrooms, room usage; for this week / month / last 30 days / academic year / any dates, per class | Students, daily and teacher CSVs |
| **Classrooms** | each panel online/offline, who is in class now, present today | |
| **Class Register** | per room and day: present/absent, first entry, last exit, time in class, per-period P/A | Excel, CSV, print |
| **Entry / Exit Log** | every entry and exit with time, room and person | CSV |
| **Monthly Register** | P/A/H/WO grid per student, totals and % | Excel, CSV |

**Who sees what:**

| Login | Sees | Can change |
|---|---|---|
| **Admin** (you) | everything | everything |
| **Viewer**, e.g. principal (Settings → Users & logins) | every class's reports | nothing |
| **Classroom**, e.g. `class8a` (Classrooms → Edit) | only that class: register, entry/exit log, monthly register, analytics, and its own students' pages | its own password | Any PC or
phone on the school network can open `http://<PC-IP>:8000`.

## 5. Privacy and limits (please read)

* **Consent.** This system processes biometric data of children. Under India's
  Digital Personal Data Protection Act 2023, the school must obtain verifiable
  consent from a parent or guardian. It must also tell staff and parents what
  is collected and why. Do not enrol anyone without consent.
* **What is stored**, on the server PC only: face data (numbers), a small face
  thumbnail and the times each person was seen. Deleting a person removes all
  of these. Delete students who leave. The backups contain the same data, so
  keep them safe.
* **Classroom logins and kiosk links are passwords.** Anyone with one can
  download that room's face data. Use strong passwords. If one leaks, change
  the login's password and click **New link** for that room, then sign the
  panel in again.
* **Not proof against spoofing.** A clear photo held up to the camera can be
  recognised as that person. Treat the register as attendance support, not a
  security control.
* Recognition is not perfect. Teachers should glance at the register, and
  absent marks should be checked before any action is taken.
