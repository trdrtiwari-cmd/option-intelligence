// v1.20.83: lets the Android app SAVE files. Run by the APK workflow after "npx cap add android".
// Capacitor's WebView ignores downloads, so MainActivity gets a DownloadListener that hands every download to Android's own
// DownloadManager: the file goes to Downloads/OptionIntelligence/ and a "download complete" notification appears.
// The page knows the app can do this from "OIApp-DL/1" in the user agent (capacitor.config.json -> android.appendUserAgent).
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const cfg = JSON.parse(readFileSync(new URL("./capacitor.config.json", import.meta.url), "utf8"));
const pkg = cfg.appId;
const dir = new URL(`./android/app/src/main/java/${pkg.replace(/\./g, "/")}/`, import.meta.url);
const file = new URL("MainActivity.java", dir);
if (!existsSync(file)) { console.error("MainActivity.java not found - run 'npx cap add android' first"); process.exit(1); }

writeFileSync(file, `package ${pkg};

import android.Manifest;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.WebView;
import android.widget.Toast;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    private static final int REQ_STORAGE = 4711;
    private String pendingUrl, pendingUa, pendingCd, pendingMime;

    // v1.20.87: Android 9 and older need the storage permission to write to Downloads - the app asks for it once at start and
    // again on the first download if it was refused. Android 10+ lets the system download manager save to Downloads with no
    // permission, so there is nothing to ask there.
    private boolean needsStorage() {
        return Build.VERSION.SDK_INT <= Build.VERSION_CODES.P
            && ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED;
    }

    private final BroadcastReceiver done = new BroadcastReceiver() {      // tells where the file went, or why it failed
        @Override public void onReceive(Context c, Intent i) {
            long id = i.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1);
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            try (Cursor cur = dm.query(new DownloadManager.Query().setFilterById(id))) {
                if (cur == null || !cur.moveToFirst()) return;
                int st = cur.getInt(cur.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                String title = cur.getString(cur.getColumnIndexOrThrow(DownloadManager.COLUMN_TITLE));
                if (st == DownloadManager.STATUS_SUCCESSFUL)
                    Toast.makeText(c, "Saved: Downloads/OptionIntelligence/" + title, Toast.LENGTH_LONG).show();
                else
                    Toast.makeText(c, "Download failed (reason " + cur.getInt(cur.getColumnIndexOrThrow(DownloadManager.COLUMN_REASON))
                        + ") - use Copy download link and open it in Chrome", Toast.LENGTH_LONG).show();
            } catch (Exception ignored) { }
        }
    };

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (needsStorage())
            ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
        try {
            ContextCompat.registerReceiver(this, done, new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE), ContextCompat.RECEIVER_EXPORTED);
        } catch (Exception ignored) { }
        if (getBridge() == null) return;
        WebView web = getBridge().getWebView();
        if (web == null) return;
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (needsStorage()) {                       // ask, then save this file once allowed
                pendingUrl = url; pendingUa = userAgent; pendingCd = contentDisposition; pendingMime = mimeType;
                ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQ_STORAGE);
                return;
            }
            save(url, userAgent, contentDisposition, mimeType);
        });
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        super.onRequestPermissionsResult(code, perms, res);
        if (code != REQ_STORAGE) return;
        boolean ok = res.length > 0 && res[0] == PackageManager.PERMISSION_GRANTED;
        if (pendingUrl != null) {
            if (ok) save(pendingUrl, pendingUa, pendingCd, pendingMime);
            else Toast.makeText(this, "Storage permission refused - the file was not saved. Allow it in Settings > Apps > Option Intelligence > Permissions.", Toast.LENGTH_LONG).show();
            pendingUrl = null;
        }
    }

    @Override
    public void onDestroy() {
        try { unregisterReceiver(done); } catch (Exception ignored) { }
        super.onDestroy();
    }

    private void save(String url, String userAgent, String contentDisposition, String mimeType) {
        try {
            String name = URLUtil.guessFileName(url, contentDisposition, mimeType);
            DownloadManager.Request req = new DownloadManager.Request(Uri.parse(url));
            req.setMimeType(mimeType);
            String cookies = CookieManager.getInstance().getCookie(url);
            if (cookies != null) req.addRequestHeader("Cookie", cookies);
            req.addRequestHeader("User-Agent", userAgent);
            req.setTitle(name);
            req.setDescription("Option Intelligence");
            req.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            req.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "OptionIntelligence/" + name);
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            dm.enqueue(req);
            Toast.makeText(this, "Saving " + name + " to Downloads/OptionIntelligence", Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            Toast.makeText(this, "Could not save the file: " + e.getMessage(), Toast.LENGTH_LONG).show();
        }
    }
}
`);

// v1.20.87: the storage permission for Android 9 and older (newer Android ignores it - maxSdkVersion 28)
const mf = new URL("./android/app/src/main/AndroidManifest.xml", import.meta.url);
if (existsSync(mf)) {
  let xml = readFileSync(mf, "utf8");
  if (!xml.includes("android.permission.WRITE_EXTERNAL_STORAGE")) {
    xml = xml.replace(/<application/, '<uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" android:maxSdkVersion="28" />\n    <application');
    writeFileSync(mf, xml);
    console.log("AndroidManifest: storage permission added (Android 9 and older)");
  }
}
console.log("MainActivity patched: downloads go to Downloads/OptionIntelligence");
