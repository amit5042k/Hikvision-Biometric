# Classroom attendance with the IFP camera (Android panels)

This app uses the camera on each classroom **interactive flat panel (IFP)** to
recognise students and teachers by face. It records when each person **enters**
and **leaves** the room and keeps a daily and monthly attendance register. You
view and download the records from any PC in the school.

It is built for **Android-only panels (no OPS PC module)**. Nothing is installed
on the panel except a browser.

```
 Classroom panel (Android, Chrome)                 School server (this app)              Your PC
 ┌──────────────────────────────┐   "seen" records   ┌───────────────────────┐   browser   ┌──────────┐
 │ camera → face recognition     │ ─────────────────▶ │ entries / exits        │ ◀────────── │ reports, │
 │ (runs inside the browser)     │ ◀───────────────── │ class & monthly        │             │ Excel    │
 └──────────────────────────────┘   enrolled faces    │ registers              │             └──────────┘
                                                      └───────────────────────┘
```

* The panel downloads the face data of enrolled people and does the recognition
  itself. It sends only "person X seen at time T". No video leaves the room and
  nothing is recorded.
* **Entry** is the first time a person is seen. **Exit** is the last time they
  were seen, once they have been out of view for the minutes set for that
  classroom (default 10). Coming back later starts a new entry.
* If the network drops, the panel keeps its records and sends them when the
  network returns. The server corrects the panel's clock if it is wrong.

## 1. The server

Run the app on an always-on PC or server on the school network (see
[README](../README.md) → *Installing the server*). Every panel and your PC must
be able to open its address, e.g. `http://192.168.1.10:8000`.

> **Tip:** Android Chrome only gives a web page the camera when the page is
> **HTTPS**. If your server already has an `https://` address (hosted online,
> or behind a reverse proxy with a certificate), the panel works with no extra
> step. On a plain `http://` LAN server, do the one-time Chrome flag in step 3.

## 2. Set up classrooms and people (on your PC)

1. Sign in as admin. Open **Classroom → Classrooms**.
2. **Add classroom** for each room with a panel, e.g. `Class 8-A`.
3. Optional: add **timetable periods**. The register then shows who was present in each period.
4. Under **Weekly off**, tick the school's weekly off days. Add holidays on the **Holidays** page.
5. Open **Students & Teachers**. Add people one at a time, or **Import from CSV** with these columns:

   | Number | Name | Role | Class | Classroom | Active |
   |---|---|---|---|---|---|
   | S1023 | Asha Verma | student | 8-A | Class 8-A | 1 |
   | T014 | Mrs Iyer | teacher | | | 1 |

   *Classroom* is the home room. Students are marked **absent** in that room's
   register when the camera does not see them. Teachers can be left without a
   room: they appear in the register of any room they are seen in.

6. **Add face photos.** A panel only recognises people who have at least one face photo.
   * **Bulk:** open **Bulk face photos** and select many photos at once. Each
     file is named after the person's number: `S1023.jpg`. Extra photos of the
     same person are named `S1023_2.jpg`. ID-card photos work.
   * **One person:** on their page, click **Upload photos** or **Use camera**.
   * 2–5 photos per person gives the best results. One of them should ideally
     come from the classroom panel itself: sign in on the panel's browser, open
     the person's page and use **Use camera**.

Photos are processed in the browser. The server stores only the face data (128
numbers) and a small thumbnail, never the original photo.

## 3. Set up each Android panel (one time, about 5 minutes)

1. **Install Google Chrome** if the panel does not have it, from Play Store or
   the panel's app store. Older built-in browsers may not support the camera
   or the face models.
2. **Plain `http://` server only:** allow the camera for your server.
   1. In Chrome, open `chrome://flags`.
   2. Search for **Insecure origins treated as secure**.
   3. Enter the server address exactly, e.g. `http://192.168.1.10:8000`.
   4. Set it to **Enabled** and tap **Relaunch**.
3. On your PC, copy the room's **kiosk link** from **Classrooms**. Open it in
   Chrome on that room's panel. When asked, tap **Allow** for the camera.
4. In the Chrome menu, tap **Add to Home screen**. The kiosk then opens like an
   app.
5. In the panel's settings, turn off **screen sleep / auto power-off** during
   school hours. The kiosk also asks the panel to keep the screen on.
6. If the panel has more than one camera, choose the right one in the dropdown
   at the bottom right. The choice is remembered.

### Keeping it running alongside lessons

**Android switches the camera off for any app that is not on screen.** While a
teacher uses the whiteboard or another app full screen, the kiosk cannot see,
so nobody is recorded. Ways to handle this, best first:

1. **Split screen or floating window.** Most Android 10+ panels can show two
   apps side by side or as a small pop-up window. Tap **Compact** on the kiosk
   and keep it in a corner window next to the lesson app. Both stay active.
2. **Roll call at the start of each period.** Keep the kiosk full screen for
   the first 2–3 minutes, then switch to the lesson. Entries are recorded.
   Exits are recorded as the last time each person was seen. Set the room's
   *Exit after unseen for* to the length of a period (e.g. 40 minutes) so
   pupils are not marked as having left mid-lesson.
3. **Kiosk-browser app.** Apps such as Fully Kiosk Browser can start the kiosk
   link when the panel boots, keep the screen on and grant camera permission.
   Check its licence terms for school use.

When the kiosk returns to the screen it restarts the camera by itself. Records
from before are kept.

### Camera and accuracy tips

* Faces must be visible and lit. A panel camera (usually 1080p or 4K,
  wide-angle, above the screen) reliably recognises faces up to about 5–6
  metres. The back rows of a large room may be missed. Check the room by
  watching the boxes on the kiosk.
* On an older panel, set **Face detector** to *Fast* under **Classrooms →
  Recognition settings**. The kiosk also switches itself to *Fast* when the
  accurate detector is too slow for that panel.
* If two people get mixed up, lower **Match strictness** (e.g. 0.45) and add
  more photos of both. If people are often not recognised, raise it a little
  (0.55) or add a photo taken by that panel.
* Panels pick up new people, photos and settings within 5 minutes.

## 4. Viewing the records on your PC

Open the server address in any browser on your PC and sign in.

| Page | What it shows | Download |
|---|---|---|
| **Classrooms** | each panel online/offline, who is in class now, present today | |
| **Class Register** | per room and day: present/absent, first entry, last exit, time in class, number of entries, per-period P/A | Excel, CSV, Print |
| **Entry / Exit Log** | every entry and exit with time, room and person; filter by room or person | CSV |
| **Monthly Register** | P/A/H/WO grid per student for the month, totals and attendance % | Excel, CSV |
| **Students & Teachers** → person | their face photos and entries/exits on any day | |

Class teachers and the principal can get **viewer** logins (Settings → Users & logins). Viewers see all
reports but cannot change anything.

## 5. Privacy and limits (please read)

* **Consent.** This system processes biometric data of children. Under India's
  Digital Personal Data Protection Act 2023, the school must obtain verifiable
  consent from a parent or guardian, and must tell staff and parents what is
  collected and why. Do not enrol anyone without consent.
* **What is stored:** face data (numbers), a small face thumbnail and the times
  each person was seen. Deleting a person removes all of these. Delete
  students who leave the school.
* **The kiosk link is a password.** Anyone with it can download the room's
  face data. Share it only with the panel. Click **New link** to cancel an old
  one.
* **Not proof against spoofing.** A clear photo of a student held up to the
  camera can be recognised as that student. Treat the register as attendance
  support, not a security control.
* Recognition is not perfect. Teachers should glance at the register, and
  absent marks should be checked before any action is taken on them.
