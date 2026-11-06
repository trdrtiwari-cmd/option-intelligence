/* Account sync (v1.10): keeps the data app.js / dashboard.js store in localStorage mirrored to the signed-in account.
   - Loads first, pulls the account's saved data into localStorage, THEN loads the real page script (data-next), so the
     app starts with the latest data from any device.
   - Every later localStorage write to a synced key is saved to the server automatically (debounced), with version
     checks so two devices never silently overwrite each other (paper trades are merged by trade id).
   - Works offline: changes stay in localStorage marked "unsaved" and are retried. */
(function () {
  "use strict";
  const NEXT = document.currentScript && document.currentScript.dataset.next;
  const SELF_SRC = (document.currentScript && document.currentScript.src) || "";
  const MAP = {                                   // localStorage key -> account data key
    "option-intelligence-paper-trades-v1": "paper-trades",
    "option-intelligence-paper-deleted-v1": "paper-deleted-trade-ids",
    "option-intelligence-paper-capital-v1": "paper-capital",
    "option-intelligence-paper-guard-v1": "paper-guard",
    "oi-signal-mode": "pref-signal-mode",
    "oi-refresh-v2": "pref-refresh-v2",
    "oi-dc-refresh": "pref-dash-refresh",
    // v1.17: every other setting is now saved to the account too
    "oi-score-min": "pref-score-min",
    "oi-reconfirm": "pref-reconfirm",
    "oi-alert-sound": "pref-alert-sound",
    "oi-sig-qty": "pref-sig-qty",
    "oi-sig-delta": "pref-sig-delta",
    "oi-settings-v1": "settings",
    "oi-chart-v1": "pref-chart",                  // v1.20.43 chart window: indicators, colours, layouts                 // symbol, timeframe, chart overlays, paper-trade form, filters... (see prefs.js)
  };
  const REV = Object.fromEntries(Object.entries(MAP).map(([l, s]) => [s, l]));
  const META_KEY = "oi-sync-meta";
  const DEBOUNCE_MS = 1500, QUOTE_ONLY_THROTTLE_MS = 30000, PULL_EVERY_MS = 30000, KEEPALIVE_LIMIT = 60000;

  const origSet = Storage.prototype.setItem, origRemove = Storage.prototype.removeItem, origGet = Storage.prototype.getItem;
  const origFetch = window.fetch.bind(window);
  const ls = {
    get: k => { try { return origGet.call(localStorage, k); } catch { return null; } },
    set: (k, v) => { try { origSet.call(localStorage, k, v); } catch { } },
    del: k => { try { origRemove.call(localStorage, k); } catch { } },
  };

  let meta = (() => { try { const m = JSON.parse(ls.get(META_KEY) || "null"); if (m && m.keys) return m; } catch { } return { user: null, keys: {} }; })();
  let user = null, redirecting = false;
  const timers = {}, inflight = {}, lastSig = {}, lastPushAt = {}, retries = {};
  let statusEl = null, lastSavedAt = null, offline = false, lastOk = null, lastErr = null;

  const saveMeta = () => ls.set(META_KEY, JSON.stringify(meta));
  const fromRaw = raw => { try { return JSON.parse(raw); } catch { return raw; } };
  const toRaw = v => typeof v === "string" ? v : JSON.stringify(v);
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const kmeta = sKey => meta.keys[sKey] || (meta.keys[sKey] = { version: 0, dirty: false });

  function redirectToLogin() {
    if (redirecting) return;
    redirecting = true;
    location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search));
  }

  // A signed-out session on any API call sends the user to the login page (their unsaved local data stays in localStorage).
  window.fetch = async function (input, init) {
    const res = await origFetch(input, init);
    if (res.status === 401) {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      if (url.startsWith("/api/") && !url.startsWith("/api/auth/")) redirectToLogin();
    }
    return res;
  };

  // ---------------------------------------------------------------- merging
  function deletedTradeIds() {
    try { const v = JSON.parse(ls.get("option-intelligence-paper-deleted-v1") || "[]"); return new Set(Array.isArray(v) ? v.map(String) : []); }
    catch { return new Set(); }
  }
  function mergeTrades(local, server) {
    const deleted = deletedTradeIds();
    const a = Array.isArray(local) ? local : [], b = Array.isArray(server) ? server : [];
    const byId = new Map();
    [...b, ...a].forEach(t => {                    // local second, so ties go to this device
      if (!t || t.id == null) return;
      const old = byId.get(t.id);
      if (!old) { byId.set(t.id, t); return; }
      const rank = x => (x.status === "CLOSED" ? 2 : 0) + (x.exit_time ? 1 : 0);
      byId.set(t.id, rank(t) >= rank(old) ? t : old);   // a closed trade beats an open copy of the same trade
    });
    return [...byId.values()].filter(t => !deleted.has(String(t.id))).sort((x, y) => String(y.opened_at || "").localeCompare(String(x.opened_at || "")));
  }
  // settings = { field: [value, savedAtMs] } - the newest write of each field wins, so two devices changing different
  // settings never overwrite each other.
  function mergeSettings(a, b) {
    const ok = x => x && typeof x === "object" && !Array.isArray(x);
    a = ok(a) ? a : {}; b = ok(b) ? b : {};
    const out = {};
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      const x = Array.isArray(a[k]) ? a[k] : null, y = Array.isArray(b[k]) ? b[k] : null;
      out[k] = !x ? y : !y ? x : ((y[1] || 0) > (x[1] || 0) ? y : x);
    }
    return out;
  }
  function merge(sKey, local, server, localWins) {
    if (sKey === "paper-trades") return mergeTrades(local, server);
    if (sKey === "settings") return mergeSettings(local, server);
    return localWins ? local : server;
  }
  // Fields that change on every price refresh; a change in only these is saved on a slow timer instead of immediately.
  function signature(sKey, value) {
    if (sKey !== "paper-trades" || !Array.isArray(value)) return JSON.stringify(value);
    return JSON.stringify(value.map(t => [t.id, t.status, t.qty, t.sl, t.exit_price, t.exit_time, t.exit_reason, t.realized_pnl, t.t1, t.t2, t.t3, t.exit_target]));
  }

  // ---------------------------------------------------------------- status chip
  function setStatus(kind, text) {
    if (!statusEl) return;
    statusEl.className = "save-state " + kind;
    statusEl.textContent = text;
  }
  function refreshStatus() {
    const pending = Object.values(meta.keys).some(m => m.dirty);
    if (offline) return setStatus("err", pending ? "Offline — unsaved, will retry" : "Offline");
    if (Object.keys(inflight).length) return setStatus("busy", "Saving…");
    if (pending) return setStatus("busy", "Changes pending…");
    setStatus("ok", lastSavedAt ? "Saved " + lastSavedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "Saved");
  }

  // ---------------------------------------------------------------- pull (server -> this browser)
  async function pull(first) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), first ? 8000 : 15000);
    let res;
    try { res = await origFetch("/api/userdata", { credentials: "same-origin", signal: ctl.signal }); } finally { clearTimeout(t); }
    if (res.status === 401) { redirectToLogin(); return false; }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    user = d.user;
    if (meta.user != null && meta.user !== user.id) {                    // someone else used this browser: never mix accounts
      Object.keys(MAP).forEach(ls.del); meta = { user: user.id, keys: {} }; try { window.OIBootCache && window.OIBootCache.clear(); } catch { }
    } else if (meta.user == null) {
      meta.user = user.id;                                              // data from before accounts existed belongs to the first account that signs in
    }
    const toPush = [], before = {};
    for (const lsKey of Object.keys(MAP)) before[lsKey] = ls.get(lsKey);
    for (const [lsKey, sKey] of Object.entries(MAP)) {
      const srv = d.data[sKey], raw = ls.get(lsKey), m = kmeta(sKey);
      if (!srv) {
        if (raw !== null) { m.dirty = true; m.version = 0; toPush.push(sKey); }           // first sign-in: upload what this browser already has
        else { m.dirty = false; m.version = 0; }
      } else if (raw === null) {
        ls.set(lsKey, toRaw(srv.value)); m.version = srv.version; m.dirty = false;
      } else if (m.version === srv.version) {
        if (m.dirty) toPush.push(sKey);
      } else {                                                          // the account changed elsewhere (or this browser never synced)
        const neverSynced = !m.version, localVal = fromRaw(raw);
        if (!neverSynced && !m.dirty) {
          ls.set(lsKey, toRaw(srv.value)); m.version = srv.version;
        } else {
          const merged = merge(sKey, localVal, srv.value, !neverSynced && m.dirty);
          ls.set(lsKey, toRaw(merged)); m.version = srv.version; m.dirty = !same(merged, srv.value);
          if (m.dirty) toPush.push(sKey);
        }
      }
      lastSig[sKey] = signature(sKey, fromRaw(ls.get(lsKey)));
    }
    // The tombstone key may be fetched after paper-trades. Apply it once all keys are loaded
    // so a deletion on another device cannot be resurrected by the merge.
    const tradeKey = "option-intelligence-paper-trades-v1";
    const tradeRaw = ls.get(tradeKey);
    if (tradeRaw !== null) {
      const filtered = mergeTrades(fromRaw(tradeRaw), []);
      if (!same(filtered, fromRaw(tradeRaw))) {
        ls.set(tradeKey, toRaw(filtered));
        const tm = kmeta("paper-trades"); tm.dirty = true;
        if (!toPush.includes("paper-trades")) toPush.push("paper-trades");
      }
    }
    saveMeta(); offline = false;
    toPush.forEach(k => schedule(k, 300));
    const changed = Object.keys(MAP).filter(k => before[k] !== ls.get(k));
    if (changed.length) { try { window.dispatchEvent(new CustomEvent("oi-sync-pulled", { detail: { changed } })); } catch { } }
    return true;
  }

  // ---------------------------------------------------------------- push (this browser -> server)
  function schedule(sKey, delay) {
    clearTimeout(timers[sKey]);
    timers[sKey] = setTimeout(() => push(sKey), delay);
    refreshStatus();
  }
  function markDirty(sKey) {
    const m = kmeta(sKey); m.dirty = true; saveMeta();
    const raw = ls.get(REV[sKey]), sig = raw === null ? "null" : signature(sKey, fromRaw(raw));
    const structural = sig !== lastSig[sKey];
    const sinceLast = Date.now() - (lastPushAt[sKey] || 0);
    schedule(sKey, structural ? DEBOUNCE_MS : Math.max(DEBOUNCE_MS, QUOTE_ONLY_THROTTLE_MS - sinceLast));
  }

  async function push(sKey, opts) {
    opts = opts || {};
    if (redirecting || inflight[sKey]) { if (inflight[sKey]) schedule(sKey, 800); return; }
    const lsKey = REV[sKey], m = kmeta(sKey), raw = ls.get(lsKey);
    if (!m.dirty) return;
    inflight[sKey] = true; refreshStatus();
    try {
      let res;
      if (raw === null) {
        res = await origFetch("/api/userdata/" + sKey, { method: "DELETE", credentials: "same-origin", keepalive: !!opts.keepalive });
        if (res.status === 401) return redirectToLogin();
        if (!res.ok) throw new Error("HTTP " + res.status);
        m.version = 0; m.dirty = false;
      } else {
        const body = JSON.stringify({ value: fromRaw(raw), base_version: m.version || null });
        res = await origFetch("/api/userdata/" + sKey, { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" },
          body, keepalive: !!opts.keepalive && body.length < KEEPALIVE_LIMIT });
        if (res.status === 401) return redirectToLogin();
        if (res.status === 409) {                                      // another device saved first: merge, then try again
          const j = await res.json().catch(() => ({}));
          if (j.current) {
            const merged = merge(sKey, fromRaw(raw), j.current.value, true);
            ls.set(lsKey, toRaw(merged)); m.version = j.current.version; m.dirty = true;
          } else { await pull(false); }
          retries[sKey] = (retries[sKey] || 0) + 1;
          if (retries[sKey] <= 4) schedule(sKey, 400);
          return;
        }
        if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(typeof j.detail === "string" ? j.detail : "HTTP " + res.status); }
        const j = await res.json();
        m.version = j.version; retries[sKey] = 0;
        m.dirty = ls.get(lsKey) !== raw;                               // changed again while saving -> save once more
        lastSig[sKey] = signature(sKey, fromRaw(raw));
      }
      lastPushAt[sKey] = Date.now(); lastSavedAt = new Date(); offline = false; saveMeta();
      if (m.dirty) schedule(sKey, DEBOUNCE_MS);
    } catch (e) {
      offline = true;
      const n = (retries[sKey] = (retries[sKey] || 0) + 1);
      schedule(sKey, Math.min(60000, 5000 * Math.pow(2, Math.min(n, 4))));
    } finally { delete inflight[sKey]; refreshStatus(); }
  }
  async function flushAll(keepalive) {
    const keys = Object.keys(REV).filter(k => kmeta(k).dirty);
    keys.forEach(k => clearTimeout(timers[k]));
    await Promise.all(keys.map(k => push(k, { keepalive })));
  }

  // Intercept the app's own localStorage writes (no change to app.js needed).
  Storage.prototype.setItem = function (k, v) {
    const watched = this === localStorage && MAP[k], prev = watched ? ls.get(k) : null;
    origSet.call(this, k, v);
    if (watched && prev !== String(v)) markDirty(MAP[k]);              // re-writing the same value is not a change
  };
  Storage.prototype.removeItem = function (k) {
    const watched = this === localStorage && MAP[k], had = watched ? ls.get(k) !== null : false;
    origRemove.call(this, k);
    if (watched && had) markDirty(MAP[k]);
  };

  window.addEventListener("online", () => { offline = false; Object.keys(REV).forEach(k => { if (kmeta(k).dirty) schedule(k, 200); }); });
  // Pick up changes made on another device: every 30 s while the page is open, and immediately when the app/tab comes back
  // to the foreground (a phone app that was in the background does not run timers).
  let lastPull = 0;
  async function pullNow(force) {
    if (redirecting || Object.keys(inflight).length) return;
    if (force !== true && document.visibilityState !== "visible") return;
    // Local changes waiting to upload must NOT block checking for other devices' changes (open paper trades keep this flag
    // set while the market is open). pull() merges them safely; make sure ours are on their way too.
    Object.keys(REV).forEach(k => { if (kmeta(k).dirty && !timers[k]) schedule(k, 300); });
    if (force !== true && Date.now() - lastPull < 2000) return;
    lastPull = Date.now();
    try { await pull(false); lastOk = Date.now(); lastErr = null; refreshStatus(); }
    catch (e) { offline = true; lastErr = String((e && e.message) || e); refreshStatus(); }
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushAll(true); else pullNow(); });
  window.addEventListener("focus", pullNow);
  window.addEventListener("pageshow", pullNow);
  window.addEventListener("pagehide", () => flushAll(true));
  setInterval(pullNow, PULL_EVERY_MS);

  // ---------------------------------------------------------------- header chip + logout
  function mountChip() {
    const host = document.querySelector(".top-actions");
    if (!host || !user || document.getElementById("userChip")) return;
    const chip = document.createElement("span"); chip.id = "userChip"; chip.className = "user-chip";
    statusEl = document.createElement("span"); statusEl.id = "saveState"; statusEl.className = "save-state ok"; statusEl.title = "Your data is saved to your account automatically";
    const name = document.createElement("a"); name.href = "/account"; name.textContent = user.username; name.title = "Account settings";
    const out = document.createElement("button"); out.type = "button"; out.className = "secondary"; out.textContent = "Log out";
    out.onclick = async () => {
      out.disabled = true; out.textContent = "Saving…";
      try { await flushAll(false); } catch { }
      if (Object.values(meta.keys).some(m => m.dirty) && !confirm("Some recent changes could not be saved to your account yet. Log out anyway and lose them?")) { out.disabled = false; out.textContent = "Log out"; return; }
      try { await origFetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); } catch { }
      Object.keys(MAP).forEach(ls.del); ls.del(META_KEY); ls.del("oi-sync-user"); try { window.OIBootCache && window.OIBootCache.clear(); } catch { }   // nothing left behind for the next person on this browser
      location.replace("/login");
    };
    chip.append(statusEl, name, out); host.appendChild(chip);
    if (!user.is_admin) document.querySelectorAll('a[href="/api/login"]').forEach(a => { a.hidden = true; });
    refreshStatus();
  }
  function whenReady(fn) { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn); else fn(); }

  function loadScript(src, done) {
    const s = document.createElement("script"); s.src = src; s.onload = s.onerror = done || null; document.body.appendChild(s);
  }
  function loadNext() {
    // prefs.js restores the saved settings into the page's controls BEFORE the page script reads them.
    const v = (SELF_SRC.match(/[?&]v=([\d.]+)/) || [])[1] || "";
    loadScript("/static/prefs.js" + (v ? "?v=" + v : ""), () => { if (NEXT) loadScript(NEXT); });
  }

  (async function boot() {
    // Fast start (v1.18.8): a device that has synced before opens the page IMMEDIATELY with its own saved data and checks the
    // account in the background (prefs.js / app.js already apply changes from other devices when the pull finishes). Only a
    // brand-new device waits for the first pull, because it has nothing of its own yet.
    let cached = null;
    try { cached = JSON.parse(ls.get("oi-sync-user") || "null"); } catch { }
    if (cached && cached.id != null && meta.user === cached.id) {
      user = cached;
      whenReady(() => { mountChip(); loadNext(); });
      try {
        const ok = await pull(true);
        if (ok && user && user.id !== cached.id) location.reload();     // another account signed in elsewhere: restart clean
        else if (ok) { ls.set("oi-sync-user", JSON.stringify(user)); refreshStatus(); }
      } catch (e) { offline = true; refreshStatus(); }
      return;
    }
    let ok = true;
    try { ok = await pull(true); } catch (e) { offline = true; }       // server unreachable: carry on with this device's data
    if (!ok) return;                                                   // redirecting to /login
    if (user) ls.set("oi-sync-user", JSON.stringify(user));
    whenReady(() => { mountChip(); loadNext(); });
  })();

  window.OISync = { flush: () => flushAll(false), pull: pullNow, sync: async () => { await flushAll(false); await pullNow(true); return { ok: !lastErr, error: lastErr }; }, status: () => ({ user, offline, lastOk, lastErr, dirty: Object.entries(meta.keys).filter(([, m]) => m.dirty).map(([k]) => k) }) };
})();
