// v1.20.29: open / close for large tables. Every table with more than 5 rows gets a small "Hide table · N rows" / "Show table · N rows"
// button above it; the choice is remembered per table on this device. Tables filled later (by refreshes or Diagnostics runs) are
// picked up automatically. Nothing else on the page moves.
(function () {
  "use strict";
  const MIN_ROWS = 6, KEY = "oi-tbl-";
  const store = {
    get(k) { try { return localStorage.getItem(KEY + k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(KEY + k, v); } catch { /* private mode */ } },
  };
  const rowCount = t => {
    const b = t.tBodies && t.tBodies[0];
    const rows = b ? Array.from(b.rows) : Array.from(t.rows).slice(1);
    return rows.filter(r => !(r.cells.length === 1 && r.cells[0].classList.contains("empty"))).length;
  };
  const pageKey = location.pathname.replace(/[^a-z0-9]+/gi, "-");
  let auto = 0;

  function setup(wrap) {
    if (wrap.dataset.oiTbl) return;
    const t = wrap.querySelector("table");
    if (!t) return;
    const body = t.tBodies && t.tBodies[0];
    const id = pageKey + ":" + (t.id || (body && body.id) || wrap.id || "t" + (auto++));
    wrap.dataset.oiTbl = id;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "secondary oi-tbl-toggle";
    btn.style.cssText = "display:none;font-size:12px;padding:4px 10px;margin:6px 0;font-weight:600";
    wrap.parentNode.insertBefore(btn, wrap);
    let open = store.get(id) !== "closed";
    const render = () => {
      const n = rowCount(t);
      btn.style.display = n >= MIN_ROWS ? "" : "none";
      const shown = open || n < MIN_ROWS;
      wrap.style.display = shown ? "" : "none";
      btn.textContent = (open ? "▾ Hide table" : "▸ Show table") + " · " + n + " rows";
      btn.setAttribute("aria-expanded", String(open));
    };
    btn.addEventListener("click", () => { open = !open; store.set(id, open ? "open" : "closed"); render(); });
    new MutationObserver(render).observe(t, { childList: true, subtree: true });
    render();
  }

  function scan(root) { (root.querySelectorAll ? root.querySelectorAll(".table-wrap") : []).forEach(setup); }
  function start() {
    scan(document);
    new MutationObserver(ms => {
      for (const m of ms) for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.classList && n.classList.contains("table-wrap")) setup(n); else scan(n);
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
