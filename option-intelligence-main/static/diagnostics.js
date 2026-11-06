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
  // v1.20.38: indicator values (CE / PE) - test here, apply to live (owner)
  const TK = ["rsi_ce_min", "rsi_ce_max", "rsi_pe_min", "rsi_pe_max", "adx_min", "chase_atr", "rsi_slope"];
  let tuneDefaults = null;
  function tuneInputs() {
    if (!$("t_rsi_ce_min") || !tuneDefaults) return null;
    const o = {};
    for (const k of TK) { const el = $("t_" + k); if (!el || el.value === "") continue; o[k] = k === "rsi_slope" ? el.value === "true" : Number(el.value); }
    return o;
  }
  function tuneFill(vals, d) {
    for (const k of TK) { const el = $("t_" + k); if (!el) continue; el.value = String(vals[k]);
      const lab = el.closest("label"); if (lab) lab.title = (lab.title.split(" · tested")[0]) + ` · tested default ${d[k]}`; }
  }
  function tuneStateText(ch) {
    const n = Object.keys(ch || {}).length;
    $("tuneState").textContent = n ? `Live signals now use changed values: ${Object.entries(ch).map(([k, v]) => k + " " + v).join(", ")}.` : "Live signals use the tested defaults.";
  }
  (async () => {
    try { const d = await api("/api/indicator-tuning"); tuneDefaults = d.defaults; tuneFill(d.live, d.defaults); tuneStateText(d.changes);
      if (Object.keys(d.changes || {}).length && $("tuneBox")) $("tuneBox").open = true; } catch {}
  })();
  async function tuneSave(body, what) {
    try { const d = await api("/api/indicator-tuning", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      tuneFill(d.live, tuneDefaults); tuneStateText(d.changes); fail(""); $("tuneState").textContent += " " + what;
    } catch (e) { fail("Indicator values: " + e.message); }
  }
  if ($("tuneApply")) $("tuneApply").addEventListener("click", () => {
    if (confirm("Apply these indicator values to LIVE signals? Test them here first - untested values can add losing trades.")) tuneSave({ values: tuneInputs() || {} }, "(applied)");
  });
  if ($("tuneReset")) $("tuneReset").addEventListener("click", () => tuneSave({ reset: true }, "(reset)"));
  function fail(msg) { const b = $("errorBox"); b.textContent = msg; b.hidden = !msg; }

  $("runBtn").addEventListener("click", async () => {
    fail(""); $("results").hidden = true;
    const p = new URLSearchParams({ symbol: $("symbol").value, interval: $("interval").value, days: $("days").value, mode: $("mode").value,
      target: $("target").value, stop: $("stop").value, cost: $("cost").value, horizon: $("horizon").value, score_min: $("scoreMin") ? $("scoreMin").value : 60,
      pattern_minutes: $("patternMin") ? $("patternMin").value : 90 });
    const tv = tuneInputs(); if (tv) p.set("tune", JSON.stringify(tv));
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
  $("dlBtn").addEventListener("click", () => {          // v1.20.8: works in the Android app too (savefile.js)
    if (!lastReport) return;
    const name = `diagnostics-${lastReport.params.symbol}-${lastReport.params.mode}-${(lastReport.params.days || []).slice(-1)[0] || "report"}.json`;
    const text = JSON.stringify(lastReport, null, 1);
    if (window.oiSave) window.oiSave(name, text, "application/json");
    else { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "application/json" })); a.download = name; document.body.appendChild(a); a.click(); a.remove(); }
  });
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
    const wf = rep.winrate_finder;
    if (wf && $("wfTable")) {
      const rows = (wf.cells && wf.cells.length) ? wf.cells : (wf.near || []);
      $("wfNote").textContent = !wf.split ? (wf.note || "Too few signals.") : (wf.cells && wf.cells.length
        ? `Halves: ${wf.split.first} (${wf.split.n1} signals) and ${wf.split.second} (${wf.split.n2}). These exits passed in both.`
        : `Halves: ${wf.split.first} and ${wf.split.second}. NO exit passed in both halves — closest exits with a positive average shown.`);
      const u = x => x.kind === "ATR" ? " ATR" : " pts";
      table($("wfTable"), ["Exit (target / stop)", "All: win · avg", "First half", "Second half"], rows.map(x => [`${x.target}${u(x)} / ${x.stop}${u(x)}`,
        `${x.all.win_rate}% · ${num(x.all.avg)}`, `${x.first.win_rate}% · ${num(x.first.avg)}`, `${x.second.win_rate}% · ${num(x.second.avg)}`]));
    }
    const ob = rep.orb;
    if (ob && $("orbTable")) {
      const sc = s => s && s.n ? `${s.n} · ${s.win_rate}% · ${num(s.avg)}` : "—";
      if (ob.error) table($("orbTable"), ["ORB"], [["Could not run: " + ob.error]]);
      else {
        table($("orbTable"), ["Strategy", "Trailing exit (live)", "1st half", "2nd half", "Fixed 1.5/1.5 ATR", `Points (${P.target}/${P.stop})`, "Total (trailing)", "Verdict"],
          [["Current signals", sc(ob.baseline), "", "", "", "", num(ob.baseline.total, 0), ""]].concat(ob.variants.map(v => [v.label, sc(v.trail), sc(v.first), sc(v.second), sc(v.fixed_atr), sc(v.points),
            num(v.trail.total, 0), { text: v.verdict, cls: v.verdict.startsWith("works") ? "green" : v.verdict.startsWith("loses") ? "red" : "" }])));
        const best = ob.variants.filter(v => v.trail.n).sort((a, b) => (b.trail.total || 0) - (a.trail.total || 0))[0];
        table($("orbTrades"), [`Trades - ${best ? best.label : "none"}`, "Side", "Range (ATR)", "Result (pts, trailing)"],
          best && best.trades.length ? best.trades.map(x => [`${x.day} ${x.time}`, x.side, x.or_width_atr, { text: num(x.pts), cls: x.pts > 0 ? "green" : "red" }]) : [["None", "", "", ""]]);
      }
    }
    const ru = rep.rupees;
    if (ru && $("rsTable")) {
      const rs = v => (v < 0 ? "−₹" : "₹") + Math.abs(Math.round(v)).toLocaleString("en-IN");
      if (ru.error) table($("rsTable"), ["Rupees per day"], [["Could not calculate: " + ru.error]]);
      else {
        $("rsNote").textContent = `${ru.trades} trades · exit plan ${ru.plan} · lot ${ru.lot}${(ru.assumed || []).includes("lot") ? " (assumed)" : ""} · delta ${ru.delta}${(ru.assumed || []).includes("delta") ? " (assumed)" : ""} · ₹${ru.charges_per_lot} charges per lot. Index points × delta × quantity − charges; days without a signal count as ₹0. An estimate: no slippage, delta kept constant, every signal taken.`;
        table($("rsTable"), ["Lots (qty)", "Average per day", "Best day", "Worst day", "Biggest fall from a high", `Days ≥ ₹${Number(ru.goal).toLocaleString("en-IN")}`, "Losing days", "Average per trade", `Total (${ru.rows.length ? ru.rows[0].days : 0} days)`],
          ru.rows.map(x => [`${x.lots} (${x.qty})`, { text: rs(x.avg_day), cls: x.avg_day >= 0 ? "green" : "red" }, rs(x.best_day), { text: rs(x.worst_day), cls: x.worst_day < 0 ? "red" : "" },
            { text: rs(x.max_drawdown), cls: x.max_drawdown < 0 ? "red" : "" }, `${x.goal_days} of ${x.days}`, `${x.losing_days} of ${x.trade_days} trading days`, rs(x.avg_trade_rs), rs(x.total)]));
      }
    }
    const stw = rep.strong_trend;
    if (stw && $("stTable")) {
      const sc = s => s && s.n ? `${s.n} · ${s.win_rate}% · ${num(s.avg)}` : "—";
      if (stw.error) table($("stTable"), ["Strong trend"], [["Could not run: " + stw.error]]);
      else {
        table($("stTable"), ["Rule", "Extra trades (trailing)", "1st half", "2nd half", "Fixed 1.5/1.5 ATR", "Signals + extra", "Verdict"],
          [["Current rules (RSI 68 / 32)", sc(stw.baseline), "", "", "", sc(stw.baseline), ""]].concat(stw.variants.map(v => [v.label, sc(v.trail), sc(v.first), sc(v.second), sc(v.fixed_atr), sc(v.combined),
            { text: v.verdict, cls: v.verdict.startsWith("works") ? "green" : v.verdict.startsWith("loses") ? "red" : "" }])));
        const tr = (stw.variants[1] || stw.variants[0] || {}).trades || [];
        table($("stTrades"), ["Extra trade (continuation version)", "Side", "RSI", "Trend (efficiency · move)", "Result (pts, trailing)"],
          tr.length ? tr.map(x => [`${x.day} ${x.time}`, x.side, x.rsi, `${x.eff} · ${x.move_atr} ATR`, { text: num(x.pts), cls: x.pts > 0 ? "green" : "red" }]) : [["No extra trades in this period", "", "", "", ""]]);
      }
    }
    const ld = rep.leading;
    if (ld && $("leadTable")) {
      const sc = s => s && s.n ? `${s.n} · ${s.win_rate}% · ${num(s.avg)}` : "—";
      if (ld.error) table($("leadTable"), ["Leading triggers"], [["Could not run: " + ld.error]]);
      else table($("leadTable"), ["Trigger", "Variant", "All entries", "Extra trades", "Extra 1st half", "Extra 2nd half", "Total with extra", "Verdict"],
        [["Current rules", "", sc(ld.baseline), "", "", "", num(ld.baseline_total, 0), ""]].concat(ld.kinds.map(k => [k.label, k.variant, sc(k.entries), sc(k.extra),
          sc(k.extra_first), sc(k.extra_second), num(k.combined_total, 0), { text: k.verdict, cls: k.verdict.startsWith("adds") ? "green" : k.verdict.startsWith("loses") ? "red" : "" }])));
    }
    const srv = rep.sr_reversal;
    if (srv && $("srTable")) {
      const sc = s => s && s.n ? `${s.n} · ${s.win_rate}% · ${num(s.avg)}` : "—";
      if (srv.error) table($("srTable"), ["S/R reversal"], [["Could not run: " + srv.error]]);
      else {
        table($("srTable"), ["Entry rule", "Trailing exit (live)", "1st half", "2nd half", "Fixed 1.5/1.5 ATR", `Points (${P.target}/${P.stop})`, "Verdict"],
          [["Current signals", sc(srv.baseline_trail), "", "", "", "", ""]].concat(srv.variants.map(v => [v.label, sc(v.trail), sc(v.first), sc(v.second), sc(v.fixed_atr), sc(v.points),
            { text: v.verdict, cls: v.verdict.startsWith("beats") ? "green" : v.verdict.startsWith("loses") ? "red" : "" }])));
        const tr = (srv.variants[0] || {}).trades || [];
        table($("srTrades"), ["Reversal trade (with trend)", "Side", "Level", "Strength", "Result (pts, trailing)"],
          tr.length ? tr.map(x => [`${x.day} ${x.time}`, x.side, x.level, x.strength, { text: num(x.pts), cls: x.pts > 0 ? "green" : "red" }]) : [["None in this period", "", "", "", ""]]);
      }
    }
    const ws = rep.window_slots;
    if (ws && $("wsTable")) {
      const sc = s => s && s.n ? `${s.n} · ${s.win_rate}% · ${num(s.avg)}` : "—";
      if (ws.error) table($("wsTable"), ["Window slots"], [["Could not run: " + ws.error]]);
      else table($("wsTable"), ["Time slot", `Points exit (${P.target}/${P.stop})`, "Fixed 1.5/1.5 ATR", "Trailing exit (live)", "Trailing 1st half", "Trailing 2nd half", "Verdict"],
        ws.map(w => [w.slot, sc(w.points), sc(w.fixed_atr), sc(w.trail), sc(w.trail_first), sc(w.trail_second),
          { text: w.verdict, cls: w.verdict.startsWith("worth") ? "green" : w.verdict.startsWith("keep") ? "red" : "" }]));
    }
    const td = rep.trend_day;
    if (td && $("tdTable")) {
      if (td.error) { table($("tdTable"), ["What-if"], [["Could not run: " + td.error]]); }
      else {
        const sc = s => s && s.n ? `${s.n} · ${s.win_rate}% · ${num(s.avg)}` : "—";
        table($("tdTable"), ["Rule (1.5 / 1.5 ATR exit)", "Extra trades · win · avg pts", "First half", "Second half", "Signals + extra", "Verdict"],
          [["Current rules (confirmed signals only)", sc(td.baseline), "", "", sc(td.baseline), ""]].concat(td.variants.map(v =>
            [v.label, sc(v.extra), sc(v.first), sc(v.second), sc(v.combined), { text: v.verdict, cls: v.verdict.startsWith("promising") ? "green" : v.verdict.startsWith("loses") ? "red" : "" }])));
        const tr = (td.variants[0] || {}).trades || [];
        table($("tdTrades"), ["Extra trade (trend day rule)", "Side", "Result (pts)", "Proven setups before it"], tr.length ? tr.map(x => [`${x.day} ${x.time}`, x.side, { text: num(x.pts), cls: x.pts > 0 ? "green" : "red" }, x.proofs]) : [["No extra trades in this period", "", "", ""]]);
      }
    }
    const mm = rep.missed_moves;
    if (mm && $("missedBlockers")) {
      $("mmRule").textContent = mm.moves ? `${mm.moves} big moves · caught ${mm.caught} · missed ${mm.missed}. Rule: ${mm.rule}.` : "No clean big moves in this period.";
      table($("missedBlockers"), ["Gate failing at the start of a missed move", "Missed moves", "% of missed"], mm.blockers.length ? mm.blockers.map(b => [b.gate, b.moves, b.pct + "%"]) : [["—", "", ""]]);
      table($("missedExamples"), ["Missed move", "Side", "Points", "ATR", "Blocked by"], (mm.examples || []).map(x => [`${x.day} ${x.time}`, x.side, x.points, x.atr_move + "×", x.blocked_by.join(" · ")]));
    }
    const l = clear($("limits")); rep.limitations.forEach(t => l.appendChild(el("li", "dg-info", t)));
    $("results").scrollIntoView({ behavior: "smooth" });
  }
})();

// v1.20.12: a sensible "hold up to" per timeframe (about 1 hour on 5-minute candles, 2 hours on 15-minute candles) unless you typed one
(function () {
  const iv = document.getElementById("interval"), hz = document.getElementById("horizon");
  if (!iv || !hz) return;
  const H = { "1": 48, "3": 20, "5": 12, "15": 8 };
  let touched = false;
  hz.addEventListener("input", () => { touched = true; });
  const apply = () => { if (!touched && H[iv.value]) hz.value = H[iv.value]; };
  iv.addEventListener("change", apply); apply();
})();

// v1.20.18: start with the live pattern window
fetch("/api/signals/config", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null).then(c => {
  if (c && c.pattern_minutes && document.getElementById("patternMin")) document.getElementById("patternMin").value = String(c.pattern_minutes);
}).catch(() => {});
