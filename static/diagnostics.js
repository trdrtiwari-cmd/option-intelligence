(function () {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const clear = n => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
  const num = (v, d = 1) => v === null || v === undefined || !Number.isFinite(Number(v)) ? "—" : (Number(v) > 0 ? "+" : "") + Number(v).toFixed(d);
  let timer = null;

  async function api(url, opts) {
    const res = await fetch(url, opts);
    let data = {}; try { data = await res.json(); } catch { }
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Request failed (" + res.status + ")");
    return data;
  }
  function fail(msg) { const b = $("errorBox"); b.textContent = msg; b.hidden = !msg; }

  $("runBtn").addEventListener("click", async () => {
    fail(""); $("results").hidden = true;
    const p = new URLSearchParams({ symbol: $("symbol").value, interval: $("interval").value, days: $("days").value, mode: $("mode").value,
      target: $("target").value, stop: $("stop").value, cost: $("cost").value, horizon: $("horizon").value });
    $("runBtn").disabled = true; $("progressBox").hidden = false; $("progressBar").style.width = "2%"; $("progressText").textContent = "Fetching candles…";
    try {
      const { job_id } = await api("/api/diagnostics/start?" + p, { method: "POST" });
      poll(job_id);
    } catch (e) { done(); fail(e.message); }
  });
  function done() { clearInterval(timer); $("runBtn").disabled = false; $("progressBox").hidden = true; }
  function poll(job) {
    clearInterval(timer);
    timer = setInterval(async () => {
      try {
        const r = await api("/api/diagnostics/result?job_id=" + encodeURIComponent(job));
        $("progressBar").style.width = Math.max(3, Math.round(r.progress * 100)) + "%"; $("progressText").textContent = `${r.stage} · ${Math.round(r.progress * 100)}%`;
        if (r.status === "done") { done(); render(r.report); }
        else if (r.status === "error") { done(); fail(r.error || "The analysis failed."); }
      } catch (e) { done(); fail(e.message); }
    }, 1200);
  }

  function heat(v, n) {
    if (v === null || v === undefined || !n) return "background:#101b2a;color:#91a4ba";
    const a = Math.min(Math.abs(v) / 12, 1) * 0.65 + 0.08, dim = n < 8 ? ";opacity:.45" : "";
    return (v >= 0 ? `background:rgba(37,197,138,${a})` : `background:rgba(255,102,122,${a})`) + ";color:#fff" + dim;
  }
  function table(host, cols, rows) {
    clear(host); const thead = el("thead"), hr = el("tr"); cols.forEach(c => hr.appendChild(el("th", "", c))); thead.appendChild(hr); host.appendChild(thead);
    const tb = el("tbody"); rows.forEach(r => { const tr = el("tr"); r.forEach((v, i) => { const td = el("td", typeof v === "object" && v ? v.cls : "", typeof v === "object" && v ? v.text : String(v)); if (i === 0) td.style.textAlign = "left"; tr.appendChild(td); }); tb.appendChild(tr); }); host.appendChild(tb);
  }
  const statCells = s => s.n ? [s.n, s.win_rate + "%", { text: num(s.avg), cls: s.avg > 0 ? "green" : "red" }, num(s.total, 0), s.pf == null ? "—" : s.pf, s.se == null ? "—" : "±" + s.se] : [0, "—", "—", "—", "—", "—"];

  let lastReport = null;
  $("dlBtn").addEventListener("click", () => { if (!lastReport) return; const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(lastReport, null, 2)], { type: "application/json" })); a.download = `diagnostics-${lastReport.params.symbol}-${lastReport.params.mode}.json`; document.body.appendChild(a); a.click(); a.remove(); });
  function render(rep) {
    lastReport = rep;
    const P = rep.params;
    $("results").hidden = false;
    const v = clear($("verdict"));
    rep.verdict.forEach(x => { const li = el("li", "dg-" + x.level, x.text); const tag = { action: "CORRECTION", good: "WORKS", bad: "PROBLEM", info: "NOTE" }[x.level] || "NOTE"; const b = el("b", "", tag + " · "); li.prepend(b); v.appendChild(li); });
    const c = rep.confirmed, h = clear($("headline"));
    [["Days replayed", P.days.length], ["Bars evaluated", P.bars_evaluated], ["Confirmed signals", c.count], ["Signals per day", P.days.length ? (c.count / P.days.length).toFixed(1) : "—"],
      ["Win rate", c.stats.n ? c.stats.win_rate + "%" : "—"], [`Avg per trade (${P.target}/${P.stop})`, c.stats.n ? num(c.stats.avg) + " pts" : "—"],
      ["Random entries, same exit", rep.random_entries && rep.random_entries.n ? num(rep.random_entries.avg) + " pts" : "—"], ["Typical best / worst move", c.median_mfe == null ? "—" : `+${c.median_mfe} / −${c.median_mae} pts`]]
      .forEach(([k, val]) => { const d = el("div"); d.appendChild(el("span", "", k)); d.appendChild(el("strong", "", String(val))); h.appendChild(d); });

    const g = clear($("gridTable"));
    if (rep.grid) {
      const hr = el("tr"); hr.appendChild(el("th", "", "Target ↓ / Stop →")); rep.grid[0].forEach(cell => hr.appendChild(el("th", "", cell.stop + " pts"))); g.appendChild(hr);
      const best = rep.best_exit;
      rep.grid.forEach(row => { const tr = el("tr"); tr.appendChild(el("th", "", row[0].target + " pts"));
        row.forEach(cell => { const td = el("td", "dg-cell", cell.n ? `${num(cell.avg)}` : "—"); td.setAttribute("style", heat(cell.avg, cell.n)); td.title = cell.n ? `n=${cell.n} · win ${cell.win_rate}% · PF ${cell.pf ?? "—"}` : "no trades";
          if (best && best.target === cell.target && best.stop === cell.stop) td.classList.add("dg-best"); if (cell.target === P.target && cell.stop === P.stop) td.classList.add("dg-current"); tr.appendChild(td); }); g.appendChild(tr); });
    } else g.appendChild(el("tr")).appendChild(el("td", "", "No confirmed signals in this period."));

    table($("strategies"), ["Exit style", "Trades", "Win", "Avg pts", "Total", "PF", "±SE"], (rep.strategies || []).map(x => [x.name, ...statCells(x)]));
    table($("costTable"), ["Cost per trade (pts)", "Trades", "Win", "Avg pts", "Total", "PF", "±SE"], (rep.cost_table || []).map(x => [x.cost + " pts" + (x.cost === P.cost ? "  (used)" : ""), ...statCells(x)]));
    const gr = clear($("groups"));
    Object.entries(rep.groups).forEach(([name, rows]) => {
      if (!rows.length) return;
      const box = el("div", "dg-group"); box.appendChild(el("h3", "", name)); const t = el("table"); table(t, ["Bucket", "Trades", "Win", "Avg pts", "Total", "PF", "±SE"], rows.map(r => [r.label, ...statCells(r)])); const w = el("div", "table-wrap"); w.appendChild(t); box.appendChild(w); gr.appendChild(box);
    });
    table($("ablation"), ["Only this gate failed", "Setups", "Win", "Avg pts", "Total", "PF", "±SE"], rep.ablation.length ? rep.ablation.map(a => [a.gate, ...statCells(a)]) : [["No one-gate near-misses in this period", "", "", "", "", "", ""]]);
    const l = clear($("limits")); rep.limitations.forEach(t => l.appendChild(el("li", "dg-info", t)));
    $("results").scrollIntoView({ behavior: "smooth" });
  }
})();
