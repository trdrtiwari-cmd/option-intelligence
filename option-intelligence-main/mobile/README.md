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


## Native push notifications (v1.18.0)
1. Create a Firebase project, add an Android app with package ID `app.optionintelligence.terminal`, download `google-services.json`. For GitHub Actions builds, base64-encode that file and add it as repository secret `GOOGLE_SERVICES_JSON_BASE64`; for local builds place it at `mobile/android/app/google-services.json` after `npm run android:add`.
2. Ensure the Android Gradle project applies the Google Services plugin (Capacitor's push plugin setup instructions for your generated Android project). Then run `npm install` and `npm run android:sync`, rebuild the APK, install it, sign in, and tap **Enable push alerts**.
3. In Render set `FIREBASE_SERVICE_ACCOUNT_JSON` to the full Firebase service-account JSON (one-line JSON value). Keep it secret; never commit it.
4. For browser/PWA push, generate a VAPID key pair and set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT` in Render. Then sign in on Chrome, tap **Enable push alerts**, and allow notifications.
5. Sign in to the deployed website, then open `https://YOUR-SITE/api/auth/me` in the same browser session. Copy `user.id` (a number) into Render as `PUSH_OWNER_USER_ID`. This routes server-side background notifications to the registered devices for that account and scopes its notification history.
6. **Registrations/history persistence:** device registrations and notification history are stored in the accounts database, so as long as `DATABASE_URL` points to PostgreSQL they survive Render restarts and redeploys. No persistent disk or `PUSH_DB` is needed.
7. In Render set `ALERT_BACKGROUND=1`, `ALERT_SYMBOLS=NIFTY,BANKNIFTY`, `ALERT_INTERVAL=5`, `ALERT_MODE=scalp`, `ALERT_SCORE_MIN=70`, and `ALERT_EVENTS=CONFIRMED`. Use an always-on service; Render Free sleeps and cannot reliably poll markets while asleep.
8. After deploy, sign in on the phone, tap **Enable push alerts**, then use the app's alert test (`/api/alerts/test`) and confirm the device receives it. Check `/api/push/config` for `delivery_configured`, `background_poller`, and `owner_user_id_configured`.

### Recommended deployment order
1. Deploy the updated repository to an always-on host.
2. Configure Upstox credentials/token and verify `/health` plus live data first.
3. Add Firebase Android app package `app.optionintelligence.terminal`; configure GitHub secret `GOOGLE_SERVICES_JSON_BASE64` for APK builds and Render secret `FIREBASE_SERVICE_ACCOUNT_JSON` for sending.
4. For browser PWA alerts, separately configure VAPID public/private keys and `VAPID_SUBJECT`.
5. Optionally set `PUSH_OWNER_USER_ID` (empty = notify every registered account) and the `ALERT_*` variables.
6. Build/reinstall the Android APK if using native push, sign in, enable push, and test. Web Push users enable it from Chrome on the deployed HTTPS site.
7. Keep the app's final decision fail-safe: stale/missing market data must remain WAIT. Notifications are alerts, not guaranteed delivery or auto-trading.

Push requires HTTPS, valid Firebase/VAPID credentials, OS notification permission, a matching `PUSH_OWNER_USER_ID`, and a backend that stays awake. Android battery optimization may still delay delivery. Push delivery is best-effort; it is not an order-execution or guaranteed trading alert service.
