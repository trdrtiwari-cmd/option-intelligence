/* Option Intelligence - chart window (v1.20.43).
   A full-size candle chart with the important numbers beside it, more indicators (EMA x3, SMA, VWAP, Bollinger Bands,
   Supertrend, volume, previous-day levels, RSI and MACD panes) and settings for each (length, multiplier, colour). Live
   through the same WebSocket as the terminal. Layout (data beside / below / strip / hidden, pane height, panel size) is
   saved separately for phones and computers. Display only: BUY signals still use the tested engine values. */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const LS_KEY = "oi-chart-v1";
  const isPhone = () => window.matchMedia("(max-width:820px)").matches;
  const DEF = {
    sym: "NIFTY", tf: 5, days: 5,
    layout: { desktop: "side", mobile: "below" }, paneH: { desktop: "0.22", mobile: "0.16" }, dataSize: { desktop: "m", mobile: "m" },
    candle: "candle", up: "#25c58a", down: "#ff667a",
    ind: {
      ema1: { on: true, len: 9, color: "#25c58a", width: 2 },
      ema2: { on: true, len: 21, color: "#ff4d5e", width: 2 },
      ema3: { on: false, len: 50, color: "#f3c969", width: 2 },
      sma: { on: false, len: 20, color: "#b48cff", width: 1 },
      vwap: { on: true, color: "#ffffff", width: 2 },
      bb: { on: false, len: 20, mult: 2, color: "#5aa7ff" },
      st: { on: false, len: 10, mult: 3, up: "#25c58a", down: "#ff667a" },
      vol: { on: true, up: "#25c58a", down: "#ff667a" },
      lvl: { on: true, color: "#f3c969" },
      rsi: { on: true, len: 14, color: "#c792ea", ob: 70, os: 30 },
      macd: { on: false, fast: 12, slow: 26, sig: 9, line: "#5aa7ff", signal: "#ff9f43", up: "#25c58a", down: "#ff667a" },
    },
  };
  const META = {   // settings rows: [key, name, note, params]
    main: [
      ["ema1", "EMA 1", "Exponential moving average", [["len", "Length", 1, 400]]],
      ["ema2", "EMA 2", "Exponential moving average", [["len", "Length", 1, 400]]],
      ["ema3", "EMA 3", "Exponential moving average", [["len", "Length", 1, 400]]],
      ["sma", "SMA", "Simple moving average", [["len", "Length", 1, 400]]],
      ["vwap", "VWAP", "Resets each session (futures volume)", []],
      ["bb", "Bollinger Bands", "Band = SMA ± multiplier × st.dev", [["len", "Length", 2, 200], ["mult", "Mult", 0.5, 5, 0.1]]],
      ["st", "Supertrend", "ATR length and multiplier", [["len", "ATR", 2, 100], ["mult", "Mult", 0.5, 10, 0.1]]],
      ["vol", "Volume", "Bars at the bottom of the chart", []],
      ["lvl", "Key levels", "Previous day high / low / close, today's open", []],
    ],
    low: [
      ["rsi", "RSI", "Pane with overbought / oversold lines", [["len", "Length", 2, 100], ["ob", "Upper", 50, 95], ["os", "Lower", 5, 50]]],
      ["macd", "MACD", "Pane: line, signal and histogram", [["fast", "Fast", 2, 100], ["slow", "Slow", 3, 200], ["sig", "Signal", 2, 50]]],
    ],
  };
  const COLORS = { ema1: ["color"], ema2: ["color"], ema3: ["color"], sma: ["color"], vwap: ["color"], bb: ["color"], st: ["up", "down"], vol: ["up", "down"],
    lvl: ["color"], rsi: ["color"], macd: ["line", "signal"] };

  // ------------------------------------------------------------------ settings
  const clone = o => JSON.parse(JSON.stringify(o));
  function merge(base, over) {
    const out = clone(base);
    (function rec(b, o) { for (const k in o) { if (o[k] && typeof o[k] === "object" && !Array.isArray(o[k]) && b[k] && typeof b[k] === "object") rec(b[k], o[k]); else if (k in b) b[k] = o[k]; } })(out, over || {});
    return out;
  }
  let S = (() => { try { return merge(DEF, JSON.parse(localStorage.getItem(LS_KEY) || "{}")); } catch { return clone(DEF); } })();
  const q = new URLSearchParams(location.search);
  if (["NIFTY", "BANKNIFTY"].includes((q.get("symbol") || "").toUpperCase())) S.sym = q.get("symbol").toUpperCase();
  if ([1, 3, 5, 15, 30].includes(Number(q.get("interval")))) S.tf = Number(q.get("interval"));
  const save = () => { try { localStorage.setItem(LS_KEY, JSON.stringify(S)); } catch { } };
  const dev = () => isPhone() ? "mobile" : "desktop";

  function toast(msg) { const el = $("toast"); el.textContent = msg; el.classList.add("show"); setTimeout(() => el.classList.remove("show"), 4000); }
  async function api(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch { } throw new Error(d || ("HTTP " + r.status)); }
    return r.json();
  }
  const fmt = (v, n = 2) => v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: n, maximumFractionDigits: n });
  const sgn = (v, n = 2) => v == null || !Number.isFinite(v) ? "—" : (v > 0 ? "+" : "") + fmt(v, n);
  const col = v => v == null || !Number.isFinite(v) ? "" : v > 0 ? "color:var(--green)" : v < 0 ? "color:var(--red)" : "";
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ time (IST axis, like the terminal)
  const IST = 19800;
  const toT = t => { if (typeof t === "number") return Math.floor(t > 1e12 ? t / 1000 : t); const ms = Date.parse(t); return Number.isFinite(ms) ? Math.floor(ms / 1000) : NaN; };
  const istD = s => new Date((s + IST) * 1000);
  const dayKey = s => Math.floor((s + IST) / 86400);
  const hhmm = s => { const d = istD(s); return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0"); };
  function tickFmt(time, type) {
    const d = istD(time), K = LightweightCharts.TickMarkType;
    if (type === K.Year) return String(d.getUTCFullYear());
    if (type === K.Month || type === K.DayOfMonth) return `${String(d.getUTCDate()).padStart(2, "0")} ${d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })}`;
    return hhmm(time);
  }
  const crossFmt = t => `${String(istD(t).getUTCDate()).padStart(2, "0")} ${istD(t).toLocaleString("en-GB", { month: "short", timeZone: "UTC" })} ${hhmm(t)} IST`;

  // ------------------------------------------------------------------ indicator maths (Wilder RSI / ATR like TradingView)
  function ema(v, n) { const a = 2 / (n + 1), o = []; let e = null; for (let i = 0; i < v.length; i++) { e = e == null ? v[i] : a * v[i] + (1 - a) * e; o.push(i >= n - 1 ? e : null); } return o; }
  function emaRaw(v, n) { const a = 2 / (n + 1), o = []; let e = null; for (const x of v) { if (x == null) { o.push(null); continue; } e = e == null ? x : a * x + (1 - a) * e; o.push(e); } return o; }
  function sma(v, n) { const o = []; let s = 0; for (let i = 0; i < v.length; i++) { s += v[i]; if (i >= n) s -= v[i - n]; o.push(i >= n - 1 ? s / n : null); } return o; }
  function stdev(v, n, m) { const o = []; for (let i = 0; i < v.length; i++) { if (i < n - 1) { o.push(null); continue; } let s = 0; for (let k = i - n + 1; k <= i; k++) s += (v[k] - m[i]) ** 2; o.push(Math.sqrt(s / n)); } return o; }
  function rsi(c, n) {
    const o = Array(c.length).fill(null); if (c.length <= n) return o;
    let g = 0, l = 0; for (let i = 1; i <= n; i++) { const d = c[i] - c[i - 1]; g += Math.max(d, 0); l += Math.max(-d, 0); }
    g /= n; l /= n; o[n] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
    for (let i = n + 1; i < c.length; i++) { const d = c[i] - c[i - 1]; g = (g * (n - 1) + Math.max(d, 0)) / n; l = (l * (n - 1) + Math.max(-d, 0)) / n; o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l); }
    return o;
  }
  function atr(cs, n) {
    const o = Array(cs.length).fill(null); if (cs.length <= n) return o;
    const tr = cs.map((c, i) => i ? Math.max(c.high - c.low, Math.abs(c.high - cs[i - 1].close), Math.abs(c.low - cs[i - 1].close)) : c.high - c.low);
    let a = 0; for (let i = 1; i <= n; i++) a += tr[i]; a /= n; o[n] = a;
    for (let i = n + 1; i < cs.length; i++) { a = (a * (n - 1) + tr[i]) / n; o[i] = a; }
    return o;
  }
  function vwap(cs) {
    let d = null, pv = 0, vol = 0, tp = 0, n = 0;
    return cs.map(c => { const k = dayKey(c.time); if (k !== d) { d = k; pv = vol = tp = n = 0; } const t = (c.high + c.low + c.close) / 3, v = c.volume || 0; pv += t * v; vol += v; tp += t; n++; return vol > 0 ? pv / vol : tp / n; });
  }
  function supertrend(cs, n, m) {
    const a = atr(cs, n), dir = Array(cs.length).fill(null), line = Array(cs.length).fill(null);
    let fu = null, fl = null, d = 1;
    for (let i = 0; i < cs.length; i++) {
      if (a[i] == null) continue;
      const hl2 = (cs[i].high + cs[i].low) / 2, bu = hl2 + m * a[i], bl = hl2 - m * a[i], pc = i ? cs[i - 1].close : cs[i].close;
      fu = fu == null || bu < fu || pc > fu ? bu : fu;
      fl = fl == null || bl > fl || pc < fl ? bl : fl;
      if (d === 1 && cs[i].close < fl) d = -1; else if (d === -1 && cs[i].close > fu) d = 1;
      dir[i] = d; line[i] = d === 1 ? fl : fu;
    }
    return { dir, line };
  }
  function calc(cs) {
    const c = cs.map(x => x.close), I = S.ind, out = {};
    out.ema1 = ema(c, I.ema1.len); out.ema2 = ema(c, I.ema2.len); out.ema3 = ema(c, I.ema3.len); out.sma = sma(c, I.sma.len);
    out.vwap = vwap(cs);
    const mid = sma(c, I.bb.len), sd = stdev(c, I.bb.len, mid);
    out.bb = { mid, up: mid.map((m, i) => m == null ? null : m + I.bb.mult * sd[i]), lo: mid.map((m, i) => m == null ? null : m - I.bb.mult * sd[i]) };
    out.st = supertrend(cs, I.st.len, I.st.mult);
    out.rsi = rsi(c, I.rsi.len);
    out.atr = atr(cs, 14);
    const f = emaRaw(c, I.macd.fast), s = emaRaw(c, I.macd.slow), line = c.map((_, i) => i >= I.macd.slow - 1 ? f[i] - s[i] : null);
    const sig = emaRaw(line, I.macd.sig);
    out.macd = { line, sig: line.map((v, i) => v == null || i < I.macd.slow + I.macd.sig - 2 ? null : sig[i]) };
    out.macd.hist = line.map((v, i) => v == null || out.macd.sig[i] == null ? null : v - out.macd.sig[i]);
    return out;
  }

  // ------------------------------------------------------------------ charts
  const base = () => ({
    layout: { background: { color: "#121d2d" }, textColor: "#91a4ba", fontSize: isPhone() ? 10 : 11 },
    grid: { vertLines: { color: "#1b2a3d" }, horzLines: { color: "#1b2a3d" } },
    rightPriceScale: { borderColor: "#27384d", minimumWidth: 64 }, timeScale: { borderColor: "#27384d", timeVisible: true, secondsVisible: false, tickMarkFormatter: tickFmt, rightOffset: 4 },
    localization: { timeFormatter: crossFmt }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  });
  const mk = id => LightweightCharts.createChart($(id), { ...base(), width: $(id).clientWidth || 300, height: $(id).clientHeight || 200 });
  const C = { main: mk("mainChart"), rsi: mk("rsiChart"), macd: mk("macdChart") };
  let ser = {}, plines = [], cs = [], IND = null, A = null, tick = null, syncing = false;

  // keep the three panes on the same bars
  Object.entries(C).forEach(([k, ch]) => ch.timeScale().subscribeVisibleLogicalRangeChange(r => {
    if (syncing || !r) return; syncing = true;
    Object.entries(C).forEach(([k2, c2]) => { if (k2 !== k) try { c2.timeScale().setVisibleLogicalRange(r); } catch { } });
    syncing = false;
  }));

  function clearSeries() {
    Object.values(ser).forEach(s => { for (const ch of Object.values(C)) { try { ch.removeSeries(s); } catch { } } });
    ser = {}; plines = [];
  }
  function line(chart, color, width = 2, extra = {}) { return chart.addLineSeries({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...extra }); }
  function build() {
    clearSeries();
    const I = S.ind, m = C.main;
    if (S.candle === "bar") ser.price = m.addBarSeries({ upColor: S.up, downColor: S.down, thinBars: false });
    else if (S.candle === "line") ser.price = m.addLineSeries({ color: "#5aa7ff", lineWidth: 2 });
    else ser.price = m.addCandlestickSeries({ upColor: S.candle === "hollow" ? "rgba(0,0,0,0)" : S.up, downColor: S.down, borderUpColor: S.up, borderDownColor: S.down, wickUpColor: S.up, wickDownColor: S.down });
    if (I.vol.on) { ser.vol = m.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false }); m.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } }); }
    if (I.bb.on) { ser.bbU = line(m, I.bb.color, 1); ser.bbM = line(m, I.bb.color, 1, { lineStyle: 2 }); ser.bbL = line(m, I.bb.color, 1); }
    ["ema3", "sma", "ema2", "ema1"].forEach(k => { if (I[k].on) ser[k] = line(m, I[k].color, I[k].width); });
    if (I.vwap.on) ser.vwap = line(m, I.vwap.color, I.vwap.width);
    if (I.st.on) { ser.stU = line(m, I.st.up, 2); ser.stD = line(m, I.st.down, 2); }
    if (I.rsi.on) {
      ser.rsi = line(C.rsi, I.rsi.color, 2, { lastValueVisible: true, priceFormat: { type: "price", precision: 1, minMove: 0.1 } });
      [I.rsi.ob, 50, I.rsi.os].forEach(v => ser.rsi.createPriceLine({ price: v, color: v === 50 ? "#33465e" : "#55667d", lineWidth: 1, lineStyle: 2, axisLabelVisible: v !== 50 }));
    }
    if (I.macd.on) {
      ser.mh = C.macd.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
      ser.ml = line(C.macd, I.macd.line, 2); ser.ms = line(C.macd, I.macd.signal, 2);
    }
    applyLayout();
    draw(true);
  }
  const pts = (arr, f) => cs.map((c, i) => arr[i] == null || !Number.isFinite(arr[i]) ? { time: c.time } : { time: c.time, value: f ? f(arr[i]) : arr[i] });
  function draw(fit) {
    if (!cs.length || !ser.price) return;
    IND = calc(cs);
    const I = S.ind;
    ser.price.setData(S.candle === "line" ? cs.map(c => ({ time: c.time, value: c.close })) : cs.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
    if (ser.vol) ser.vol.setData(cs.map(c => ({ time: c.time, value: c.volume || 0, color: (c.close >= c.open ? I.vol.up : I.vol.down) + "66" })));
    ["ema1", "ema2", "ema3", "sma", "vwap"].forEach(k => { if (ser[k]) ser[k].setData(pts(IND[k])); });
    if (ser.bbU) { ser.bbU.setData(pts(IND.bb.up)); ser.bbM.setData(pts(IND.bb.mid)); ser.bbL.setData(pts(IND.bb.lo)); }
    if (ser.stU) {
      const d = IND.st.dir, l = IND.st.line;
      ser.stU.setData(cs.map((c, i) => d[i] === 1 ? { time: c.time, value: l[i] } : { time: c.time }));
      ser.stD.setData(cs.map((c, i) => d[i] === -1 ? { time: c.time, value: l[i] } : { time: c.time }));
    }
    if (ser.rsi) ser.rsi.setData(pts(IND.rsi));
    if (ser.ml) {
      ser.ml.setData(pts(IND.macd.line)); ser.ms.setData(pts(IND.macd.sig));
      ser.mh.setData(cs.map((c, i) => IND.macd.hist[i] == null ? { time: c.time } : { time: c.time, value: IND.macd.hist[i], color: (IND.macd.hist[i] >= 0 ? I.macd.up : I.macd.down) + "aa" }));
    }
    levels();
    if (fit) { const n = cs.length, show = Math.min(n, isPhone() ? 70 : 160); C.main.timeScale().setVisibleLogicalRange({ from: n - show, to: n + 3 }); }
    legend(null);
    panel();
  }
  function sessions() {
    const days = []; cs.forEach((c, i) => { const d = dayKey(c.time); if (!days.length || days[days.length - 1].d !== d) days.push({ d, from: i, to: i }); else days[days.length - 1].to = i; });
    return days;
  }
  function dayStats() {
    const ds = sessions(), t = ds.at(-1), p = ds.at(-2);
    if (!t) return {};
    const seg = (x) => cs.slice(x.from, x.to + 1);
    const T = seg(t), P = p ? seg(p) : [];
    return { open: T[0].open, high: Math.max(...T.map(c => c.high)), low: Math.min(...T.map(c => c.low)),
      pc: P.length ? P.at(-1).close : null, ph: P.length ? Math.max(...P.map(c => c.high)) : null, pl: P.length ? Math.min(...P.map(c => c.low)) : null };
  }
  function levels() {
    plines.forEach(l => { try { ser.price.removePriceLine(l); } catch { } }); plines = [];
    if (!S.ind.lvl.on || !ser.price) return;
    const d = dayStats(), c = S.ind.lvl.color, add = (price, title, style = 2) => { if (Number.isFinite(price)) plines.push(ser.price.createPriceLine({ price, color: c, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title })); };
    add(d.ph, "PDH"); add(d.pl, "PDL"); add(d.pc, "PDC", 3); add(d.open, "Open", 1);
  }

  // ------------------------------------------------------------------ legend (crosshair) and data panel
  function legend(param) {
    let i = cs.length - 1;
    if (param && param.time != null) { const j = cs.findIndex(c => c.time === param.time); if (j >= 0) i = j; }
    const c = cs[i]; if (!c) { $("legend").innerHTML = ""; return; }
    const prev = cs[i - 1], ch = prev ? c.close - prev.close : null, I = S.ind, v = k => IND && IND[k] ? IND[k][i] : null;
    const items = [];
    ["ema1", "ema2", "ema3", "sma"].forEach(k => { if (I[k].on) items.push(`<span style="color:${I[k].color}">${k === "sma" ? "SMA" : "EMA"} ${I[k].len} ${fmt(v(k))}</span>`); });
    if (I.vwap.on) items.push(`<span style="color:${I.vwap.color}">VWAP ${fmt(v("vwap"))}</span>`);
    if (I.st.on && IND) items.push(`<span style="color:${IND.st.dir[i] === 1 ? I.st.up : I.st.down}">ST ${fmt(IND.st.line[i])}</span>`);
    if (I.bb.on && IND) items.push(`<span style="color:${I.bb.color}">BB ${fmt(IND.bb.up[i])} / ${fmt(IND.bb.lo[i])}</span>`);
    $("legend").innerHTML = `<div><b>${esc(S.sym)}</b> ${S.tf}m · ${crossFmt(c.time)}</div>` +
      `<div>O <b>${fmt(c.open)}</b> H <b>${fmt(c.high)}</b> L <b>${fmt(c.low)}</b> C <b style="${col(ch)}">${fmt(c.close)}</b> <span style="${col(ch)}">${sgn(ch)}</span>${c.volume ? ` · Vol ${Number(c.volume).toLocaleString("en-IN")}` : ""}</div>` +
      `<div class="ind">${items.join("")}</div>`;
    if (I.rsi.on) $("rsiTag").textContent = `RSI ${I.rsi.len}: ${fmt(v("rsi"), 1)}`;
    if (I.macd.on && IND) $("macdTag").textContent = `MACD ${I.macd.fast},${I.macd.slow},${I.macd.sig}: ${fmt(IND.macd.line[i])} · signal ${fmt(IND.macd.sig[i])} · hist ${fmt(IND.macd.hist[i])}`;
  }
  C.main.subscribeCrosshairMove(p => legend(p && p.time != null ? p : null));

  function panel() {
    if (!cs.length || !IND) return;
    const n = cs.length - 1, c = cs[n], d = dayStats(), I = S.ind, last = tick || c.close, a = A || {}, fin = a.final || {};
    const at = IND.atr[n], vw = IND.vwap[n], r = IND.rsi[n], st = IND.st, e1 = IND.ema1[n], e2 = IND.ema2[n], h = IND.macd.hist[n];
    const chg = d.pc ? last - d.pc : null;
    const side = fin.side || a.side, lvl = fin.level || "", act = fin.action || (a.signal || "WAIT").replace("_", " ");
    const sigCol = /BUY|CONFIRMED/.test(act + lvl) ? (side === "PE" ? "var(--red)" : "var(--green)") : lvl === "WATCH" || lvl === "PENDING" ? "var(--yellow)" : "var(--text)";
    const atrX = v => at ? fmt(v / at, 2) + " ATR" : "—";
    const rows = (title, kv) => `<div><h3>${title}</h3><div class="kv">${kv.map(([k, v, s]) => `<span>${k}</span><b style="${s || ""}">${v}</b>`).join("")}</div></div>`;
    const html = [
      `<div><h3>Signal</h3><div class="cw-sig"><div class="act" style="color:${sigCol}">${esc(act)}</div><small>${esc(fin.headline || a.decision || "Waiting for the engine…")}</small>` +
      `<small>Score ${a.status === "ok" ? `${a.score}/100 · bull ${a.bull_score} · bear ${a.bear_score}` : "—"}${a.regime_label ? " · " + esc(a.regime_label) : ""}</small></div></div>`,
      rows("Price", [["Last", fmt(last), col(chg)], ["Change", chg == null ? "—" : `${sgn(chg)} (${sgn(chg / d.pc * 100)}%)`, col(chg)], ["Day open", fmt(d.open)],
        ["Day high / low", `${fmt(d.high)} / ${fmt(d.low)}`], ["Prev close", fmt(d.pc)], ["Prev day high / low", `${fmt(d.ph)} / ${fmt(d.pl)}`],
        ["Day range", d.high ? `${fmt(d.high - d.low)} (${atrX(d.high - d.low)})` : "—"]]),
      rows("Trend", [[`EMA ${I.ema1.len} / ${I.ema2.len}`, `${fmt(e1)} / ${fmt(e2)}`, e1 != null && e2 != null ? col(e1 - e2) : ""],
        ["EMA trend", e1 == null || e2 == null ? "—" : e1 > e2 ? "Up (fast above slow)" : "Down (fast below slow)", e1 != null && e2 != null ? col(e1 - e2) : ""],
        ["Price vs VWAP", vw == null ? "—" : `${sgn(last - vw)} (${last >= vw ? "above" : "below"})`, vw == null ? "" : col(last - vw)],
        [`Supertrend (${I.st.len}, ${I.st.mult})`, st.dir[n] == null ? "—" : `${st.dir[n] === 1 ? "Up" : "Down"} · ${fmt(st.line[n])}`, st.dir[n] ? col(st.dir[n]) : ""],
        ["ADX (engine)", a.adx ? `${fmt(a.adx.adx, 1)} · +DI ${fmt(a.adx.plus_di, 1)} / −DI ${fmt(a.adx.minus_di, 1)}` : "—"],
        ["Higher timeframe", a.htf ? esc(a.htf.trend || a.htf.label || a.htf.bias || "—") : "—"]]),
      rows("Momentum & volatility", [[`RSI (${I.rsi.len})`, r == null ? "—" : `${fmt(r, 1)}${r >= I.rsi.ob ? " · overbought" : r <= I.rsi.os ? " · oversold" : ""}`],
        [`MACD hist (${I.macd.fast},${I.macd.slow},${I.macd.sig})`, fmt(h), col(h)], ["ATR (14)", at == null ? "—" : `${fmt(at)} pts (${fmt(at / last * 100, 2)}%)`],
        ["Bollinger %B (engine)", a.bollinger ? `${fmt(a.bollinger.percent_b, 2)}${a.bollinger.squeeze ? " · squeeze" : ""}` : "—"],
        ["Relative volume", a.relative_volume != null ? fmt(a.relative_volume, 2) + "×" : "—"]]),
      rows("Room", [["To day high", d.high ? `${fmt(d.high - last)} (${atrX(d.high - last)})` : "—"], ["To day low", d.low ? `${fmt(last - d.low)} (${atrX(last - d.low)})` : "—"],
        ["Recent high / low (engine)", a.breakout_high ? `${fmt(a.breakout_high)} / ${fmt(a.breakdown_low)}` : "—"],
        ["Chart pattern", a.chart_pattern && a.chart_pattern.name && a.chart_pattern.name !== "None" ? esc(a.chart_pattern.name) : "—"]]),
    ];
    $("groups").innerHTML = html.join("");
    $("strip").innerHTML = [["Signal", esc(act), `color:${sigCol}`], ["Last", fmt(last), col(chg)], ["Chg", chg == null ? "—" : sgn(chg), col(chg)], ["RSI", fmt(r, 1)], ["ATR", fmt(at)],
      ["VWAP", vw == null ? "—" : sgn(last - vw), vw == null ? "" : col(last - vw)], ["EMA", e1 == null || e2 == null ? "—" : e1 > e2 ? "Up" : "Down", e1 != null && e2 != null ? col(e1 - e2) : ""],
      ["ST", st.dir[n] == null ? "—" : st.dir[n] === 1 ? "Up" : "Down"], ["Score", a.status === "ok" ? a.score : "—"], ["Day H/L", `${fmt(d.high, 0)} / ${fmt(d.low, 0)}`]]
      .map(([k, v, s]) => `<div><span>${k}</span><b style="${s || ""}">${v}</b></div>`).join("");
  }

  // ------------------------------------------------------------------ layout
  function applyLayout() {
    const L = S.layout[dev()], ph = Number(S.paneH[dev()]) || 0.2, size = S.dataSize[dev()] || "m";
    document.body.classList.remove("l-side", "l-below", "l-strip", "l-only");
    document.body.classList.add("l-" + L);
    $("layout").value = L;
    const data = $("data");
    data.style.width = L === "side" ? (isPhone() ? { s: "34%", m: "42%", l: "52%" } : { s: "260px", m: "330px", l: "430px" })[size] : "";
    data.style.maxHeight = L === "below" ? { s: "28vh", m: "40vh", l: "55vh" }[size] : "";
    const H = $("charts").clientHeight || 500;
    $("rsiPane").style.display = S.ind.rsi.on ? "" : "none"; $("macdPane").style.display = S.ind.macd.on ? "" : "none";
    $("rsiPane").style.height = $("macdPane").style.height = Math.max(70, Math.round(H * ph)) + "px";
    const lower = S.ind.rsi.on || S.ind.macd.on;
    C.main.applyOptions({ timeScale: { visible: !lower } });
    C.rsi.applyOptions({ timeScale: { visible: S.ind.rsi.on && !S.ind.macd.on } });
    C.macd.applyOptions({ timeScale: { visible: S.ind.macd.on } });
    requestAnimationFrame(resize);
  }
  function resize() {
    [["main", "mainChart"], ["rsi", "rsiChart"], ["macd", "macdChart"]].forEach(([k, id]) => { const el = $(id); if (el.clientWidth && el.clientHeight) C[k].resize(el.clientWidth, el.clientHeight); });
  }
  new ResizeObserver(() => applyLayoutSoft()).observe($("charts"));
  let lastDev = dev();
  function applyLayoutSoft() { if (dev() !== lastDev) { lastDev = dev(); applyLayout(); } else resize(); }

  // ------------------------------------------------------------------ settings drawer
  function renderSettings() {
    $("devNote").textContent = isPhone() ? "(this phone layout - computers keep their own)" : "(computer layout - phones keep their own)";
    $("sLayout").value = S.layout[dev()]; $("sPaneH").value = String(S.paneH[dev()]); $("sDataSize").value = S.dataSize[dev()]; $("sCandle").value = S.candle;
    $("cUp").value = S.up; $("cDown").value = S.down;
    const row = ([k, name, note, params]) => {
      const o = S.ind[k];
      const ps = params.map(([p, lab, min, max, step]) => `<label class="p">${lab}<input type="number" data-k="${k}" data-p="${p}" min="${min}" max="${max}" step="${step || 1}" value="${o[p]}"></label>`).join("");
      const cs_ = (COLORS[k] || []).map(cp => `<input type="color" data-k="${k}" data-c="${cp}" value="${o[cp]}" title="${cp}">`).join("");
      return `<div class="ind-row"><input type="checkbox" data-k="${k}" data-on="1" ${o.on ? "checked" : ""} aria-label="${name}"><div class="nm">${name}<small>${note}</small></div><div class="ps">${ps}${cs_}</div></div>`;
    };
    $("indMain").innerHTML = META.main.map(row).join(""); $("indLow").innerHTML = META.low.map(row).join("");
  }
  function onSet(e) {
    const t = e.target, k = t.dataset.k;
    if (t.id === "sLayout") S.layout[dev()] = t.value;
    else if (t.id === "sPaneH") S.paneH[dev()] = t.value;
    else if (t.id === "sDataSize") S.dataSize[dev()] = t.value;
    else if (t.id === "sCandle") S.candle = t.value;
    else if (t.id === "cUp") S.up = t.value; else if (t.id === "cDown") S.down = t.value;
    else if (k && t.dataset.on) S.ind[k].on = t.checked;
    else if (k && t.dataset.c) S.ind[k][t.dataset.c] = t.value;
    else if (k && t.dataset.p) {
      const v = Number(t.value), min = Number(t.min), max = Number(t.max);
      if (!Number.isFinite(v) || v < min || v > max) { t.style.borderColor = "var(--red)"; return; }
      t.style.borderColor = ""; S.ind[k][t.dataset.p] = v;
      if (k === "macd" && S.ind.macd.fast >= S.ind.macd.slow) { toast("MACD: fast must be shorter than slow"); return; }
    } else return;
    save(); build();
  }
  $("set").addEventListener("change", onSet);
  $("openSet").onclick = () => { renderSettings(); $("set").classList.add("open"); };
  $("closeSet").onclick = () => $("set").classList.remove("open");
  $("set").addEventListener("click", e => { if (e.target.id === "set") $("set").classList.remove("open"); });
  $("resetSet").onclick = () => { const keep = { sym: S.sym, tf: S.tf, days: S.days }; S = { ...clone(DEF), ...keep }; save(); renderSettings(); build(); toast("Chart settings reset"); };
  $("layout").onchange = e => { S.layout[dev()] = e.target.value; save(); applyLayout(); };

  // ------------------------------------------------------------------ data: REST first load + live WebSocket
  const mode = () => { try { return localStorage.getItem("oi-signal-mode-v2") || "standard"; } catch { return "standard"; } };
  const scoreMin = () => { try { const v = Number(localStorage.getItem("oi-score-min-v2")); return [60, 65, 70].includes(v) ? v : 60; } catch { return 60; } };
  const reconfirm = () => { try { const v = localStorage.getItem("oi-reconfirm"); const n = Number(v && v.replace(/"/g, "")); return Number.isFinite(n) && v !== null ? n : -1; } catch { return -1; } };
  function prep(raw) {
    const v = (raw || []).map(c => ({ time: toT(c.time), open: +c.open, high: +c.high, low: +c.low, close: +c.close, volume: +c.volume || 0 }))
      .filter(c => Number.isFinite(c.time) && [c.open, c.high, c.low, c.close].every(Number.isFinite)).sort((a, b) => a.time - b.time);
    const u = []; for (const c of v) { if (!u.length || u.at(-1).time !== c.time) u.push(c); else u[u.length - 1] = c; } return u;
  }
  let loadGen = 0;
  async function load() {
    const g = ++loadGen;
    $("live").textContent = "Loading candles…";
    try {
      const d = await api(`/api/candles?symbol=${S.sym}&interval=${S.tf}&days=${S.days}`);
      if (g !== loadGen) return;
      cs = prep(d.candles); tick = null;
      if (!cs.length) { $("live").textContent = "No candles yet"; return; }
      draw(true);
      $("live").textContent = `${cs.length} candles · ${d.volume_source ? "volume: " + d.volume_source : "no volume"}`;
    } catch (e) { $("live").textContent = "Candles unavailable: " + e.message; }
    api(`/api/analysis?symbol=${S.sym}&interval=${S.tf}&mode=${mode()}&score_min=${scoreMin()}&reconfirm=${reconfirm()}`).then(a => { if (g === loadGen) { A = a; panel(); } }).catch(() => { });
    connect();
  }
  function mergeBars(list) {
    if (!cs.length) return;
    let appended = false;
    for (const b of prep(list)) {
      const j = cs.findIndex((c, i) => i >= cs.length - 6 && c.time === b.time);
      if (j >= 0) cs[j] = b; else if (b.time > cs.at(-1).time) { cs.push(b); appended = true; }
    }
    tick = null;
    draw(false);
    if (appended) C.main.timeScale().scrollToRealTime();
  }
  let ws = null, wsGen = 0, lastTickDraw = 0, lastMsg = 0;
  function connect() {
    const g = ++wsGen;
    if (ws) { ws.onclose = ws.onmessage = ws.onerror = null; try { ws.close(); } catch { } }
    ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/live?symbol=${S.sym}`);
    ws.onopen = () => { if (g === wsGen) ws.send(JSON.stringify({ type: "sub", interval: S.tf, mode: mode(), score_min: scoreMin(), reconfirm: reconfirm(), view: "index" })); };
    ws.onmessage = ev => {
      if (g !== wsGen) return;
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      lastMsg = Date.now();
      if (m.type === "bars" && m.interval === S.tf) { mergeBars(m.candles || []); $("live").textContent = "● Live · " + new Date().toLocaleTimeString("en-IN", { hour12: false }); }
      else if (m.type === "analysis" && m.interval === S.tf && m.data) { A = m.data; panel(); }
      else if (m.type === "index" && m.index && m.index.fresh && !m.index.rest && cs.length) {
        const p = Number(m.index.ltp), last = cs.at(-1), now = Date.now() / 1000;
        if (!Number.isFinite(p) || now - last.time >= S.tf * 60) return;          // a new candle comes with the next "bars" push
        last.close = p; last.high = Math.max(last.high, p); last.low = Math.min(last.low, p); tick = p;
        if (Date.now() - lastTickDraw > 400) {
          lastTickDraw = Date.now();
          try { ser.price.update(S.candle === "line" ? { time: last.time, value: p } : { time: last.time, open: last.open, high: last.high, low: last.low, close: p }); } catch { }
          legend(null); panel();
        }
      }
    };
    ws.onclose = () => { if (g === wsGen) setTimeout(() => { if (g === wsGen && document.visibilityState === "visible") connect(); }, 4000); };
  }
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && (!ws || ws.readyState > 1)) connect(); });
  setInterval(() => {                      // REST fallback when the socket is quiet (no token / server busy)
    if (document.visibilityState !== "visible" || Date.now() - lastMsg < 20000 || !cs.length) return;
    api(`/api/candles?symbol=${S.sym}&interval=${S.tf}&days=1`).then(d => mergeBars((d.candles || []).slice(-4))).catch(() => { });
  }, 15000);

  // ------------------------------------------------------------------ toolbar
  $("sym").value = S.sym; $("tf").value = String(S.tf); $("days").value = String(S.days);
  const reload = () => { S.sym = $("sym").value; S.tf = Number($("tf").value); S.days = Number($("days").value); save(); A = null; load(); };
  ["sym", "tf", "days"].forEach(id => $(id).onchange = reload);
  if (window.opener || window.self !== window.top) $("back").addEventListener("click", e => { if (window.opener) { e.preventDefault(); try { window.opener.focus(); } catch { } window.close(); } });
  document.title = `${S.sym} ${S.tf}m · Chart · Option Intelligence`;
  build();
  load();
})();
