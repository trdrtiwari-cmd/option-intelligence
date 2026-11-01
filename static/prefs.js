/* Settings autosave (v1.17).
   Remembers every setting you change - symbol, timeframe, chart overlays, signal filters, paper-trade form choices,
   diagnostics inputs - and saves it to your account automatically. Open the app on any phone or computer and the same
   settings come back.

   How it fits together:
   - Settings live in ONE localStorage object, "oi-settings-v1" = { "page.field": [value, savedAtMs] }.
   - sync.js (loaded first) mirrors that object to the account (server key "settings") and merges per field, so the newest
     change of each field wins even when two devices edit different settings.
   - This file restores the saved values into the page's controls BEFORE the page script runs, then keeps saving.
   - When another device changes a setting, the new value is applied here within about a minute (the controls update and the
     page refreshes itself, exactly as if you had picked the value by hand).
   Other synced pieces (paper trades, capital, risk guards, signal mode, refresh rate...) keep their own keys in sync.js. */
(function () {
  "use strict";
  const KEY = "oi-settings-v1";
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const PAGE = ({ "/": "terminal", "/dashboard": "dashboard", "/diagnostics": "diagnostics", "/signals": "signals" })[path];

  // Controls saved per page. Order matters when one control resets another (dashboard: symbol resets universe).
  const FIELDS = {
    terminal: ["symbol", "interval", "chartView", "showEma", "showVWAP", "showSR", "srTf", "optionTypeFilter", "moneynessFilter",
      "paperRiskMode", "paperExitTarget", "paperAtrInterval", "paperSlMult", "paperT1Mult", "paperT2Mult", "paperT3Mult"],
    dashboard: ["symbol", "universe", "offset", "interval", "graphMode"],
    diagnostics: ["symbol", "interval", "days", "mode", "scoreMin", "target", "stop", "cost", "horizon"],
    signals: ["symbol"],
  };
  // Controls whose value the page script already stores itself in its own (synced) localStorage key. They are not saved
  // here; we only re-apply them when another device changes them.
  const LEGACY = {
    terminal: { signalMode: "oi-signal-mode", scoreMin: "oi-score-min", reconfirmSec: "oi-reconfirm", alertSound: "oi-alert-sound", refreshEvery: "oi-refresh-sec" },
    dashboard: { refreshEvery: "oi-dc-refresh" },
    signals: { qty: "oi-sig-qty", delta: "oi-sig-delta" },
  };

  const $ = id => document.getElementById(id);
  const read = () => { try { const o = JSON.parse(localStorage.getItem(KEY) || "{}"); return o && typeof o === "object" && !Array.isArray(o) ? o : {}; } catch { return {}; } };
  const write = o => { try { localStorage.setItem(KEY, JSON.stringify(o)); } catch { } };
  const listeners = [];

  function get(name, dflt) { const e = read()[name]; return Array.isArray(e) && e[0] !== undefined ? e[0] : dflt; }
  function set(name, value) {
    const o = read(), cur = o[name];
    if (cur && JSON.stringify(cur[0]) === JSON.stringify(value)) return;      // unchanged -> nothing to save
    o[name] = [value, Date.now()]; write(o);
    listeners.forEach(fn => { try { fn(name, value); } catch { } });
  }

  const isCheck = el => el.type === "checkbox";
  const valueOf = el => isCheck(el) ? !!el.checked : String(el.value);
  function canTake(el, v) {
    if (isCheck(el)) return typeof v === "boolean";
    if (typeof v !== "string") return false;
    if (el.tagName === "SELECT") return [...el.options].some(o => o.value === v);
    if (el.type === "number") return v !== "" && Number.isFinite(Number(v));
    return true;
  }
  function put(el, v) { if (isCheck(el)) el.checked = v; else el.value = v; }

  // Put saved values into the controls. fire=true also sends the change event so the page reacts (cross-device update).
  function apply(fire) {
    if (!PAGE) return;
    const o = read();
    (FIELDS[PAGE] || []).forEach(id => {
      const el = $(id), e = o[PAGE + "." + id];
      if (!el || !Array.isArray(e) || !canTake(el, e[0]) || valueOf(el) === e[0]) return;
      if (fire && document.activeElement === el) return;                         // never fight the user mid-edit
      put(el, e[0]);
      if (fire) el.dispatchEvent(new Event(el.tagName === "SELECT" || isCheck(el) ? "change" : "input", { bubbles: true }));
    });
  }
  function applyLegacy() {                                                       // another device changed a self-stored setting
    const map = LEGACY[PAGE] || {};
    Object.entries(map).forEach(([id, lsKey]) => {
      const el = $(id); let v = null; try { v = localStorage.getItem(lsKey); } catch { }
      if (!el || v === null || el.value === v || document.activeElement === el) return;
      if (el.tagName === "SELECT" && ![...el.options].some(o => o.value === v)) return;
      el.value = v; el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
    });
  }

  function bind() {
    if (!PAGE) return;
    (FIELDS[PAGE] || []).forEach(id => {
      const el = $(id); if (!el) return;
      const name = PAGE + "." + id;
      let t = null;
      const save = () => { clearTimeout(t); set(name, valueOf(el)); };
      el.addEventListener("change", save);
      if (!(el.tagName === "SELECT" || isCheck(el))) el.addEventListener("input", () => { clearTimeout(t); t = setTimeout(save, 600); });
    });
  }

  apply(false);                       // runs before the page script, so it reads the restored values
  bind();
  window.addEventListener("oi-sync-pulled", ev => {
    const ch = (ev.detail && ev.detail.changed) || [];
    if (ch.includes(KEY)) { apply(true); listeners.forEach(fn => { try { fn("*", null); } catch { } }); }
    if (ch.some(k => k !== KEY)) applyLegacy();
    // Risk guards and starting capital are only read when the page opens, so reload to show the other device's values
    // (never while you are typing).
    const loadOnly = ["option-intelligence-paper-guard-v1", "option-intelligence-paper-capital-v1"];
    const a = document.activeElement;
    if (ch.some(k => loadOnly.includes(k)) && !(a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName))) location.reload();
  });

  window.OIPrefs = { get, set, onChange: fn => listeners.push(fn), KEY };
  try { window.dispatchEvent(new Event("oi-prefs-ready")); } catch { }
})();
