/* Account sync (v1.10): keeps the data app.js / dashboard.js store in localStorage mirrored to the signed-in account.
   - Loads first, pulls the account's saved data into localStorage, THEN loads the real page script (data-next), so the
     app starts with the latest data from any device.
   - Every later localStorage write to a synced key is saved to the server automatically (debounced), with version
     checks so two devices never silently overwrite each other (paper trades are merged by trade id).
   - Works offline: changes stay in localStorage marked "unsaved" and are retried. */
(function () {
  "use strict";
  const NEXT = document.currentScript && document.currentScript.dataset.next;
  const MAP = {                                   // localStorage key -> account data key
    "option-intelligence-paper-trades-v1": "paper-trades",
    "option-intelligence-paper-capital-v1": "paper-capital",
    "option-intelligence-paper-guard-v1": "paper-guard",
    "oi-signal-mode": "pref-signal-mode",
    "oi-refresh-sec": "pref-refresh-sec",
    "oi-dc-refresh": "pref-dash-refresh",
  };
  const REV = Object.fromEntries(Object.entries(MAP).map(([l, s]) => [s, l]));
  const META_KEY = "oi-sync-meta";
  const DEBOUNCE_MS = 1500, QUOTE_ONLY_THROTTLE_MS = 30000, PULL_EVERY_MS = 60000, KEEPALIVE_LIMIT = 60000;

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
  let statusEl = null, lastSavedAt = null, offline = false;

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
  function mergeTrades(local, server) {
    const a = Array.isArray(local) ? local : [], b = Array.isArray(server) ? server : [];
    const byId = new Map();
    [...b, ...a].forEach(t => {                    // local second, so ties go to this device
      if (!t || t.id == null) return;
      const old = byId.get(t.id);
      if (!old) { byId.set(t.id, t); return; }
      const rank = x => (x.status === "CLOSED" ? 2 : 0) + (x.exit_time ? 1 : 0);
      byId.set(t.id, rank(t) >= rank(old) ? t : old);   // a closed trade beats an open copy of the same trade
    });
    return [...byId.values()].sort((x, y) => String(y.opened_at || "").localeCompare(String(x.opened_at || "")));
  }
  function merge(sKey, local, server, localWins) {
    if (sKey === "paper-trades") return mergeTrades(local, server);
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
      Object.keys(MAP).forEach(ls.del); meta = { user: user.id, keys: {} };
    } else if (meta.user == null) {
      meta.user = user.id;                                              // data from before accounts existed belongs to the first account that signs in
    }
    const toPush = [];
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
    saveMeta(); offline = false;
    toPush.forEach(k => schedule(k, 300));
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
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flushAll(true); });
  window.addEventListener("pagehide", () => flushAll(true));
  setInterval(async () => {                                              // pick up changes made on another device
    if (document.visibilityState !== "visible" || redirecting || Object.keys(inflight).length) return;
    if (Object.values(meta.keys).some(m => m.dirty)) return;
    try { await pull(false); refreshStatus(); } catch { offline = true; refreshStatus(); }
  }, PULL_EVERY_MS);

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
      Object.keys(MAP).forEach(ls.del); ls.del(META_KEY);              // nothing left behind for the next person on this browser
      location.replace("/login");
    };
    chip.append(statusEl, name, out); host.appendChild(chip);
    if (!user.is_admin) document.querySelectorAll('a[href="/api/login"]').forEach(a => { a.hidden = true; });
    refreshStatus();
  }
  function whenReady(fn) { if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn); else fn(); }

  function loadNext() {
    if (!NEXT) return;
    const s = document.createElement("script"); s.src = NEXT; document.body.appendChild(s);
  }

  (async function boot() {
    let ok = true;
    try { ok = await pull(true); } catch (e) { offline = true; }       // server unreachable: carry on with this device's data
    if (!ok) return;                                                   // redirecting to /login
    whenReady(() => { mountChip(); loadNext(); });
  })();

  window.OISync = { flush: () => flushAll(false), status: () => ({ user, offline, dirty: Object.entries(meta.keys).filter(([, m]) => m.dirty).map(([k]) => k) }) };
})();
