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
  window.__oiIssues = { report, flush };
})();
