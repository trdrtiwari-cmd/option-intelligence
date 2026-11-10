// v1.20.39: page index - a small "Sections" button that lists the page's sections (every panel title) and jumps to the one you tap.
// Built fresh each time it opens, so panels that appear later (Diagnostics results) are included. Nothing else on the page moves.
(function () {
  "use strict";
  if (/^\/login/.test(location.pathname)) return;
  const css = document.createElement("style");
  css.textContent =
    ".oi-sec-btn{position:fixed;right:12px;bottom:calc(var(--nav-h,0px) + var(--sab,0px) + 14px);z-index:950;border-radius:999px;padding:8px 12px;" +
    "font-size:12px;font-weight:700;box-shadow:0 4px 14px rgba(0,0,0,.35)}" +
    ".oi-sec-list{position:fixed;right:12px;bottom:calc(var(--nav-h,0px) + var(--sab,0px) + 56px);z-index:951;max-height:60vh;overflow:auto;" +
    "min-width:220px;max-width:min(320px,calc(100vw - 24px));background:var(--panel,#121d2d);border:1px solid var(--line,#2a3a50);border-radius:12px;" +
    "padding:6px;box-shadow:0 8px 28px rgba(0,0,0,.45)}" +
    ".oi-sec-list button{display:block;width:100%;text-align:left;background:none;border:0;border-radius:8px;padding:8px 10px;color:var(--text,#e7eef8);" +
    "font-size:13px;font-weight:600;cursor:pointer}.oi-sec-list button:hover{background:var(--panel2,#18263a)}" +
    ".oi-sec-list small{display:block;color:var(--muted,#8aa0b8);font-weight:500;font-size:11px}" +
    "html.m-framed .oi-sec-btn,html.m-framed .oi-sec-list{display:none}";
  document.head.appendChild(css);

  function sections() {
    const seen = new Set(), out = [];
    document.querySelectorAll("h2").forEach(h => {
      const box = h.closest("section, .panel") || h;
      if (seen.has(box) || box.closest("[hidden]") || box.offsetParent === null) return;
      seen.add(box);
      const t = (h.textContent || "").trim();
      if (t) out.push({ el: box, title: t.length > 60 ? t.slice(0, 58) + "…" : t });
    });
    return out;
  }
  function start() {
    if (document.querySelectorAll("h2").length < 2) return;
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "oi-sec-btn"; btn.textContent = "☰ Sections"; btn.setAttribute("aria-expanded", "false");
    const list = document.createElement("div"); list.className = "oi-sec-list"; list.hidden = true; list.setAttribute("role", "menu");
    const close = () => { list.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.onclick = () => {
      if (!list.hidden) return close();
      const items = sections();
      list.innerHTML = "";
      const top = document.createElement("button"); top.type = "button"; top.innerHTML = "↑ Top of the page";
      top.onclick = () => { window.scrollTo({ top: 0, behavior: "smooth" }); close(); };
      list.appendChild(top);
      items.forEach((it, i) => {
        const b = document.createElement("button"); b.type = "button"; b.setAttribute("role", "menuitem");
        b.innerHTML = `${i + 1}. ${it.title.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]))}`;
        b.onclick = () => {
          const y = it.el.getBoundingClientRect().top + window.scrollY - 8;
          window.scrollTo({ top: Math.max(0, y), behavior: "smooth" }); close();
        };
        list.appendChild(b);
      });
      list.hidden = false; btn.setAttribute("aria-expanded", "true");
    };
    document.addEventListener("click", e => { if (!list.hidden && !list.contains(e.target) && e.target !== btn) close(); });
    document.body.append(list, btn);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
