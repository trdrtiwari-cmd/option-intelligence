// v1.20.61: automatic self-checks on the main terminal. Once a minute the page compares what it SHOWS with the analysis it received and
// with itself; anything that does not add up is logged as a warning ("Display mismatch: ...") through the problem reporter, so it
// appears in Download for Claude without anyone pressing Report. Nothing on the page changes.
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const rep = (msg, detail) => { try { window.__oiIssues && window.__oiIssues.report("warning", "Display mismatch: " + msg, detail); } catch { } };
  const t = el => (el && el.textContent || "").replace(/\s+/g, " ").trim();
  const now = () => new Date().toLocaleTimeString("en-IN", {hour12: false});
  const strikes = {};
  const optSig = {sig: "", same: 0};                       // a mismatch must be seen on 2 checks in a row (a refresh in progress is not a problem)
  function flag(key, bad, msg, detail) {
    if (!bad) { strikes[key] = 0; return; }
    strikes[key] = (strikes[key] || 0) + 1;
    if (strikes[key] === 2) rep(msg, `${now()} · ${detail}`);
  }

  function check() {
    if (document.visibilityState !== "visible") return;
    let a = null, open = false, at = 0;
    try { a = lastAnalysis; open = marketOpen; at = lastAnalysisAt; } catch { return; }
    if (!a || a.status !== "ok") return;
    const lbl = String(a.regime_label || ""), atr = Number(a.atr) || 0, px = Number(a.last_close), vwap = Number(a.vwap_approx);
    const fin = a.final || {}, scoreMin = Number(($("scoreMin") && $("scoreMin").value) || 60);
    const dom = Math.max(a.bull_score || 0, a.bear_score || 0);
    const view = ($("chartView") && $("chartView").value) || "index";

    // 1. trend label against price vs VWAP
    if (atr && px && vwap) {
      flag("upVwap", lbl === "UPTREND" && px < vwap - 0.5 * atr, "UPTREND shown while price is well below VWAP",
           `close ${px.toFixed(1)} vs VWAP ${vwap.toFixed(1)} (${((px - vwap) / atr).toFixed(2)} ATR) · score ${dom}`);
      flag("dnVwap", lbl === "DOWNTREND" && px > vwap + 0.5 * atr, "DOWNTREND shown while price is well above VWAP",
           `close ${px.toFixed(1)} vs VWAP ${vwap.toFixed(1)} (${((px - vwap) / atr).toFixed(2)} ATR) · score ${dom}`);
    }
    // 2. the Market regime card shows a firm trend word the analysis does not support (index view only)
    const shown = t($("regime"));
    if (view === "index" && shown && shown !== "—") {
      const firm = /^(UPTREND|DOWNTREND|BULLISH|BEARISH)$/.test(shown);
      flag("regimeCard", firm && shown !== lbl, "Market regime card differs from the analysis", `card "${shown}" · analysis "${lbl}" · score ${dom}`);
      flag("weakFirm", firm && dom < 50, "Firm trend label with a weak score", `card "${shown}" · score ${dom}/100 · ${String(a.regime_note || "").slice(0, 120)}`);
    }
    // 3. a BUY / confirmed signal that should not be possible
    const lvl = String(fin.level || ""), act = String(fin.action || "");
    flag("buyScore", lvl === "CONFIRMED" && dom < scoreMin && a.derivatives?.mode !== "score", "Confirmed signal below the minimum score",
         `${act} · score ${dom} < ${scoreMin}`);
    flag("buyClosed", /^BUY/.test(act) && !open, "BUY shown while the market is closed", `${act} · ${fin.headline ? String(fin.headline).slice(0, 120) : ""}`);
    const dec = t($("decision"));
    flag("decBuy", /^BUY/.test(dec) && lvl !== "CONFIRMED", "Decision card says BUY but the signal is not confirmed", `card "${dec}" · level ${lvl || "—"}`);
    // 4. the analysis stopped refreshing while the market is open
    const ageMin = at ? (Date.now() - at) / 60000 : null;
    flag("stale", open && ageMin !== null && ageMin > 3, "Signal analysis not refreshing during market hours",
         `last analysis ${ageMin === null ? "never" : ageMin.toFixed(1) + " min ago"} · bar ${a.last_closed_bar || "—"}`);
    // 5. the Derivatives filters switch and the mode the server used disagree
    const sel = $("derivMode"), used = a.derivatives ? a.derivatives.mode : (sel && sel.value === "off" ? "off" : null);
    flag("derivMode", !!(sel && used && sel.value !== used), "Derivatives filters switch differs from the mode in use",
         `switch "${sel && sel.value}" · analysis used "${used}"`);
    // 7. (v1.20.64) the selected option's price is not live / not updating during market hours
    let po = null;
    try { po = paperSelected; } catch { }
    if (po && po.instrument_key) {
      const live = po.quoteStatus === "LIVE_TICK";
      flag("optNotLive", open && po.quoteStatus && !live, "Selected option price is not a live tick",
           `${po.symbol || ""} ${po.strike || ""} ${po.type || ""} · quote ${po.quoteStatus} · LTP ${po.ltp}`);
      const sig = `${po.instrument_key}|${po.ltp}|${po.quoteTimestamp || ""}`;
      if (open && sig === optSig.sig) optSig.same += 1; else { optSig.sig = sig; optSig.same = 0; }
      flag("optStuck", open && optSig.same >= 3, "Selected option price not updating",
           `${po.symbol || ""} ${po.strike || ""} ${po.type || ""} · LTP ${po.ltp} unchanged for ${optSig.same} min · quote ${po.quoteStatus || "—"}`);
      // 8. (v1.20.64) paper entry price far from the option's last price (auto-filled, not typed by the owner)
      const ent = $("paperEntry"), ev = ent ? Number(ent.value) : 0, ltp = Number(po.ltp);
      const typed = ent && ent.dataset && ent.dataset.userEdited;
      flag("entryVsLtp", !!(ev > 0 && ltp > 0 && !typed && Math.abs(ev - ltp) / ltp > 0.03), "Paper entry price differs from the option's last price",
           `entry ${ev.toFixed(2)} vs LTP ${ltp.toFixed(2)} (${(((ev - ltp) / ltp) * 100).toFixed(1)}%) · quote ${po.quoteStatus || "—"}`);
    }
    // 6. score card vs analysis (index view)
    const sc = t($("score")).match(/^(\d+)\/100/);
    flag("scoreCard", view === "index" && sc && Math.abs(Number(sc[1]) - Number(a.score)) > 0 && Date.now() - at > 15000,
         "Directional score card differs from the analysis", `card ${sc && sc[1]} · analysis ${a.score}`);
  }

  setInterval(check, 60000);
  setTimeout(check, 20000);
})();
