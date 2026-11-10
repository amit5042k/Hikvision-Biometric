# Mobile app

The attendance system is an **installable web app (PWA)**. Once your site runs on HTTPS (see `DEPLOY_GODADDY.md`), anyone can install it on a phone. It gets its own home-screen icon and opens full-screen like a regular app. Every user role works in it: admin, viewer, and employee self-service.

There is nothing to build or update separately. When you update the website, the app updates too.

## Install on a phone

| Phone | Steps |
|---|---|
| **Android** (Chrome) | Open `https://attendance.yourcompany.com` and sign in. Tap **📱 Install app** in the menu, or ⋮ → **Install app** / **Add to Home screen**. |
| **iPhone / iPad** (Safari) | Open the site in **Safari** and sign in. Tap **Share** (□↑) → **Add to Home Screen** → **Add**. |
| **Windows / Mac** (Chrome, Edge) | Click the install icon (⊕) at the right end of the address bar. |

What the app does:

- **Online only.** It needs an internet connection. Offline, it shows a friendly "You are offline" page.
- **No data on the phone.** Attendance pages are never stored on the device. Only the app's look (styles, icons) is cached, so a lost phone does not leak records.
- **Your own sign-in.** Each person signs in with their own login, and two-factor sign-in works in the app too.

## Optional: publish on Google Play

If you want staff to find the app in the Play Store, wrap the PWA as a *Trusted Web Activity*. That is a small Android app that shows your website. You need a Google Play developer account, which has a one-time fee.

1. Go to **https://www.pwabuilder.com**, enter your site address and click **Start**.
2. Choose **Package for stores → Android**. Keep the suggested package name (e.g. `com.yourcompany.attendance`) and download the package.
3. The download contains an `assetlinks.json` file. Copy its contents into **Settings → Mobile app → assetlinks.json** and save. The site then serves it at `/.well-known/assetlinks.json`, which proves to Android that the app and the website belong together. Without it, the app shows a browser bar at the top.
4. In the **Google Play Console**, create an app and upload the `.aab` file from the package. Fill in the store listing and send it for review.
5. **Keep the signing key from the package safe.** Every later version must be signed with the same key.

## Optional: Apple App Store

PWABuilder can also produce an iOS package. Building it needs a Mac with Xcode and an Apple Developer account (yearly fee). For most companies, **Add to Home Screen** in Safari is enough and costs nothing.
