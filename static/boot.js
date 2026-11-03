/* Instant start (v1.18.8). Loaded first on the terminal, Data Check and Signals pages.
   Every successful read of live data (candles, analysis, derivatives, dashboard, signals, option chain) is kept on this device.
   The next time the page opens, the FIRST request for each of those is answered at once from the saved copy while the real
   request runs in the background, so the screen fills instantly even if the server is waking up or slow. The very next
   refresh (2-6 s) picks up the live answer. A small badge shows how old the saved copy is until the live data lands.
   Nothing else is cached: prices ticks, quotes, paper trades, account and push calls always go to the server. */
(function () {
  "use strict";
  const PREFIX = "oi-bc1:", MAX_AGE = 12 * 3600e3, MAX_BYTES = 1500000, BOOT_WINDOW_MS = 20000;
  const WATCH = [/^\/api\/candles\?(?!.*instrument_key)/, /^\/api\/analysis\?/, /^\/api\/live-derivatives\?/, /^\/api\/dashboard\/(summary|premium-graph)\?/,
    /^\/api\/signals(\/summary|\/config)?(\?|$)/, /^\/api\/option-chain\?/, /^\/api\/option-contracts\?/];
  const origFetch = window.fetch.bind(window);
  const served = new Set(), inflight = {};
  let pending = 0, badge = null, oldest = 0;
  const ls = {
    get: k => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); return true; } catch { return false; } },
    del: k => { try { localStorage.removeItem(k); } catch { } },
    keys: () => { try { return Object.keys(localStorage).filter(k => k.startsWith(PREFIX)); } catch { return []; } },
  };
  function clearAll() { ls.keys().forEach(ls.del); }
  window.OIBootCache = { clear: clearAll };

  const keyOf = input => {
    try {
      const u = new URL(typeof input === "string" ? input : input.url, location.origin);
      return u.origin === location.origin ? u.pathname + u.search : null;
    } catch { return null; }
  };
  function store(k, text) {
    if (text.length > MAX_BYTES) return;
    const rec = JSON.stringify({ t: Date.now(), b: text });
    if (ls.set(PREFIX + k, rec)) return;
    // storage full: drop the oldest saved copies until it fits
    const all = ls.keys().map(x => { try { return [x, JSON.parse(ls.get(x)).t]; } catch { return [x, 0]; } }).sort((a, b) => a[1] - b[1]);
    for (const [x] of all) { ls.del(x); if (ls.set(PREFIX + k, rec)) return; }
  }
  function load(k) {
    const raw = ls.get(PREFIX + k); if (!raw) return null;
    try { const r = JSON.parse(raw); return Date.now() - r.t < MAX_AGE ? r : null; } catch { return null; }
  }
  function showBadge() {
    if (!badge) {
      badge = document.createElement("div"); badge.setAttribute("role", "status");
      badge.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);bottom:calc(var(--nav-h,0px) + env(safe-area-inset-bottom,0px) + 10px);z-index:995;white-space:nowrap;background:#2b2517;border:1px solid #66552c;color:#f5d995;border-radius:999px;padding:4px 10px;font:600 11px/1.3 system-ui,sans-serif;pointer-events:none;max-width:90vw";
      (document.body || document.documentElement).appendChild(badge);
    }
    const mins = Math.round((Date.now() - oldest) / 60000);
    badge.textContent = "Saved data" + (mins >= 1 ? " from " + (mins >= 120 ? Math.round(mins / 60) + " h" : mins + " min") + " ago" : "") + " · updating…";
  }
  function hideBadge() { if (badge) { badge.remove(); badge = null; } }
  function settled() { pending = Math.max(0, pending - 1); if (!pending) setTimeout(hideBadge, 400); }

  // A caller that waits on the shared in-flight request must still honour ITS OWN abort signal and a deadline, otherwise a hung
  // request would leave the page on "Loading…" forever (the original caller's timer is already cleared by then).
  function withDeadline(p, signal, ms) {
    return new Promise((resolve, reject) => {
      const abort = () => reject(new DOMException("Aborted", "AbortError"));
      const t = setTimeout(abort, ms);
      if (signal) { if (signal.aborted) return abort(); signal.addEventListener("abort", () => { clearTimeout(t); abort(); }, { once: true }); }
      p.then(v => { clearTimeout(t); resolve(v); }, e => { clearTimeout(t); reject(e); });
    });
  }

  function fake(rec, k) {
    let body = rec.b;
    if (k.startsWith("/api/analysis")) {                    // an old "confirmed" event must never ring the alert again
      try { const o = JSON.parse(body); if (o && o.reconfirm) o.reconfirm.event = null; o.from_saved_copy = true; body = JSON.stringify(o); } catch { }
    }
    return new Response(body, { status: 200, headers: { "Content-Type": "application/json", "X-OI-Saved-At": String(rec.t) } });
  }

  window.fetch = function (input, init) {
    const method = ((init && init.method) || (typeof input !== "string" && input.method) || "GET").toUpperCase();
    const k = method === "GET" ? keyOf(input) : null;
    if (!k || !WATCH.some(rx => rx.test(k))) return origFetch(input, init);
    if (inflight[k]) { const p = inflight[k]; delete inflight[k]; return withDeadline(p.then(r => r.clone()), init && init.signal, 20000); }   // 2nd call: wait for the live answer already on its way
    // the request that stands in for this call must never be able to hang forever (its caller's own timer ends as soon as the saved copy is returned)
    const ctl = new AbortController(), kill = setTimeout(() => ctl.abort(), 25000);
    if (init && init.signal) init.signal.addEventListener("abort", () => ctl.abort(), { once: true });
    const real = origFetch(input, { ...(init || {}), signal: ctl.signal });
    real.then(() => clearTimeout(kill), () => clearTimeout(kill));
    real.then(res => {
      if (res.status === 401) clearAll();
      else if (res.ok) res.clone().text().then(t => store(k, t)).catch(() => { });
    }).catch(() => { });
    const rec = !served.has(k) && performance.now() < BOOT_WINDOW_MS ? load(k) : null;
    if (!rec) return real;
    served.add(k); inflight[k] = real;
    setTimeout(() => { if (inflight[k] === real) delete inflight[k]; }, 30000);
    oldest = oldest ? Math.min(oldest, rec.t) : rec.t;
    pending++; real.then(settled, settled);
    if (Date.now() - rec.t > 20000) { showBadge(); setTimeout(hideBadge, 10000); }
    return Promise.resolve(fake(rec, k));
  };
})();
