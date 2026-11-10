// v1.20.8 - saving files from the website AND the Android app.
// The app's WebView ignores normal downloads (<a download>, Content-Disposition), which is why "Download report" did nothing on the
// phone. In the app (APK with the Filesystem + Share plugins) the file is written to the phone and the share menu opens (Drive,
// Files, WhatsApp, Claude...). In a browser it is a normal download. An older APK without the plugins falls back to the share
// sheet or, for text, the clipboard - and says so.
(function () {
  const native = () => !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
  const plugin = n => window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins[n];

  async function toB64(blob) {
    const buf = new Uint8Array(await blob.arrayBuffer());
    let s = "";
    for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(s);
  }

  function toast(msg, ms) {
    let el = document.getElementById("oiSaveToast");
    if (!el) {
      el = document.createElement("div"); el.id = "oiSaveToast";
      el.style.cssText = "position:fixed;left:12px;right:12px;bottom:84px;z-index:9999;background:#13233a;color:#e8eef7;border:1px solid #2b3d55;" +
        "border-radius:12px;padding:12px 14px;font-size:14px;line-height:1.4;box-shadow:0 8px 24px rgba(0,0,0,.4);max-width:560px;margin:0 auto";
      document.body.appendChild(el);
    }
    el.textContent = msg; el.hidden = false;
    clearTimeout(el._t); el._t = setTimeout(() => { el.hidden = true; }, ms || 7000);
  }

  async function save(name, data, mime) {
    const blob = data instanceof Blob ? data : new Blob([data], { type: mime || "application/octet-stream" });
    const FS = plugin("Filesystem"), SH = plugin("Share");
    if (native() && FS) {
      const b64 = await toB64(blob);
      let saved = null;
      try { await FS.writeFile({ path: "OptionIntelligence/" + name, data: b64, directory: "DOCUMENTS", recursive: true }); saved = "Documents/OptionIntelligence/" + name; } catch (e) {}
      try {
        const c = await FS.writeFile({ path: name, data: b64, directory: "CACHE" });
        if (SH) {
          try { await SH.share({ title: name, url: c.uri, dialogTitle: "Save or send " + name }); return saved ? `Saved to ${saved}` : "Choose Drive, Files or Claude in the share menu to keep it."; }
          catch (e) { if (/cancel/i.test((e && e.message) || "")) return saved ? `Saved to ${saved}` : "Share cancelled."; }
        }
      } catch (e) {}
      if (saved) return `Saved to ${saved}`;
    }
    try {
      const f = new File([blob], name, { type: blob.type });
      if (native() && navigator.canShare && navigator.canShare({ files: [f] })) { await navigator.share({ files: [f], title: name }); return "Shared."; }
    } catch (e) { if (e && e.name === "AbortError") return "Share cancelled."; }
    if (!native()) {
      const url = URL.createObjectURL(blob), a = document.createElement("a");
      a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      return "Downloaded " + name;
    }
    if (typeof data === "string") {
      let ok = false;
      try { await navigator.clipboard.writeText(data); ok = true; } catch (e) {}
      if (!ok) { const ta = document.createElement("textarea"); ta.value = data; document.body.appendChild(ta); ta.select(); try { ok = document.execCommand("copy"); } catch (e) {} ta.remove(); }
      if (ok) return `This app version can't save files, so the ${name} content was copied - paste it where you need it. Install the latest APK to save files.`;
    }
    return "This app version can't save files. Install the latest APK (GitHub → Releases → apk-latest) or open the site in Chrome.";
  }

  // fetch a server export (xlsx / csv) and save it the same way
  async function saveUrl(url, name) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw Error((await r.text()).slice(0, 200) || r.status);
    const cd = r.headers.get("content-disposition") || "", m = /filename="?([^";]+)"?/i.exec(cd);
    return save(name || (m && m[1]) || "export", await r.blob());
  }

  window.oiSave = async (name, data, mime) => { const msg = await save(name, data, mime); toast(msg); return msg; };
  window.oiSaveUrl = async (url, name) => {
    toast("Preparing " + (name || "file") + "…", 60000);
    try { const msg = await saveUrl(url, name); toast(msg); return msg; } catch (e) { toast("Export failed: " + e.message); throw e; }
  };
  window.oiToast = toast;
})();
