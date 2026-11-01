# Option Intelligence - Android / iOS app

The mobile app is the same Option Intelligence site, wrapped so it installs like a normal app (icon, full screen, no browser bar).
Because it loads your hosted site, **every update you deploy to Render shows up in the app immediately** - there is nothing to re-publish.
Your login, paper trades and all settings are stored in your account, so they follow you between the app, the browser and other phones.

## Easiest: install straight from the website (no build, Android + iPhone)
1. Open your site in Chrome (Android) or Safari (iPhone) and sign in.
2. Android: menu -> **Install app** (or More -> Install app inside the site). iPhone: Share -> **Add to Home Screen**.

## Android APK without Android Studio (GitHub builds it for you)
1. Put this whole project in your GitHub repository (the same one Render deploys).
2. GitHub -> **Actions** -> **Build Android APK** -> **Run workflow** -> type your site address, e.g. `https://option-intelligence.onrender.com`.
3. After a few minutes download the **option-intelligence-apk** artifact, unzip it and send `app-debug.apk` to your phone.
4. Open it on the phone and allow "install from this source" once. This is a debug-signed APK for your own use; it is not for the Play Store.

## Build it yourself (Node 20 + JDK 17 + Android Studio)
```bash
cd mobile
npm install
export APP_URL=https://your-service.onrender.com    # Windows PowerShell: $env:APP_URL="https://..."
npm run android:add
npm run assets            # icon + splash from mobile/assets
npm run android:sync
npm run android:open      # Android Studio -> Run, or Build -> Build APK(s)
```
iPhone (needs a Mac with Xcode and an Apple developer account for anything beyond 7-day test installs): `npm run ios:add && npm run assets && npm run ios:sync && npm run ios:open`.

## Notes
- Upstox login (owner only) opens inside the app; the allowed hosts are listed in `capacitor.config.json` -> `allowNavigation`.
- The app needs internet. Offline it shows an offline screen; your settings and paper trades are kept on the device and sync when it reconnects.
- Alerts on the phone: the app itself beeps while open. For alerts while the app is closed use the Telegram / ntfy / email channels already built in (see `.env.example`).
