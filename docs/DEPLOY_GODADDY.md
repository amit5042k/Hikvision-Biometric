# Hosting on GoDaddy: step-by-step guide

This guide puts the attendance app online on GoDaddy. It then shows how to ship bug fixes and new features from git **without losing logins or attendance data**.

---

## 0. How the pieces fit together

```
  Office LAN                                    Internet
 ┌───────────────────────────────┐            ┌──────────────────────────────┐
 │ DS-K1T320EFWX                 │            │ GoDaddy hosting              │
 │ 192.168.1.64                  │            │ https://attendance.your.com  │
 │      ▲                        │  HTTPS     │  • web app                   │
 │      │ LAN (ISAPI)            │ (outgoing  │  • database (attendance.db)  │
 │ ┌────┴──────────────┐         │   only)    │                              │
 │ │ Bridge PC         │─────────┼───────────►│  /api/bridge/...             │
 │ │ bridge/hik_bridge │         │            └──────────────────────────────┘
 │ └───────────────────┘         │                       ▲
 └───────────────────────────────┘                       │ browser / phone
                                                    HR, managers
```

**Why a "bridge"?** Your terminal has a private office address such as `192.168.1.64`. A GoDaddy server on the internet cannot reach that address. The bridge is a small program (`bridge/hik_bridge.py`) that runs on any always-on PC in the office:

- It reads punches from the terminal over the LAN and uploads them to your GoDaddy site.
- It carries out the actions you click on the site: send an employee to the terminal, enrol a face, open the door, sync the clock, and so on.
- It only makes **outgoing** HTTPS requests. You do not need port forwarding, a static IP or router changes.

---

## 1. Choose the right GoDaddy product

| GoDaddy product | Works? | Notes |
|---|---|---|
| **Web Hosting (Linux, cPanel)**: Economy / Deluxe / Ultimate | ✅ Recommended | cPanel must show **Setup Python App** under *Software*. → Part A |
| **VPS Hosting** (Ubuntu) | ✅ | Full control, more admin work. → Part B |
| Website Builder, Managed WordPress, Windows (Plesk) hosting | ❌ | These cannot run Python web apps |

> Not sure? Log in to GoDaddy → **My Products** → **Web Hosting → Manage** → **cPanel Admin**. Search for "Python". If **Setup Python App** is there, follow Part A. If it is missing, ask GoDaddy support to enable it, or use a VPS (Part B).

---

## 2. Before you start (once)

1. **Put the code on a deploy branch.** This repository's work is on the branch `ccr-9a6085eb-e2f36j`. On GitHub, open the repository → **Branches** → **New branch**. Name it `main`, base it on `ccr-9a6085eb-e2f36j`, then make `main` the default branch (**Settings → Branches**). Everything below deploys `main`.
2. **Pick a web address**, for example `attendance.yourcompany.com`. A sub-domain keeps it separate from your main website.
3. **Choose an office PC for the bridge.** It must be on the same network as the terminal and switched on all day. Windows 10/11 or Linux are both fine.
4. **Give the terminal a fixed IP address**, either in the router's DHCP reservation or in the terminal's network settings. Then enable ISAPI on the terminal:
   - In the terminal's web page, go to **Configuration → Network → Advanced Settings → Integration Protocol**.
   - Tick **Enable ISAPI**.

---

## Part A: GoDaddy cPanel Web Hosting

### A1. Create the sub-domain

1. cPanel → **Domains** → **Create A New Domain** (on older cPanel: **Subdomains**).
2. Domain: `attendance.yourcompany.com`.
   - **Untick** "Share document root".
   - Leave the suggested document root as it is.
3. Click **Submit**. If the domain's DNS is at GoDaddy, it starts working within a few minutes.

### A2. Open a terminal on the server

- cPanel → **Advanced → Terminal**.
- If Terminal is not shown, go to GoDaddy → **Web Hosting → Manage → Settings → Server → SSH Access → Enable**. Then connect with `ssh <cpanel-username>@<your-server-ip>` using PuTTY or Windows Terminal.

All commands below are typed into this terminal.

### A3. Give the server read-only access to your GitHub repository

The repository is private, so the server needs a key:

```bash
ssh-keygen -t ed25519 -C "godaddy-attendance" -f ~/.ssh/github_attendance -N ""
cat ~/.ssh/github_attendance.pub
```

1. Copy the line that is printed.
2. On GitHub, open the repository → **Settings → Deploy keys → Add deploy key**.
3. Title: `GoDaddy`. Paste the key. Leave **write access unticked**. Click **Add key**.

Tell SSH to use that key for GitHub. This also routes over port 443, which works even where port 22 is blocked:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/github_attendance
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com     # answer "yes"; expect "Hi amit5042k/Hikvision-Biometric! You've successfully authenticated"
```

### A4. Download the code

```bash
cd ~
git clone -b main git@github.com:amit5042k/Hikvision-Biometric.git
mkdir -p ~/attendance-data      # the database and backups live HERE, outside the code
```

> 🔒 `~/attendance-data` is the only place your data is stored. It is outside the code folder and is not part of git, so updating the code never touches it. Never delete it.

### A5. Create the Python application

cPanel → **Software → Setup Python App** → **Create Application**:

| Field | Value |
|---|---|
| Python version | the highest offered (3.10 or newer is recommended; at least 3.9) |
| Application root | `Hikvision-Biometric` |
| Application URL | `attendance.yourcompany.com` (leave the path empty) |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |
| Passenger log file | `/home/<cpanel-username>/Hikvision-Biometric/stderr.log` |

Under **Environment variables**, click **Add Variable** for each of these:

| Name | Value |
|---|---|
| `ATTENDANCE_DATA_DIR` | `/home/<cpanel-username>/attendance-data` |
| `HTTPS_ONLY` | `1` |

Click **Create**.

> ⚠️ cPanel sometimes replaces `passenger_wsgi.py` with its own "It works!" sample. Put ours back:
> ```bash
> cd ~/Hikvision-Biometric && git checkout -- passenger_wsgi.py
> ```

### A6. Install the Python packages

At the top of the application's page, cPanel shows a command like this:

```
source /home/<user>/virtualenv/Hikvision-Biometric/3.11/bin/activate && cd /home/<user>/Hikvision-Biometric
```

Copy it, paste it into the terminal, then run:

```bash
pip install -r requirements.txt
```

Back in **Setup Python App**, click **Restart**.

### A7. Turn on HTTPS

- cPanel → **Security → SSL/TLS Status**.
- Tick `attendance.yourcompany.com` → **Run AutoSSL**.
- If your plan has no free SSL, add the GoDaddy SSL certificate from **My Products**.

Then open `https://attendance.yourcompany.com`. The login page should appear.

### A8. First login

1. Sign in with **admin / admin**.
2. **Settings → Change your password** immediately.
3. **Settings → General**: set the company name.
4. **Settings → Bridge agent**: you need the **server_url** and **token** shown here in step C.

### A9. Nightly backup (recommended)

cPanel → **Advanced → Cron Jobs** → add:

- Common setting: *Once Per Day*.
- Command:
  ```
  bash /home/<cpanel-username>/Hikvision-Biometric/deploy/backup-cron.sh
  ```

The last 30 daily copies are kept in `~/attendance-data/backups/`.

Now continue with **Part C**.

---

## Part B: GoDaddy VPS (Ubuntu 22.04 / 24.04)

1. GoDaddy → **My Products → Servers → Manage**. Note the server's **IP address** and **root password**.
2. In GoDaddy DNS for your domain, add an **A record**: name `attendance`, value `<VPS IP>`.
3. Connect: `ssh root@<VPS IP>`.
4. Download the code. The repository is private, so first create a deploy key as in step A3:
   ```bash
   apt-get update && apt-get install -y git
   cd /opt && git clone -b main git@github.com:amit5042k/Hikvision-Biometric.git
   ```
5. Install everything with one command: the web server, HTTPS certificate, auto-start service and nightly backups.
   ```bash
   bash /opt/Hikvision-Biometric/deploy/vps-setup.sh attendance.yourcompany.com you@yourcompany.com
   ```
   - The data is stored in `/var/lib/attendance`, outside the code.
6. Open `https://attendance.yourcompany.com`. Then do step **A8** (first login) and continue with **Part C**.

---

## Part C: Connect the terminal through the bridge PC

### C1. Install the bridge (Windows)

1. Install **Python 3.11+** from python.org.
   - In the installer, tick **"Add python.exe to PATH"**.
   - Choose **Customize installation → Install for all users**.
2. Download the code onto the PC:
   - Either install *Git for Windows* and run `git clone https://github.com/amit5042k/Hikvision-Biometric.git C:\Hikvision-Biometric`.
   - Or, on GitHub, click **Code → Download ZIP** and extract it to `C:\Hikvision-Biometric`.
3. In `C:\Hikvision-Biometric\bridge\`, copy `bridge.ini.example` to `bridge.ini`. Open it in Notepad and paste the two values from **Settings → Bridge agent**:
   ```ini
   [bridge]
   server_url = https://attendance.yourcompany.com
   token = (the long token)
   poll_seconds = 20
   ```
4. Double-click **`bridge\run_bridge.bat`**.
   - The first run installs the packages, which takes about a minute.
   - When it shows `Bridge started, talking to https://...`, it works. Close the window.
5. Right-click **`bridge\install_windows_task.bat`** → **Run as administrator**. The bridge now starts by itself whenever the PC boots.
6. Windows **Settings → Power → Screen and sleep**: set **"When plugged in, put my device to sleep"** to **Never**.

*(Linux PC or Raspberry Pi: clone the code, run `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, create `bridge/bridge.ini`, then install `bridge/hik-bridge.service` as described inside that file.)*

### C2. Add the terminal in the web app

1. **Devices → Add device**:
   - **IP address**: the terminal's office LAN IP, e.g. `192.168.1.64`.
   - **Username / password**: the terminal's admin login.
   - **UTC offset**: `+05:30` for India.
   - **Connection**: **Through the bridge agent**.
   - Click **Save**.
2. Click **Test connection**. Wait about 20 seconds and reload the page.
   - Under **Recent bridge jobs**, the job should show **done** and the terminal's model and serial number.
3. Click **Import users from device**. Each user on the terminal becomes an employee.
4. Click **Download new punches**. From now on, new punches upload automatically every few minutes. The interval is in **Settings → Auto-sync interval**.
5. Set up **Shifts**, **Holidays** and **Departments**, and assign shifts on the **Employees** page.

---

## 3. Updating the app later (bug fixes and new features), without losing data

### The routine

1. Make and test the change on your computer, then push it to `main` on GitHub.
2. On the server, run **one command**:

   **cPanel:**
   ```bash
   bash ~/Hikvision-Biometric/deploy/update.sh
   ```
   **VPS:**
   ```bash
   sudo bash /opt/Hikvision-Biometric/deploy/update.sh --systemd
   ```

   The script does four things in order:
   1. Saves a backup of the database to `attendance-data/backups/`.
   2. Downloads the new code from git.
   3. Installs any new Python packages.
   4. Restarts the app.

3. Refresh the site. Logins, employees, punches and settings are all still there.

**The bridge PC** only needs updating when files in `bridge/` or `app/hikvision.py` / `app/device_actions.py` change:

- Run `git pull` in `C:\Hikvision-Biometric`, or download the ZIP again. **Keep your `bridge.ini`.**
- Restart the PC, or run `schtasks /End /TN HikvisionAttendanceBridge` followed by `schtasks /Run /TN HikvisionAttendanceBridge`.

### Why your data is safe

| Protection | What it means |
|---|---|
| Data lives outside the code | The database, login key and backups are in `~/attendance-data` (VPS: `/var/lib/attendance`). `git pull` only changes the code folder. |
| The database is never in git | `.gitignore` excludes `*.db` and the data folders, so nothing in git can overwrite it. |
| Logins are never re-created | The default `admin/admin` account is only created when the database has **no users at all**. Restarts and updates never reset passwords. |
| Database changes are migrations | New features that need new tables or columns add a numbered *migration* in `app/db.py`. On restart the app applies only the new ones, inside a transaction, and keeps all existing rows. |
| Automatic safety copies | `update.sh` backs up before every update. The app takes another backup before applying any migration. Nightly backups run from cron. |
| The session key is kept | `attendance-data/secret_key` persists, so users are not logged out by an update. |

### Rules for whoever changes the code

- **Never edit `app/schema.sql`.** It is the version-1 baseline. To change the database, **append** a new entry to `MIGRATIONS` in `app/db.py`, for example:
  ```python
  (3, "ALTER TABLE employees ADD COLUMN blood_group TEXT"),
  ```
- Never commit `.db` files or `bridge.ini`.
- Never delete or move the data folder. To move the app, copy the folder and point `ATTENDANCE_DATA_DIR` at the new location.
- Run `pytest` before pushing. `tests/test_persistence.py` checks that upgrades keep users and data.

---

## 4. Backup and restore

- **Download a backup any time:** **Settings → Backup → Download backup**. You get a single `.db` file with everything. Keep copies off the server too, for example on Google Drive.
- **Restore** (cPanel):
  1. In **Setup Python App**, click **Stop**.
  2. In the terminal:
     ```bash
     cp ~/attendance-data/attendance.db ~/attendance-data/attendance-before-restore.db
     cp ~/attendance-data/backups/attendance-YYYYMMDD-HHMMSS.db ~/attendance-data/attendance.db
     ```
  3. Click **Start**.
- **Restore** (VPS): run `systemctl stop attendance`, copy the file the same way into `/var/lib/attendance/`, run `chown attendance: /var/lib/attendance/attendance.db`, then `systemctl start attendance`.

---

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| "It works! Python …" page | cPanel replaced the startup file. Run `cd ~/Hikvision-Biometric && git checkout -- passenger_wsgi.py`, then **Restart**. |
| 503 / "Incomplete response" / blank page | Read `~/Hikvision-Biometric/stderr.log`. `No module named flask` means step A6 was skipped: activate the venv and run `pip install -r requirements.txt`. |
| Login page loops or "Invalid CSRF token" | Open the site with **https://**. If you have no SSL yet, remove the `HTTPS_ONLY` variable and restart. |
| Device shows "last check-in: never" | The bridge is not running. Run `bridge\run_bridge.bat` by hand and read the message. Check that the PC is not asleep and that `bridge\bridge.log` has recent entries. |
| Bridge says "Server rejected the bridge token" | Copy the token again from **Settings → Bridge agent** into `bridge.ini`, then restart the bridge. |
| Jobs fail with "Cannot reach device" | On the bridge PC, open `http://192.168.1.64` (your terminal's IP) in a browser. If it doesn't open, the IP is wrong or the PC is on another network. |
| Jobs fail with "Authentication failed" | Wrong terminal password. Fix it under **Devices → Edit**. |
| Punch times are off by hours | Set the device's **UTC offset** correctly and click **Sync device clock**. |
| No punches arrive although the terminal works | **Settings → Event minor codes**: set it to `0` (all events), then click **Download new punches**. |
| Forgot the admin password | In the server terminal, run the A6 `source …/activate && cd …` command, then:<br>`export ATTENDANCE_DATA_DIR=~/attendance-data && flask --app app reset-admin`<br>This sets the password to `admin`; change it straight away. |
| Repository was moved or renamed | Run `git remote set-url origin git@github.com:<owner>/<repo>.git` in the code folder. |
