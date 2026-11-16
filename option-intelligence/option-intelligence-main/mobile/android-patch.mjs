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

import android.app.DownloadManager;
import android.content.Context;
import android.net.Uri;
import android.os.Bundle;
import android.os.Environment;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.webkit.WebView;
import android.widget.Toast;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (getBridge() == null) return;
        WebView web = getBridge().getWebView();
        if (web == null) return;
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
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
        });
    }
}
`);
console.log("MainActivity patched: downloads go to Downloads/OptionIntelligence");
