/* Option Intelligence - mobile app shell (v1.17).
   - registers the service worker (installable app, offline screen)
   - bottom tab bar + "More" sheet on phones
   - Install app button (Android/desktop prompt, or the Add to Home Screen hint on iPhone)
   - Keep screen awake (setting saved to your account)
   - offline banner, collapsible controls on the main terminal
   Settings used here live in OIPrefs (prefs.js), so they sync to the account automatically. */
(function () {
  "use strict";
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const isAuth = path === "/login";
  const prefs = () => window.OIPrefs || { get: (k, d) => d, set() { }, onChange() { } };

  // v1.20.67: the tab-visibility switch lives in issues.js (the first script on every page) - see there.
  const setTabActive = on => { try { window.__oiSetTabActive && window.__oiSetTabActive(on); } catch { } };

  // ------------------------------------------------------------- service worker
  if ("serviceWorker" in navigator && window.isSecureContext && window.self === window.top) {
    window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => { }));
  }

  // ------------------------------------------------------------- install
  let deferred = null;
  const standalone = () => window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); deferred = e; refreshInstallRow(); });
  window.addEventListener("appinstalled", () => { deferred = null; refreshInstallRow(); });

  // ------------------------------------------------------------- keep screen awake
  let lock = null;
  async function applyWake() {
    const want = prefs().get("app.keepAwake", false) === true && document.visibilityState === "visible";
    try {
      if (want && !lock && "wakeLock" in navigator) {
        lock = await navigator.wakeLock.request("screen");
        lock.addEventListener("release", () => { lock = null; });
      } else if (!want && lock) { await lock.release(); lock = null; }
    } catch { lock = null; }
  }
  document.addEventListener("visibilitychange", applyWake);

  // ------------------------------------------------------------- offline banner
  function mountOffline() {
    const b = document.createElement("div"); b.className = "m-offline"; b.hidden = true; b.setAttribute("role", "status");
    b.textContent = "You're offline - showing the last data. Changes are saved on this device and sync when you're back.";
    document.body.appendChild(b);
    const upd = () => { b.hidden = navigator.onLine !== false; };
    addEventListener("online", upd); addEventListener("offline", upd); upd();
  }

  // ------------------------------------------------------------- bottom nav
  const ICONS = {
    terminal: '<path d="M4 19V5M4 19h16"/><path d="M8 15l3-4 3 2 4-6"/>',
    data: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    signals: '<path d="M5 6h14M5 12h14M5 18h9"/>',
    chart: '<path d="M7 4v16M17 4v16"/><rect x="4.5" y="8" width="5" height="7" rx="1"/><rect x="14.5" y="6" width="5" height="9" rx="1"/>',
    diag: '<path d="M4 14l4-6 4 8 3-5 5 3"/>',
    more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
  };
  const TABS = [["/", "Terminal", "terminal"], ["/chart", "Chart", "chart"], ["/dashboard", "Data", "data"], ["/signals", "Signals", "signals"], ["/diagnostics", "Tests", "diag"]];
  const svg = k => '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS[k] + "</svg>";

  let sheet = null, installRow = null;
  function refreshInstallRow() {
    if (!installRow) return;
    if (standalone()) { installRow.hidden = true; return; }
    installRow.hidden = false;
    const sm = installRow.querySelector("small");
    sm.textContent = deferred ? "Add Option Intelligence to your home screen" : isIOS ? "In Safari tap Share, then Add to Home Screen" : "Use your browser menu: Install app / Add to Home screen";
  }

  function mountNav() {
    const nav = document.createElement("nav"); nav.className = "m-nav"; nav.setAttribute("aria-label", "Main");
    TABS.forEach(([href, label, icon]) => {
      const a = document.createElement("a"); a.href = href; a.innerHTML = svg(icon) + "<span>" + label + "</span>";
      if (path === href) { a.className = "on"; a.setAttribute("aria-current", "page"); }
      nav.appendChild(a);
    });
    const more = document.createElement("button"); more.type = "button"; more.innerHTML = svg("more") + "<span>More</span>";
    if (path === "/account") more.className = "on";
    nav.appendChild(more);

    const bg = document.createElement("div"); bg.className = "m-sheet-bg";
    sheet = document.createElement("div"); sheet.className = "m-sheet"; sheet.setAttribute("role", "dialog"); sheet.setAttribute("aria-label", "More");
    sheet.innerHTML = '<h3>More</h3>' +
      '<a class="m-row" href="/account"><span>Account &amp; saved settings<small>Password, saved data, reset preferences, app update</small></span></a>' +
      '<a class="m-row" id="mUpstox" href="/api/login" hidden><span>Connect Upstox<small>Owner only - refresh the daily token</small></span></a>' +
      '<label class="m-row"><span>Notifications<small id="mNotifyInfo">Which signals send a push</small></span><select class="m-select" id="mNotify"><option value="all">All signal changes</option><option value="confirmed">Confirmed buy only</option></select></label>' +
      '<label class="m-row"><span>Keep screen awake<small>Stops the phone sleeping while the app is open</small></span><input type="checkbox" class="m-switch" id="mAwake"></label>' +
      '<button type="button" class="m-row" id="mInstall"><span>Install app<small></small></span></button>' +
      '<button type="button" class="m-row" id="mSync"><span>Sync now<small id="mSyncInfo">Checking…</small></span></button>' +
      '<button type="button" class="m-row" id="mRefresh"><span>Reload app<small>Fetch the latest version</small></span></button>';
    const close = () => document.documentElement.classList.remove("m-open");
    more.onclick = () => {
      document.documentElement.classList.toggle("m-open");
      const st = window.OISync && window.OISync.status && window.OISync.status();
      if (st && st.user && st.user.is_admin) sheet.querySelector("#mUpstox").hidden = false;
      showSync();
    };
    const showSync = () => {
      const st = window.OISync && window.OISync.status && window.OISync.status(), info = sheet.querySelector("#mSyncInfo");
      if (!st || !st.user) { info.textContent = "Not signed in / sync not running"; return; }
      const t = st.lastOk ? new Date(st.lastOk).toLocaleTimeString() : "not yet";
      info.textContent = "Signed in as " + st.user.username + " · last checked " + t + (st.dirty.length ? " · waiting to upload: " + st.dirty.length : "") + (st.lastErr ? " · problem: " + st.lastErr : "");
    };
    sheet.querySelector("#mSync").onclick = async () => {
      const info = sheet.querySelector("#mSyncInfo"); info.textContent = "Syncing…";
      try { await window.OISync.sync(); } catch { }
      showSync(); setTimeout(() => location.reload(), 600);
    };
    bg.onclick = close;
    sheet.addEventListener("click", e => { if (e.target.closest("a")) close(); });
    document.body.append(nav, bg, sheet);

    installRow = sheet.querySelector("#mInstall");
    installRow.onclick = async () => {
      if (deferred) { deferred.prompt(); try { await deferred.userChoice; } catch { } deferred = null; refreshInstallRow(); close(); }
    };
    sheet.querySelector("#mRefresh").onclick = async () => {
      try { const keys = await caches.keys(); await Promise.all(keys.map(k => caches.delete(k))); } catch { }
      try { const regs = await navigator.serviceWorker.getRegistrations(); await Promise.all(regs.map(r => r.update())); } catch { }
      location.reload();
    };
    const nsel = sheet.querySelector("#mNotify"), ninfo = sheet.querySelector("#mNotifyInfo");
    const nload = () => fetch("/api/push/prefs", {credentials: "same-origin"}).then(r => r.ok ? r.json() : null).then(d => { if (d && d.mode) nsel.value = d.mode; }).catch(() => { });
    nload();
    window.addEventListener("oi-notify-pref", e => { nsel.value = e.detail; });
    nsel.onchange = async () => {
      const want = nsel.value;
      try {
        const r = await fetch("/api/push/prefs", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: JSON.stringify({mode: want})});
        if (!r.ok) throw new Error("HTTP " + r.status);
        ninfo.textContent = want === "confirmed" ? "Only CONFIRMED buy signals send a push" : "Every signal change sends a push";
        window.dispatchEvent(new CustomEvent("oi-notify-pref", {detail: want}));
      } catch (e) { ninfo.textContent = "Not saved: " + e.message; nload(); }
    };
    const awake = sheet.querySelector("#mAwake");
    const sync = () => { awake.checked = prefs().get("app.keepAwake", false) === true; applyWake(); };
    awake.onchange = () => { prefs().set("app.keepAwake", awake.checked); applyWake(); };
    prefs().onChange(name => { if (name === "app.keepAwake" || name === "*") sync(); });
    sync(); refreshInstallRow();
  }

  // ------------------------------------------------------------- collapsible controls (main terminal only)
  function mountToggle() {
    if (path !== "/") return;
    const bar = document.querySelector("section.toolbar");
    if (!bar) return;
    const btn = document.createElement("button"); btn.type = "button"; btn.className = "secondary m-toggle";
    const show = open => {                       // display only - nothing is saved here
      bar.classList.toggle("m-collapsed", !open);
      btn.innerHTML = "<span>Signal &amp; alert controls</span><span>" + (open ? "&#9652;" : "&#9662;") + "</span>";
      btn.setAttribute("aria-expanded", String(open));
    };
    btn.onclick = () => { const open = bar.classList.contains("m-collapsed"); show(open); prefs().set("app.controlsOpen", open); };
    bar.parentNode.insertBefore(btn, bar);
    show(prefs().get("app.controlsOpen", false) === true);
    prefs().onChange(name => { if (name === "app.controlsOpen" || name === "*") show(prefs().get("app.controlsOpen", false) === true); });
  }

  // ------------------------------------------------------------- v1.20.39: tabs stay alive
  // Opening another tab (bottom bar, or any link to Terminal / Data / Signals / Tests) no longer reloads the app: each tab is kept
  // running in its own frame, so the terminal's live feed, alerts and AI view keep working while Signals or Tests are open.
  const framed = window.self !== window.top;
  const TAB_PATHS = TABS.map(t => t[0]);
  const frames = {};
  const hostPath = path;
  function tabOf(a) {
    try {
      const u = new URL(a.getAttribute("href"), location.href);
      if (u.origin !== location.origin) return null;
      const p = u.pathname.replace(/\/+$/, "") || "/";
      return TAB_PATHS.includes(p) ? p : null;
    } catch { return null; }
  }
  function markNav(p) {
    document.querySelectorAll(".m-nav a").forEach(a => {
      const on = tabOf(a) === p;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  // v1.20.68: during market hours the Terminal keeps running behind other tabs (live feed, alerts, AI view). The other tabs and
  // the Terminal outside market hours pause while not on screen.
  let curTab = hostPath;
  const keepLive = k => k === "/" && !!(window.__oiMarketOpen && window.__oiMarketOpen());
  function applyActive() {
    Object.entries(frames).forEach(([k, f]) => { try { f.contentWindow.postMessage({ oiActive: k === curTab || keepLive(k) }, location.origin); } catch { } });
    setTabActive(curTab === hostPath || keepLive(hostPath));
  }
  if (!framed) setInterval(applyActive, 60000);          // market opens / closes while another tab is on screen
  function showTab(p, push) {
    curTab = p;
    Object.entries(frames).forEach(([k, f]) => { f.style.display = k === p ? "block" : "none"; });
    applyActive();
    if (p !== hostPath && !frames[p]) {
      const f = document.createElement("iframe");
      f.className = "oi-tab-frame"; f.title = p; f.src = p;
      f.style.cssText = "position:fixed;inset:0;width:100%;height:100%;border:0;z-index:800;background:var(--bg,#0b1420)";
      document.body.appendChild(f); frames[p] = f;
    }
    document.documentElement.classList.toggle("m-framed", p !== hostPath);
    document.body.style.overflow = p !== hostPath ? "hidden" : "";
    markNav(p);
    if (push) { try { history.pushState({ oiTab: p }, "", p); } catch { } }
  }
  function mountKeepAlive() {
    if (isAuth) return;
    if (framed) {                                   // inside a tab frame: ask the app shell to switch instead of navigating
      document.addEventListener("click", e => {
        const a = e.target.closest && e.target.closest("a[href]");
        const p = a && !a.target && !e.ctrlKey && !e.metaKey && !e.shiftKey && tabOf(a);
        if (p) { e.preventDefault(); try { window.parent.postMessage({ oiTab: p }, location.origin); } catch { } }
      }, true);
      return;
    }
    if (!TAB_PATHS.includes(hostPath)) return;
    document.addEventListener("click", e => {
      const a = e.target.closest && e.target.closest("a[href]");
      const p = a && !a.target && !e.ctrlKey && !e.metaKey && !e.shiftKey && tabOf(a);
      if (p) { e.preventDefault(); showTab(p, true); }
    }, true);
    window.addEventListener("message", e => { if (e.origin === location.origin && e.data && e.data.oiTab) showTab(e.data.oiTab, true); });
    // v1.20.45: all tabs live together - each tab is loaded in the background a few seconds after the app opens (one at a time),
    // then keeps running hidden, so switching tabs shows data that is already loaded and updating.
    const preload = TAB_PATHS.filter(p => p !== hostPath);
    const next = () => {
      const p = preload.shift(); if (!p) return;
      if (!frames[p]) {
        const f = document.createElement("iframe");
        f.className = "oi-tab-frame"; f.title = p; f.src = p;
        f.style.cssText = "position:fixed;inset:0;width:100%;height:100%;border:0;z-index:800;background:var(--bg,#0b1420);display:none";
        document.body.appendChild(f); frames[p] = f;
        f.addEventListener("load", () => { try { f.contentWindow.postMessage({ oiActive: f.style.display !== "none" || keepLive(p) }, location.origin); } catch { } setTimeout(next, 1500); }, { once: true });
      } else next();
    };
    setTimeout(next, 4000);
    window.addEventListener("popstate", e => showTab((e.state && e.state.oiTab) || (location.pathname.replace(/\/+$/, "") || "/"), false));
    try { history.replaceState({ oiTab: hostPath }, "", location.href); } catch { }
  }

  function init() {
    if (!isAuth && !framed) { mountNav(); mountToggle(); }
    if (!isAuth && framed) mountToggle();
    mountKeepAlive();
    mountOffline();
    applyWake();
  }
  // Start once the saved settings are available (sync.js -> prefs.js loads them first). If they never arrive (offline
  // login page etc.) start anyway after a short wait.
  let started = false;
  const start = () => { if (started) return; started = true; init(); };
  const ready = () => document.readyState !== "loading";
  function boot() {
    if (!ready()) return document.addEventListener("DOMContentLoaded", boot, { once: true });
    if (window.OIPrefs || isAuth) return start();
    window.addEventListener("oi-prefs-ready", start, { once: true });
    setTimeout(start, 5000);
  }
  boot();

  // ------------------------------------------------------------- v1.19: tables become cards on phones
  // Every <td> of a table.cardify gets data-label = its column title, so mobile.css can print "Label  value" inside a card.
  // Rows are re-rendered by the page scripts all the time, so a MutationObserver relabels them.
  function labelTable(t) {
    const heads = [...t.querySelectorAll("thead th")].map(th => th.textContent.trim());
    if (!heads.length) return;
    t.querySelectorAll("tbody tr").forEach(tr => {
      let col = 0;
      [...tr.children].forEach(td => {
        if (td.dataset.label !== heads[col]) td.dataset.label = heads[col] || "";
        if (!td.textContent.trim() || td.textContent.trim() === "—" && td.closest("tr.sr-price")) td.classList.add("blank"); else td.classList.remove("blank");
        col += Number(td.colSpan) || 1;
      });
    });
  }
  function watchTables() {
    document.querySelectorAll("table.cardify").forEach(t => {
      if (t.dataset.cardified) return;
      t.dataset.cardified = "1";
      labelTable(t);
      const mo = new MutationObserver(() => labelTable(t));
      mo.observe(t, { childList: true, subtree: true });
    });
  }
  const startTables = () => { watchTables(); setTimeout(watchTables, 1500); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startTables, { once: true }); else startTables();

  // stat cards: tap to expand a clamped note (regime evidence etc.)
  document.addEventListener("click", e => {
    const card = e.target.closest && e.target.closest(".stat");
    if (card && !e.target.closest("button,a,select,input")) card.classList.toggle("open");
  });
})();

/* v1.20.79: the running server version in the header of every page, next to the page name (it used to be only in the footer of
   the terminal). Asked from /health on every load, never cached, so after a deploy it shows what is really running. */
(function () {
  "use strict";
  function show() {
    var brand = document.querySelector("header.topbar .brand");
    if (!brand || document.getElementById("srvVer")) return;
    var tag = document.createElement("span");
    tag.id = "srvVer";
    tag.title = "Server version now running";
    tag.style.cssText = "margin-left:8px;font-size:11px;font-weight:600;letter-spacing:0;color:#91a4ba;white-space:nowrap;vertical-align:middle";
    brand.appendChild(tag);
    fetch("/health", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
      if (d && d.version) tag.textContent = "server v" + d.version;
    }).catch(function () {});
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", show); else show();
})();

/* v1.20.88: "Check for app update" (Account page, only inside the Android app). Nothing is checked on its own - only when the
   button is pressed. The app sends its build as OIApp-APK/<n> in the user agent (older APKs send none = 0); if GitHub has a
   newer build an Update button appears: it opens the APK link in Chrome, which downloads it - then tap the download and Install
   (Android always asks for that tap). Your settings stay. */
(function () {
  "use strict";
  function native() { return !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform()); }
  function mine() { var m = /OIApp-APK\/(\d+)/.exec(navigator.userAgent || ""); return m ? +m[1] : 0; }
  function init() {
    var panel = document.getElementById("apkPanel"), btn = document.getElementById("apkCheck"), msg = document.getElementById("apkMsg");
    if (!panel || !btn || !native()) return;
    panel.hidden = false;
    btn.onclick = function () {
      btn.disabled = true; msg.textContent = "Checking…";
      var old = document.getElementById("apkGo"); if (old) old.remove();
      fetch("/api/app/apk", { cache: "no-store", credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        if (!d || d.build == null) { msg.textContent = "Could not read the latest version from GitHub - try again later."; return; }
        if (!(d.build > mine())) { msg.textContent = "You have the latest app (build " + mine() + ")."; return; }
        msg.textContent = "New app version available (build " + d.build + ", you have " + mine() + "). ";
        var go = document.createElement("button");
        go.id = "apkGo"; go.type = "button"; go.textContent = "Update";
        go.onclick = function () {
          msg.textContent = "Opening the download in Chrome… when it finishes, tap it and choose Install. Your settings stay.";
          window.location.href = d.url;        // not an app address, so the app hands it to Chrome, which downloads it
        };
        btn.after(go);
      }).catch(function () { msg.textContent = "Could not check - no connection?"; })
        .then(function () { btn.disabled = false; });
    };
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
