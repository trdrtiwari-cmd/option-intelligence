/* v1.20.67 bandwidth - must run before any page script, so it sits at the top of issues.js (loaded first everywhere). */
(function () {
"use strict";
// ------------------------------------------------------------- v1.20.67: only the tab on screen talks to the server
// Every tab (Terminal, Chart, Data, Signals, Tests) is kept alive in its own frame. A hidden frame still reported
// document.visibilityState === "visible", so all five pages polled the server and two of them held live sockets at the
// same time - this used up the 5 GB monthly bandwidth on Render. Now a page that is not the shown tab reports "hidden":
// every existing "only when visible" check pauses it, and its live socket is closed until the tab is opened again.
let tabActive = true;
try {
  const fr = window.self !== window.top && window.frameElement;
  if (fr && fr.style.display === "none") tabActive = false;
} catch { }
try {
  const nv = Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState");
  if (nv && nv.get) {
    Object.defineProperty(document, "visibilityState", { configurable: true, get() { const v = nv.get.call(document); return v === "visible" && !tabActive ? "hidden" : v; } });
    Object.defineProperty(document, "hidden", { configurable: true, get() { return document.visibilityState !== "visible"; } });
  }
} catch { }
function setTabActive(on) {
  on = !!on; if (on === tabActive) return;
  tabActive = on;
  try { document.dispatchEvent(new Event("visibilitychange")); } catch { }
}
window.__oiSetTabActive = setTabActive;
// Market open / closed for every page. The Terminal stores the server's answer (it knows NSE holidays) each minute; if that is
// missing or old, fall back to the clock (Mon-Fri 09:00-15:35 IST). Closed = holidays, weekends, before 09:00 and after 15:35.
const MKT_KEY = "oi-market-v1";
window.__oiSetMarket = open => { try { localStorage.setItem(MKT_KEY, JSON.stringify({ open: !!open, at: Date.now() })); } catch { } };
window.__oiMarketOpen = () => {
  try { const m = JSON.parse(localStorage.getItem(MKT_KEY) || "null"); if (m && Date.now() - m.at < 10 * 60e3) return !!m.open; } catch { }
  const d = new Date(Date.now() + 5.5 * 3600e3), dow = d.getUTCDay(), min = d.getUTCHours() * 60 + d.getUTCMinutes();
  return dow >= 1 && dow <= 5 && min >= 540 && min <= 935;
};
window.__oiClosedWait = 15 * 60e3;     // market closed: pages reload their data every 15 min only (the data cannot change)
window.addEventListener("message", e => { if (e.origin === location.origin && e.source === window.parent && e.data && typeof e.data.oiActive === "boolean") setTabActive(e.data.oiActive); });
if (!tabActive) setTimeout(() => { try { document.dispatchEvent(new Event("visibilitychange")); } catch { } }, 0);
})();
/* v1.20.49: page problem reporter - loaded first on every page.
   Sends the problems this page hits to the server (POST /api/client-issues), so "Download for Claude" (day / week / month) lists
   them from every tab and every device, without anyone having to notice or screenshot them:
     error    - script errors and failed promises
     request  - server calls that failed (HTTP 400+ except sign-in, or no connection)
     warning  - "⚠ ..." warnings shown on the page (checked every 30 s)
     message  - pop-up messages that report a problem (failed / unavailable / could not ...)
   Each different problem is sent once per page load with a count; nothing is sent while signed out. */
(function () {
  "use strict";
  if (window.__oiIssues) return;
  const nativeFetch = window.fetch.bind(window);
  const path = location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/login") return;
  const PAGE = { "/": "terminal", "/chart": "chart", "/dashboard": "data", "/signals": "signals", "/diagnostics": "tests", "/account": "account" }[path] || path;
  const device = () => (window.oiDesktop ? "windows-app" : window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform() ? "android-app"
    : window.matchMedia("(max-width:820px)").matches ? "phone-browser" : "computer-browser");
  const SCRIPT_V = (() => { try { return new URL(document.currentScript.src).searchParams.get("v") || ""; } catch { return ""; } })();
  const version = () => { try { return typeof FRONTEND_VERSION !== "undefined" ? FRONTEND_VERSION : SCRIPT_V; } catch { return SCRIPT_V; } };
  const seen = new Map(), queue = [];
  function report(kind, msg, detail, once) {
    msg = String(msg || "").replace(/\s+/g, " ").trim().slice(0, 300);
    if (!msg) return;
    const k = kind + "|" + msg, s = seen.get(k);
    if (s && once) return;
    if (s) { s.n++; if (!s.queued) { s.queued = true; queue.push(s); } return; }
    const it = { kind, msg, detail: String(detail || "").slice(0, 300), page: PAGE, n: 1, queued: true };
    seen.set(k, it); queue.push(it);
    if (seen.size > 300) seen.delete(seen.keys().next().value);
  }
  async function flush(keepalive) {
    if (!queue.length) return;
    const items = queue.splice(0, 60).map(it => { it.queued = false; const o = { kind: it.kind, msg: it.msg, detail: it.detail, page: it.page, n: it.n, device: device(), version: version() }; it.n = 0; return o; })
      .filter(o => o.n > 0);
    if (!items.length) return;
    try { await nativeFetch("/api/client-issues", { method: "POST", credentials: "same-origin", keepalive: !!keepalive, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) }); } catch { }
  }
  setInterval(() => flush(false), 15000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(true); });
  window.addEventListener("pagehide", () => flush(true));

  window.addEventListener("error", e => {
    if (!e.message && e.target && e.target !== window) { const src = e.target.src || e.target.href || ""; if (src) report("error", "could not load " + String(src).split("?")[0].split("/").slice(-2).join("/")); return; }
    report("error", e.message || e.error, String(e.filename || "").split("/").pop().split("?")[0] + ":" + (e.lineno || ""));
  }, true);
  window.addEventListener("unhandledrejection", e => { const r = e.reason; report("error", "promise: " + ((r && r.message) || r)); });

  const quiet = /\/api\/(client-issues|auth\/me|userdata)/;
  window.fetch = async function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    const where = (() => { try { const u = new URL(url, location.href); return u.origin === location.origin ? u.pathname : u.host; } catch { return String(url).slice(0, 80); } })();
    try {
      const res = await nativeFetch(input, init);
      if (res.status >= 400 && res.status !== 401 && !quiet.test(where)) {
        let d = ""; try { const c = res.clone(); d = String((await c.json()).detail || ""); } catch { }
        report("request", `${(init && init.method) || "GET"} ${where} -> HTTP ${res.status}`, d);
      }
      return res;
    } catch (err) {
      if (!quiet.test(where) && !(err && err.name === "AbortError")) report("request", `${(init && init.method) || "GET"} ${where} -> no connection`, err && err.message);
      throw err;
    }
  };

  const PROBLEM = /fail|error|unavailable|could not|couldn't|can't|cannot|not available|blocked|denied|timed out|⚠/i;
  function watchToasts() {
    ["toast", "oiSaveToast"].forEach(id => {
      const el = document.getElementById(id);
      if (!el || el.__oiw) return; el.__oiw = true;
      new MutationObserver(() => { const t = el.textContent || ""; if (PROBLEM.test(t)) report("message", t); }).observe(el, { childList: true, characterData: true, subtree: true });
    });
  }
  function scanWarnings() {
    watchToasts();
    if (!document.body) return;
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node, n = 0;
    while ((node = w.nextNode()) && n < 4000) {
      n++;
      const t = node.nodeValue;
      if (!t || t.indexOf("⚠") < 0) continue;
      const el = node.parentElement;
      if (!el || el.closest("[hidden]") || el.offsetParent === null && getComputedStyle(el).position !== "fixed") continue;
      report("warning", (el.textContent || t).trim(), "", true);
    }
  }
  window.addEventListener("load", () => { setTimeout(scanWarnings, 8000); setInterval(scanWarnings, 30000); });

  // ---------------------------------------------------------------- v1.20.56: what the page shows (no screenshots needed)
  // Every minute the page builds a compact snapshot - the choice in every menu, every tick box, short number / text fields and
  // labelled values, the warnings shown - and sends it when it changed (at least every 15 min). Passwords / keys never included.
  const framed = window.self !== window.top;
  const visible = el => !!(el && (el.offsetParent !== null || getComputedStyle(el).position === "fixed") && !el.closest("[hidden]"));
  function snapshot() {
    const st = { page: PAGE, path: location.pathname + location.search.replace(/key=[^&]*/i, "key=…"), title: document.title, device: device(), version: version(),
      viewport: `${window.innerWidth}x${window.innerHeight}`, selects: {}, checks: {}, inputs: {}, texts: {}, warnings: [] };
    document.querySelectorAll("select[id]").forEach(el => { const o = el.options[el.selectedIndex]; st.selects[el.id] = o ? o.text.trim() : el.value; });
    document.querySelectorAll("input[id]").forEach(el => {
      if (/pass|token|secret|key/i.test(el.id + el.name) || el.type === "password" || el.type === "hidden" || el.type === "file") return;
      if (el.type === "checkbox" || el.type === "radio") st.checks[el.id] = el.checked; else if (el.value !== "" && el.value.length <= 80) st.inputs[el.id] = el.value;
    });
    let n = 0;
    document.querySelectorAll("[id]").forEach(el => {
      if (n >= 220 || /^(SELECT|INPUT|SCRIPT|STYLE|IFRAME|svg|CANVAS|TEMPLATE|OPTION)$/i.test(el.tagName) || !visible(el)) return;
      const t = (el.innerText || "").replace(/\s+/g, " ").trim();
      if (!t || t.length > 200 || el.querySelector("[id]") && t.length > 60) return;
      st.texts[el.id] = t; n++;
    });
    document.querySelectorAll(".final-action, .stat-value, .card h3, .cw-sig .act").forEach((el, i) => { if (n < 240 && visible(el) && !el.id) { const t = (el.innerText || "").trim(); if (t && t.length < 120) { st.texts["_" + (el.className.split(" ")[0] || "v") + i] = t; n++; } } });
    for (const [k] of seen) if (k.startsWith("warning|")) st.warnings.push(k.slice(8));
    // a chart inside this page (Terminal's live chart): add its key values
    try { const fr = document.querySelector("#confirmChartSlot iframe"); const fd = fr && fr.contentDocument;
      if (fd) ["sym", "tf", "view", "live", "optText", "legend"].forEach(id => { const el = fd.getElementById(id); if (el) st.texts["chart." + id] = (el.value || el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 200); }); } catch { }
    return st;
  }
  let lastHash = "", lastSent = 0;
  const hash = s => { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0; return String(h); };
  async function sendState(force) {
    if (document.visibilityState !== "visible" && !force) return;
    let st; try { st = snapshot(); } catch { return; }
    const body = JSON.stringify(st), h = hash(body.replace(/"viewport":"[^"]*"/, ""));
    if (!force && h === lastHash && Date.now() - lastSent < 15 * 60000) return;
    lastHash = h; lastSent = Date.now();
    try { await nativeFetch("/api/page-state", { method: "POST", credentials: "same-origin", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: st }) }); } catch { }
  }
  window.addEventListener("load", () => { setTimeout(() => sendState(false), 12000); setInterval(() => sendState(false), 60000); });

  // ---------------------------------------------------------------- v1.20.56: "⚑ Report" - a picture of the page + what it shows
  async function capture() {
    if (!window.html2canvas) await new Promise((ok, bad) => { const s = document.createElement("script"); s.src = "https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });
    const scale = Math.min(1, 1400 / Math.max(1, window.innerWidth));
    const cv = await window.html2canvas(document.body, { scale, useCORS: true, backgroundColor: "#0b1220", logging: false,
      x: window.scrollX, y: window.scrollY, width: window.innerWidth, height: window.innerHeight, windowWidth: document.documentElement.clientWidth });
    let q = 0.7, url = cv.toDataURL("image/jpeg", q);
    while (url.length > 850000 && q > 0.3) { q -= 0.15; url = cv.toDataURL("image/jpeg", q); }
    return url.length <= 850000 ? url : null;
  }
  async function doReport(note) {
    let image = null; try { image = await capture(); } catch { }
    const st = snapshot();
    const r = await nativeFetch("/api/client-reports", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note, state: st, image }) });
    return r.ok ? (await r.json()).id : null;
  }
  window.addEventListener("message", async e => {         // the top page asks the tab on screen to report itself
    if (e.origin !== location.origin || !e.data || !e.data.oiReport) return;
    const id = await doReport(e.data.oiReport.note).catch(() => null);
    try { e.source.postMessage({ oiReportDone: { id } }, location.origin); } catch { }
  });
  function reportButton() {
    if (framed || document.getElementById("oiReportBtn")) return;
    const b = document.createElement("button");
    b.id = "oiReportBtn"; b.type = "button"; b.textContent = "⚑ Report"; b.title = "Send Claude a picture of this screen and what it shows, with a note";
    b.style.cssText = "position:fixed;left:12px;bottom:calc(14px + var(--nav-h,0px) + var(--sab,0px));z-index:2100;padding:8px 12px;border-radius:999px;font:600 12.5px system-ui,sans-serif;background:#8a3df0;color:#fff;border:1px solid #a873f5;box-shadow:0 4px 14px rgba(0,0,0,.35);cursor:pointer;opacity:.92";
    b.onclick = async () => {
      const note = window.prompt("What is wrong or worth checking on this screen? (optional)", "");
      if (note === null) return;
      b.disabled = true; b.textContent = "Saving…";
      let id = null;
      const fr = [...document.querySelectorAll("iframe.oi-tab-frame")].find(f => f.style.display !== "none" && f.contentWindow);
      try {
        if (fr) id = await new Promise(res => { const h = e => { if (e.data && e.data.oiReportDone) { window.removeEventListener("message", h); res(e.data.oiReportDone.id); } }; window.addEventListener("message", h); fr.contentWindow.postMessage({ oiReport: { note } }, location.origin); setTimeout(() => res(null), 25000); });
        else id = await doReport(note);
      } catch { }
      b.disabled = false; b.textContent = id ? "✓ Sent" : "⚑ Report (failed)";
      setTimeout(() => { b.textContent = "⚑ Report"; }, 4000);
    };
    document.body.appendChild(b);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", reportButton); else reportButton();
  window.__oiIssues = { report, flush, snapshot, sendState };
})();
