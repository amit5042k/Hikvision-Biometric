# QR Generator — website, Android app and server

People sign up with email + password and get a **7-day free trial**. Every QR code they make
is a short link on your server (`https://qr.yourdomain.com/r/Ab3xY9k`), so:

- codes **stop scanning when the trial ends**. Scanners see "This QR code has expired" plus your contact text;
- you **extend an account from the admin page** and all of its printed codes work again immediately;
- users can **edit a code's content later without reprinting it**, and see how many times each code was scanned.

Code types: link, text, Wi-Fi, contact (vCard), email, SMS, phone. Links redirect straight to the
website; the others open a small page (copy text, save contact, Wi-Fi name + password, call/SMS/email button).
Design options: colours, square/rounded/dot style, error correction, size, quiet zone, centre logo,
PNG/SVG download, copy, share.

```
qr-generator/
├── server/     Flask + SQLite: accounts, trial, short links, admin page (also serves web/)
├── web/        the website (plain HTML/CSS/JS)
├── android/    Android app: opens your server's website, adds gallery saving and the share sheet
└── Dockerfile  runs server + website
```

## 1. Run the server

You need a host with a domain and HTTPS. Any of these work: a VPS with Docker, Render, Railway or Fly.io.
SQLite needs a **persistent disk/volume** for the data folder.

### With Docker

```bash
docker build -t qr-generator qr-generator
docker run -d --name qr -p 8000:8000 -v qrdata:/data \
  -e ADMIN_EMAIL=you@example.com -e ADMIN_PASSWORD='choose-a-strong-one' \
  -e CONTACT_TEXT='WhatsApp +91 98765 43210 to extend your account.' \
  -e HTTPS_ONLY=1 \
  qr-generator
```

Put it behind HTTPS (Caddy, nginx, or your platform's built-in TLS) at e.g. `https://qr.yourdomain.com`.

### Without Docker

```bash
cd qr-generator/server
pip install -r requirements.txt
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='...' python run.py     # http://0.0.0.0:8000
```

### Settings (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | — | Creates the first admin account on start-up (only if it doesn't exist yet). |
| `CONTACT_TEXT` | "Contact the administrator…" | Shown to expired users and on expired-code pages. Put your WhatsApp/phone here. |
| `TRIAL_DAYS` | `7` | Length of the free trial for new sign-ups. |
| `QR_DATA_DIR` | `server/data` (`/data` in Docker) | Database (`qr.db`) and secret key. **Back this folder up.** |
| `PUBLIC_URL` | address of the request | Force the base of short links, e.g. `https://qr.yourdomain.com`. |
| `BEHIND_PROXY` | off (`1` in Docker) | Trust `X-Forwarded-*` from your HTTPS proxy so short links use `https://`. |
| `HTTPS_ONLY` | off | Secure-only admin session cookie. Turn on once you're on HTTPS. |
| `PORT`, `HOST` | `8000`, `0.0.0.0` | Where to listen. |

Pick your domain before users print codes: the short links contain it, so changing it later breaks printed codes.

## 2. Admin page

Open `https://qr.yourdomain.com/admin` and log in with the admin account. For each account you see
status, days left, number of codes and scans, and buttons to:

- **+ days / +7 / +1 yr**: extend access (counted from today if expired, otherwise added to the remaining time);
- **End now**: stop access and the account's codes immediately;
- **Disable / Enable**: block the account and log it out everywhere.

Another admin: `cd server && flask --app qrserver create-admin other@example.com`.

## 3. Website

The server serves it at `https://qr.yourdomain.com/`. Nothing else to deploy.

## 4. Android app

The app opens your server's website, so it must know the address.

1. In GitHub → **Settings → Secrets and variables → Actions → Variables**, add
   `QR_SERVER_URL` = `https://qr.yourdomain.com`.
2. Push any change under `qr-generator/` (or run the **QR Generator** workflow manually from the Actions tab).
3. Download the `qr-generator-apk` artifact from the run. `app-debug.apk` installs on any phone
   (allow "Install unknown apps").

Build locally instead: `cd qr-generator/android && ./gradlew assembleDebug -PqrServerUrl=https://qr.yourdomain.com`
(JDK 17 + Android SDK, or just open the folder in Android Studio). For the emulator against a local server:
`-PqrServerUrl=http://10.0.2.2:8000`.

The app supports Android 8.0+ and saves PNGs to `Pictures/QR Generator` and SVGs to `Download/QR Generator`.
Sharing a link or text from another app to **QR Generator** pre-fills a new code. Short links open in the
browser, the same way they do for anyone who scans them.

### Signed release for the Play Store

1. Create an upload key once and keep it safe:
   `keytool -genkeypair -v -keystore release.jks -alias qr -keyalg RSA -keysize 2048 -validity 10000`
2. Add the Actions secrets `QR_KEYSTORE_BASE64` (`base64 -w0 release.jks`), `QR_KEYSTORE_PASSWORD`,
   `QR_KEY_ALIAS` (`qr`), `QR_KEY_PASSWORD`.
3. The next workflow run also produces a signed `app-release.apk` and `app-release.aab`. Upload the `.aab` to Play Console.

Before the first Play release, decide on `applicationId` in `android/app/build.gradle`
(currently `org.mgnurbanestate.qrgenerator`); it can't change afterwards. Increase `versionCode` for every update.

## Development

```bash
cd qr-generator/server
pip install -r requirements-dev.txt
pytest -q            # API, trial expiry, scanning and admin tests
python run.py        # then open http://localhost:8000
```

## Credits

QR encoding: [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) by Kazuhiko Arase (MIT),
vendored at `web/vendor/qrcode.js`. "QR Code" is a registered trademark of DENSO WAVE INCORPORATED.
