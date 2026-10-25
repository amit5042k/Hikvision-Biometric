# QR Generator — website + Android app

A free, private QR code generator. Everything runs on the device; nothing is uploaded.

- **Content types:** link, plain text, Wi-Fi login, contact card (vCard), email, SMS, phone number
- **Design:** foreground/background colours, square / rounded / dot styles, error-correction level,
  size (128–2048 px), quiet zone, optional centre logo (error correction is raised to High automatically)
- **Export:** PNG, SVG (vector, for print), copy to clipboard, share
- **Extras:** recent codes history, dark mode, works offline (installable PWA)

```
qr-generator/
├── web/        the website (plain HTML/CSS/JS, no build step)
└── android/    Android app — a native shell that bundles web/ and adds
                gallery saving, the share sheet and "Share → QR Generator"
```

There is one copy of the UI: the Android build packages `web/` directly as app assets,
so any change to the website ships in the next app build too.

## Website

It is a static site — upload the contents of `qr-generator/web/` to any web host
(GoDaddy cPanel File Manager, Netlify drop, GitHub Pages, …). No server code or database.

- Serve it over **HTTPS** so the offline mode / "Install app" prompt works.
- When you change a file, bump `CACHE` in `web/sw.js` (e.g. `qrgen-v2`) so returning visitors
  get the new version.

Try it locally:

```bash
cd qr-generator/web
python3 -m http.server 8000   # then open http://localhost:8000
```

## Android app

**Get an APK without installing anything:** every push that touches `qr-generator/` runs the
*QR Generator Android* GitHub Action. Open the run under the repo's **Actions** tab and download
the `qr-generator-apk` artifact. `app-debug.apk` inside can be installed on any phone
(allow "Install unknown apps" when prompted).

**Build locally:** open `qr-generator/android` in Android Studio and press Run, or:

```bash
cd qr-generator/android
./gradlew assembleDebug          # → app/build/outputs/apk/debug/app-debug.apk
```

Requires JDK 17 and the Android SDK (Android Studio installs both).

App details: Android 8.0+ (minSdk 26), targets Android 15 (API 35). Saved PNGs go to
`Pictures/QR Generator`, SVGs to `Download/QR Generator`. Sharing a link or text from another
app to **QR Generator** opens it pre-filled.

### Publishing a signed release (Play Store)

1. Create an upload key once and keep it safe — losing it means you can't update the app:
   ```bash
   keytool -genkeypair -v -keystore release.jks -alias qr -keyalg RSA -keysize 2048 -validity 10000
   ```
2. In GitHub → Settings → Secrets and variables → Actions, add:
   `QR_KEYSTORE_BASE64` (output of `base64 -w0 release.jks`), `QR_KEYSTORE_PASSWORD`,
   `QR_KEY_ALIAS` (`qr`), `QR_KEY_PASSWORD`.
3. The next workflow run also produces a signed `app-release.apk` and `app-release.aab`.
   Upload the `.aab` to Google Play Console.

Before publishing, consider changing `applicationId` in `android/app/build.gradle`
(currently `org.mgnurbanestate.qrgenerator`) — it can't be changed after the first Play release —
and increase `versionCode` for every update.

## Credits

QR encoding: [qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) by Kazuhiko Arase (MIT),
vendored at `web/vendor/qrcode.js`. "QR Code" is a registered trademark of DENSO WAVE INCORPORATED.
