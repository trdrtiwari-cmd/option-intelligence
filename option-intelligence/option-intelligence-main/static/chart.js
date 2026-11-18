/* Option Intelligence - chart window (v1.20.43).
   A full-size candle chart with the important numbers beside it, more indicators (EMA x3, SMA, VWAP, Bollinger Bands,
   Supertrend, volume, previous-day levels, RSI and MACD panes) and settings for each (length, multiplier, colour). Live
   through the same WebSocket as the terminal. Layout (data beside / below / strip / hidden, pane height, panel size) is
   saved separately for phones and computers. Display only: BUY signals still use the tested engine values. */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const LS_KEY = "oi-chart-v1";
  const isPhone = () => window.matchMedia("(max-width:820px), (max-height:520px) and (pointer:coarse)").matches;
  const isLandscape = () => isPhone() && window.innerWidth > window.innerHeight;
  const EMBED = new URLSearchParams(location.search).get("embed") === "1";     // v1.20.50: inside the Terminal's Signal confirmation
  if (EMBED) document.documentElement.classList.add("cw-embed");
  const DEF = {
    sym: "NIFTY", tf: 5, days: 5,
    view: "auto",
    layout: { desktop: "side", mobile: "below", landscape: "side", embed: "strip" }, paneH: { desktop: "0.22", mobile: "0.16", landscape: "0.2", embed: "0.18" },
    dataSize: { desktop: "m", mobile: "m", landscape: "s", embed: "s" },
    candle: "candle", up: "#25c58a", down: "#ff667a",
    ind: {
      ema1: { on: true, len: 9, color: "#25c58a", width: 2 },
      ema2: { on: true, len: 21, color: "#ff4d5e", width: 2 },
      ema3: { on: false, len: 50, color: "#f3c969", width: 2 },
      sma: { on: false, len: 20, color: "#b48cff", width: 1 },
      vwap: { on: true, color: "#ffffff", width: 2 },
      bb: { on: false, len: 20, mult: 2, color: "#5aa7ff" },
      st: { on: false, len: 10, mult: 3, up: "#25c58a", down: "#ff667a" },
      vol: { on: true, up: "#25c58a", down: "#ff667a", ma: 20, maColor: "#f3c969" },
      lvl: { on: true, color: "#f3c969" },
      sr: { on: true, sup: "#25c58a", res: "#ff4d5e" },
      tl: { on: true, sup: "#3fd0c9", res: "#ffb547" },
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
      ["vol", "Volume", "Bars at the bottom of the chart, with a moving average line (MA 0 = no line)", [["ma", "MA", 0, 200]]],
      ["lvl", "Key levels", "Previous day high / low / close, today's open", []],
      ["sr", "Auto support / resistance", "Nearest 3 zones each side (S1-S3, R1-R3) of what is on the chart - index or option", []],
      ["tl", "Auto trendlines", "Support and resistance trendlines; dashed with ✕ when broken", []],
    ],
    low: [
      ["rsi", "RSI", "Pane with overbought / oversold lines", [["len", "Length", 2, 100], ["ob", "Upper", 50, 95], ["os", "Lower", 5, 50]]],
      ["macd", "MACD", "Pane: line, signal and histogram", [["fast", "Fast", 2, 100], ["slow", "Slow", 3, 200], ["sig", "Signal", 2, 50]]],
    ],
  };
  const COLORS = { ema1: ["color"], ema2: ["color"], ema3: ["color"], sma: ["color"], vwap: ["color"], bb: ["color"], st: ["up", "down"], vol: ["up", "down", "maColor"],
    lvl: ["color"], sr: ["sup", "res"], tl: ["sup", "res"], rsi: ["color"], macd: ["line", "signal"] };

  // ------------------------------------------------------------------ settings
  const clone = o => JSON.parse(JSON.stringify(o));
  function merge(base, over) {
    const out = clone(base);
    (function rec(b, o) { for (const k in o) { if (o[k] && typeof o[k] === "object" && !Array.isArray(o[k]) && b[k] && typeof b[k] === "object") rec(b[k], o[k]); else if (k in b) b[k] = o[k]; } })(out, over || {});
    return out;
  }
  let S = (() => { try { return merge(DEF, JSON.parse(localStorage.getItem(LS_KEY) || "{}")); } catch { return clone(DEF); } })();
  const q = new URLSearchParams(location.search);
  { const t = (() => { try { const o = JSON.parse(localStorage.getItem("oi-settings-v1") || "{}"); const g = k => Array.isArray(o["terminal." + k]) ? o["terminal." + k][0] : null; return { sym: g("symbol"), tf: Number(g("interval")) }; } catch { return {}; } })();
    if (["NIFTY", "BANKNIFTY"].includes(t.sym)) S.sym = t.sym;
    if ([1, 3, 5, 15, 30].includes(t.tf)) S.tf = t.tf; }
  if (["NIFTY", "BANKNIFTY"].includes((q.get("symbol") || "").toUpperCase())) S.sym = q.get("symbol").toUpperCase();
  if ([1, 3, 5, 15, 30].includes(Number(q.get("interval")))) S.tf = Number(q.get("interval"));
  const save = () => { try { localStorage.setItem(LS_KEY, JSON.stringify(S)); } catch { } };
  // v1.20.54: one index everywhere - the chart starts on the Terminal's index / timeframe and follows it (and the Terminal follows
  // a change made here). The address (?symbol=&interval=) still wins when the Terminal opens the chart window.
  const termPick = () => { try { const o = JSON.parse(localStorage.getItem("oi-settings-v1") || "{}"); const g = k => Array.isArray(o["terminal." + k]) ? o["terminal." + k][0] : null; return { sym: g("symbol"), tf: Number(g("interval")) }; } catch { return {}; } };
  const dev = () => EMBED ? "embed" : isLandscape() ? "landscape" : isPhone() ? "mobile" : "desktop";

  function toast(msg) { const el = $("toast"); el.textContent = msg; el.classList.add("show"); setTimeout(() => el.classList.remove("show"), 4000); }
  async function api(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch { } throw new Error(d || ("HTTP " + r.status)); }
    return r.json();
  }
  const fmt = (v, n = 2) => v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: n, maximumFractionDigits: n });
  const sgn = (v, n = 2) => v == null || !Number.isFinite(v) ? "—" : (v > 0 ? "+" : "") + fmt(v, n);
  const col = v => v == null || !Number.isFinite(v) ? "" : v > 0 ? "color:var(--green)" : v < 0 ? "color:var(--red)" : "";
  const fmtVol = v => v == null || !Number.isFinite(Number(v)) ? "—" : v >= 1e7 ? (v / 1e7).toFixed(2) + " Cr" : v >= 1e5 ? (v / 1e5).toFixed(2) + " L" : v >= 1e3 ? (v / 1e3).toFixed(1) + " K" : String(Math.round(v));
  let VOLINFO = { source: "", note: "" };
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
  let autoSer = [], autoLines = [], autoKey = "", SR = null, TL = null;

  // keep the three panes on the same bars
  Object.entries(C).forEach(([k, ch]) => ch.timeScale().subscribeVisibleLogicalRangeChange(r => {
    if (syncing || !r) return; syncing = true;
    Object.entries(C).forEach(([k2, c2]) => { if (k2 !== k) try { c2.timeScale().setVisibleLogicalRange(r); } catch { } });
    syncing = false;
  }));

  function clearSeries() {
    autoSer.forEach(s => { try { C.main.removeSeries(s); } catch { } });
    drawSer.forEach(s => { try { C.main.removeSeries(s); } catch { } }); drawSer = []; drawLines = [];
    Object.values(ser).forEach(s => { for (const ch of Object.values(C)) { try { ch.removeSeries(s); } catch { } } });
    ser = {}; plines = []; autoSer = []; autoLines = []; autoKey = "";
  }
  function line(chart, color, width = 2, extra = {}) { return chart.addLineSeries({ color, lineWidth: width, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...extra }); }
  function build() {
    clearSeries();
    const I = S.ind, m = C.main;
    m.applyOptions({ leftPriceScale: { visible: false } });
    if (S.candle === "bar") ser.price = m.addBarSeries({ upColor: S.up, downColor: S.down, thinBars: false });
    else if (S.candle === "line") ser.price = m.addLineSeries({ color: "#5aa7ff", lineWidth: 2 });
    else ser.price = m.addCandlestickSeries({ upColor: S.candle === "hollow" ? "rgba(0,0,0,0)" : S.up, downColor: S.down, borderUpColor: S.up, borderDownColor: S.down, wickUpColor: S.up, wickDownColor: S.down });
    if (I.vol.on) {
      const vs = isPhone() ? "vol" : "left";                 // computers: volume values on a left axis; phones: legend / data only
      ser.vol = m.addHistogramSeries({ priceScaleId: vs, priceFormat: { type: "volume" }, lastValueVisible: !isPhone(), priceLineVisible: false });
      m.priceScale(vs).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 }, visible: vs === "left", borderColor: "#27384d" });
      if (I.vol.ma > 0) ser.volMa = m.addLineSeries({ priceScaleId: vs, color: I.vol.maColor, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat: { type: "volume" } });
    }
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
    renderDrawings();
  }
  const pts = (arr, f) => cs.map((c, i) => arr[i] == null || !Number.isFinite(arr[i]) ? { time: c.time } : { time: c.time, value: f ? f(arr[i]) : arr[i] });
  function draw(fit) {
    if (!cs.length || !ser.price) return;
    IND = calc(cs);
    const I = S.ind;
    ser.price.setData(S.candle === "line" ? cs.map(c => ({ time: c.time, value: c.close })) : cs.map(c => ({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close })));
    if (ser.vol) ser.vol.setData(cs.map(c => c.volume == null ? { time: c.time } : { time: c.time, value: c.volume, color: (c.close >= c.open ? I.vol.up : I.vol.down) + "66" }));
    if (ser.volMa) ser.volMa.setData(pts(volAvg(I.vol.ma)));
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
    autoLevels();
    if (fit) { const n = cs.length, show = Math.min(n, isPhone() ? 70 : 160); C.main.timeScale().setVisibleLogicalRange({ from: n - show, to: n + 3 }); }
    legend(null);
    panel();
  }
  // average of the known volumes over the last n candles (candles without volume data are skipped, not counted as 0)
  function volAvg(n) {
    return cs.map((c, i) => {
      if (n <= 0 || c.volume == null) return null;
      const w = cs.slice(Math.max(0, i - n + 1), i + 1).filter(x => x.volume != null);
      return w.length >= Math.min(n, 5) ? w.reduce((a, x) => a + x.volume, 0) / w.length : null;
    });
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

  // v1.20.47: automatic support / resistance zones and trendlines, from the candles on the chart (the option's own levels when the
  // chart shows an option). Same code as the old Terminal chart (static/sr.js, static/trendlines.js). Recomputed when a candle closes
  // (or every 20 s), not on every tick.
  let autoAt = 0;
  function autoLevels(force) {
    if (!ser.price || !cs.length) return;
    const key = `${cs.length}|${cs[0].time}|${OPT ? OPT.key : S.sym}|${S.ind.sr.on}|${S.ind.tl.on}|${S.ind.sr.sup}${S.ind.sr.res}${S.ind.tl.sup}${S.ind.tl.res}`;
    if (!force && key === autoKey && Date.now() - autoAt < 20000) return;
    autoKey = key; autoAt = Date.now();
    autoSer.forEach(s => { try { C.main.removeSeries(s); } catch { } }); autoSer = [];
    autoLines.forEach(l => { try { ser.price.removePriceLine(l); } catch { } }); autoLines = [];
    const LS = LightweightCharts.LineStyle, lastTime = cs.at(-1).time, noScale = () => null;
    SR = null; TL = null;
    if (S.ind.sr.on && window.OISR) {
      try { SR = window.OISR.computeSR(cs, S.tf, S.sym, !OPT); } catch { SR = null; }
      const styles = [LS.Solid, LS.Dashed, LS.Dotted];
      const one = (lv, i, sup) => {
        const color = sup ? S.ind.sr.sup : S.ind.sr.res;
        if (lv.originTime < lastTime) {
          const s = C.main.addLineSeries({ color, lineWidth: lv.strength === "Strong" ? 3 : lv.strength === "Medium" ? 2 : 1, lineStyle: styles[i] ?? LS.Dotted,
            priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: noScale });
          s.setData([{ time: lv.originTime, value: lv.price }, { time: lastTime, value: lv.price }]); autoSer.push(s);
        }
        autoLines.push(ser.price.createPriceLine({ price: lv.price, lineVisible: false, axisLabelVisible: true, title: (sup ? "S" : "R") + (i + 1), axisLabelColor: color, axisLabelTextColor: "#0b1220" }));
      };
      if (SR) { SR.supports.forEach((l, i) => one(l, i, true)); SR.resistances.forEach((l, i) => one(l, i, false)); }
    }
    if (S.ind.tl.on && window.OITrendlines) {
      try { TL = window.OITrendlines.computeTrendlines(cs); } catch { TL = null; }
      for (const t of TL ? [TL.support, TL.resistance] : []) {
        if (!t) continue;
        const sup = t.kind === "support";
        const s = C.main.addLineSeries({ color: sup ? S.ind.tl.sup : S.ind.tl.res, lineWidth: 2, lineStyle: t.broken ? LS.Dashed : LS.Solid, priceLineVisible: false,
          lastValueVisible: true, title: (sup ? "TL S" : "TL R") + (t.broken ? " ✕" : ""), crosshairMarkerVisible: false, autoscaleInfoProvider: noScale });
        s.setData([{ time: t.from.time, value: t.from.value }, { time: t.to.time, value: t.to.value }]); autoSer.push(s);
      }
    }
  }

  // v1.20.51: drawing tools - trend line, ray, horizontal line, price zone. Saved on the account per chart (index, or each option
  // contract), so they come back on every device. Tap / click points on the price chart.
  const DKEY = "oi-chart-draw-v1";
  let drawMode = null, drawPend = null, drawSer = [], drawLines = [];
  const dKey = () => `${S.sym}|${OPT ? OPT.key : "index"}`;
  const dAll = () => { try { const o = JSON.parse(localStorage.getItem(DKEY) || "{}"); return o && typeof o === "object" ? o : {}; } catch { return {}; } };
  const dGet = () => dAll()[dKey()] || [];
  const dSet = list => { const o = dAll(); if (list.length) o[dKey()] = list.slice(-60); else delete o[dKey()]; try { localStorage.setItem(DKEY, JSON.stringify(o)); } catch { } };
  function renderDrawings() {
    drawSer.forEach(s => { try { C.main.removeSeries(s); } catch { } }); drawSer = [];
    drawLines.forEach(l => { try { ser.price.removePriceLine(l); } catch { } }); drawLines = [];
    if (!ser.price || !cs.length) return;
    const lastT = cs.at(-1).time, LS = LightweightCharts.LineStyle, noScale = () => null;
    for (const d of dGet()) {
      if (d.t === "hline" || d.t === "zone") {
        (d.t === "hline" ? [d.p] : [d.p, d.p2]).forEach((p, k) => drawLines.push(ser.price.createPriceLine({ price: p, color: d.c, lineWidth: 2, lineStyle: d.t === "zone" ? LS.Dashed : LS.Solid, axisLabelVisible: true, title: d.t === "zone" ? (k ? "zone" : "zone") : "" })));
        continue;
      }
      let a = d.a, b = d.b;
      if (!a || !b || a.time === b.time) continue;
      if (a.time > b.time) [a, b] = [b, a];
      const pts_ = [a, b];
      if (d.t === "ray" && lastT > b.time) { const slope = (b.value - a.value) / (b.time - a.time); pts_.push({ time: lastT, value: b.value + slope * (lastT - b.time) }); }
      const s = C.main.addLineSeries({ color: d.c, lineWidth: 2, priceLineVisible: false, lastValueVisible: d.t === "ray", crosshairMarkerVisible: false, autoscaleInfoProvider: noScale });
      try { s.setData(pts_); drawSer.push(s); } catch { try { C.main.removeSeries(s); } catch { } }
    }
  }
  function setTool(t) {
    drawMode = drawMode === t ? null : t; drawPend = null;
    document.querySelectorAll("#drawBar [data-tool]").forEach(b => b.classList.toggle("on", b.dataset.tool === drawMode));
    $("drawHint").textContent = !drawMode ? "" : drawMode === "hline" ? "Tap a price" : drawMode === "zone" ? "Tap the top, then the bottom of the zone" : "Tap the first point";
  }
  document.querySelectorAll("#drawBar [data-tool]").forEach(b => b.onclick = () => setTool(b.dataset.tool));
  $("drawToggle").onclick = () => { const t = $("drawTools"), open = t.hidden; t.hidden = !open; $("drawToggle").classList.toggle("on", open); if (!open) setTool(null); };
  $("drawUndo").onclick = () => { const l = dGet(); l.pop(); dSet(l); renderDrawings(); };
  $("drawClear").onclick = () => { if (dGet().length && confirm("Remove all drawings on this chart?")) { dSet([]); renderDrawings(); } };
  C.main.subscribeClick(param => {
    if (!drawMode || !param || !param.point || !ser.price) return;
    const price = ser.price.coordinateToPrice(param.point.y);
    let time = param.time != null ? param.time : C.main.timeScale().coordinateToTime(param.point.x);
    if (time == null && cs.length) time = cs.at(-1).time;
    if (!Number.isFinite(price)) return;
    const c = $("drawColor").value || "#5aa7ff", p = Math.round(price * 100) / 100;
    if (drawMode === "hline") { dSet([...dGet(), { t: "hline", p, c }]); setTool(null); renderDrawings(); return; }
    if (!drawPend) { drawPend = { time, value: p }; $("drawHint").textContent = drawMode === "zone" ? "Now tap the bottom of the zone" : "Now tap the second point"; return; }
    const item = drawMode === "zone" ? { t: "zone", p: drawPend.value, p2: p, c } : { t: drawMode, a: drawPend, b: { time, value: p }, c };
    if (drawMode !== "zone" && item.a.time === item.b.time) { $("drawHint").textContent = "Pick a second point on another candle"; return; }
    dSet([...dGet(), item]); setTool(null); renderDrawings();
  });
  window.addEventListener("storage", e => { if (e.key === DKEY) renderDrawings(); });

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
    $("legend").innerHTML = `<div><b>${esc(OPT ? OPT.name : S.sym)}</b> ${S.tf}m · ${crossFmt(c.time)}</div>` +
      `<div>O <b>${fmt(c.open)}</b> H <b>${fmt(c.high)}</b> L <b>${fmt(c.low)}</b> C <b style="${col(ch)}">${fmt(c.close)}</b> <span style="${col(ch)}">${sgn(ch)}</span>${c.volume != null && c.volume > 0 ? ` · Vol <b>${fmtVol(c.volume)}</b>${I.vol.on && I.vol.ma > 0 && volAvg(I.vol.ma)[i] ? ` (avg ${fmtVol(volAvg(I.vol.ma)[i])} · ${(c.volume / volAvg(I.vol.ma)[i]).toFixed(2)}×)` : ""}` : VOLINFO.note ? ` · <span style="color:var(--yellow)">no volume</span>` : ""}</div>` +
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
      rows(OPT ? "Premium · " + esc(OPT.name) : "Price", [["Last", fmt(last), col(chg)], ["Change", chg == null ? "—" : `${sgn(chg)} (${sgn(chg / d.pc * 100)}%)`, col(chg)], ["Day open", fmt(d.open)],
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
      rows("Volume", (() => {
        const v = c.volume, av = S.ind.vol.ma > 0 ? volAvg(S.ind.vol.ma)[n] : null, today = cs.filter(x => dayKey(x.time) === dayKey(c.time) && x.volume != null);
        return [["Last candle", v == null ? "—" : fmtVol(v)], [`Average (${S.ind.vol.ma || "—"} candles)`, fmtVol(av)],
          ["Relative volume", v != null && av ? `${(v / av).toFixed(2)}×${v / av >= 1.5 ? " · high" : v / av <= 0.6 ? " · low" : ""}` : "—", v != null && av ? (v / av >= 1.5 ? "color:var(--green)" : v / av <= 0.6 ? "color:var(--yellow)" : "") : ""],
          ["Today so far", today.length ? fmtVol(today.reduce((a2, x) => a2 + x.volume, 0)) : "—"],
          ["Source", OPT ? "option contract" : esc(VOLINFO.source || "—")],
          ...(VOLINFO.note ? [["Note", esc(VOLINFO.note), "color:var(--yellow)"]] : [])];
      })()),
      rows("Support / resistance", [
        ...(SR ? [...SR.resistances.map((l, i) => ["R" + (i + 1) + " · " + l.strength, `${fmt(l.price)} (+${fmt(l.distance)} · ${l.atrMult == null ? "—" : fmt(l.atrMult, 1) + " ATR"})`, "color:var(--red)"]).reverse(),
          ...SR.supports.map((l, i) => ["S" + (i + 1) + " · " + l.strength, `${fmt(l.price)} (−${fmt(l.distance)} · ${l.atrMult == null ? "—" : fmt(l.atrMult, 1) + " ATR"})`, "color:var(--green)"])] : [["Auto S/R", S.ind.sr.on ? "not enough candles" : "off"]]),
        ...(S.ind.tl.on ? [TL && TL.support, TL && TL.resistance].map((t, k) => [k ? "Resistance trendline" : "Support trendline",
          !t ? "none clear" : `${fmt(t.value)} ${t.direction === "rising" ? "↗" : t.direction === "falling" ? "↘" : "→"}${t.broken ? (k ? " · BROKEN above" : " · BROKEN below") : ` · ${t.touches} touches`}`,
          t && t.broken ? "color:var(--yellow)" : ""]) : [])]),
      rows("Room", [["To day high", d.high ? `${fmt(d.high - last)} (${atrX(d.high - last)})` : "—"], ["To day low", d.low ? `${fmt(last - d.low)} (${atrX(last - d.low)})` : "—"],
        ["Recent high / low (engine)", a.breakout_high ? `${fmt(a.breakout_high)} / ${fmt(a.breakdown_low)}` : "—"],
        ["Chart pattern", a.chart_pattern && a.chart_pattern.name && a.chart_pattern.name !== "None" ? esc(a.chart_pattern.name) : "—"]]),
    ];
    $("groups").innerHTML = html.join("");
    $("strip").innerHTML = [["Signal", esc(act), `color:${sigCol}`], ["Last", fmt(last), col(chg)], ["Chg", chg == null ? "—" : sgn(chg), col(chg)], ["RSI", fmt(r, 1)], ["ATR", fmt(at)],
      ["VWAP", vw == null ? "—" : sgn(last - vw), vw == null ? "" : col(last - vw)], ["EMA", e1 == null || e2 == null ? "—" : e1 > e2 ? "Up" : "Down", e1 != null && e2 != null ? col(e1 - e2) : ""],
      ["ST", st.dir[n] == null ? "—" : st.dir[n] === 1 ? "Up" : "Down"], ["Vol", c.volume == null ? "—" : fmtVol(c.volume)], ["Score", a.status === "ok" ? a.score : "—"], ["S1", SR && SR.supports[0] ? fmt(SR.supports[0].price) : "—", "color:var(--green)"], ["R1", SR && SR.resistances[0] ? fmt(SR.resistances[0].price) : "—", "color:var(--red)"], ["Day H/L", `${fmt(d.high, 0)} / ${fmt(d.low, 0)}`]]
      .map(([k, v, s]) => `<div><span>${k}</span><b style="${s || ""}">${v}</b></div>`).join("");
  }

  // ------------------------------------------------------------------ layout
  function applyLayout() {
    const L = S.layout[dev()], ph = Number(S.paneH[dev()]) || 0.2, size = S.dataSize[dev()] || "m";
    document.body.classList.remove("l-side", "l-below", "l-strip", "l-only");
    document.body.classList.add("l-" + L);
    $("layout").value = L;
    const data = $("data");
    data.style.width = L === "side" ? (dev() === "mobile" || dev() === "embed" ? { s: "34%", m: "42%", l: "52%" } : dev() === "landscape" ? { s: "28%", m: "34%", l: "42%" } : { s: "260px", m: "330px", l: "430px" })[size] : "";
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
    $("devNote").textContent = { embed: "(chart inside the Terminal - the chart window and phones keep their own)", mobile: "(phone upright - sideways and computer layouts are kept separately)", landscape: "(phone sideways - upright and computer layouts are kept separately)", desktop: "(computer layout - phones keep their own)" }[dev()];
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
  // v1.20.45: "← Back to chart" at the top, Done at the bottom, a tap outside, or the phone's back key all close the drawer
  const setOpen = () => $("set").classList.contains("open");
  function closeSet(fromHistory) {
    if (!setOpen()) return;
    $("set").classList.remove("open");
    if (!fromHistory && history.state && history.state.oiChartSet) { try { history.back(); } catch { } }
  }
  $("openSet").onclick = () => { renderSettings(); $("set").classList.add("open"); try { history.pushState({ oiChartSet: 1 }, ""); } catch { } };
  $("closeSet").onclick = () => closeSet(false);
  $("backSet").onclick = () => closeSet(false);
  $("set").addEventListener("click", e => { if (e.target.id === "set") closeSet(false); });
  window.addEventListener("popstate", () => closeSet(true));
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeSet(false); });
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
  // ------------------------------------------------------------------ v1.20.46: what the chart shows - index, or the signal's option
  // Auto works like the old Terminal chart: on a WATCH / PENDING / CONFIRMED setup it switches to the option the signal picked
  // (once per setup; it stays on that contract while the side is the same), and goes back to the index after 10 minutes without a
  // setup - 2 minutes when the engine leans the other side. "Index only" never switches.
  let OPT = null, autoIdle = null, lastSide = null;
  // v1.20.90: like the terminal (v1.20.74), no automatic switch to an option while the market is closed - after 15:30 the last
  // setup (e.g. WATCH CE at 15:30) kept the Chart tab on the option while the terminal showed the index price. Market state from
  // /api/status, asked every 5 min; unknown = treated as open, so nothing changes if the call fails.
  let mktOpen = null;
  // v1.20.90: the first analysis can arrive before /api/status answers - so no automatic switch until the state is known, and
  // a switch already made goes back to the index as soon as the answer says closed. If the call fails, treat as open (old behaviour).
  async function refreshMarket() {
    try { const s = await api("/api/status"); mktOpen = !!(s.market && s.market.open); } catch { if (mktOpen === null) mktOpen = true; }
    if (mktOpen === false && OPT && OPT.auto && S.view === "auto") toIndex("Market closed: chart back to the index");
  }
  refreshMarket(); setInterval(refreshMarket, 300000);
  const optQ = () => OPT ? `&instrument_key=${encodeURIComponent(OPT.key)}` : "";
  function showOpt() {
    $("optBar").hidden = !OPT;
    if (OPT) $("optText").innerHTML = `Option chart: <b>${esc(OPT.name)}</b> · premium candles${OPT.why ? " · " + esc(OPT.why) : ""}`;
    document.title = `${OPT ? OPT.name : S.sym} ${S.tf}m · Chart · Option Intelligence`;
  }
  // v1.20.70: the socket sends the charted option's every tick, other strikes once a second - so it must know which one is charted
  function subMsg() { return JSON.stringify({ type: "sub", interval: S.tf, mode: mode(), score_min: scoreMin(), reconfirm: reconfirm(), view: "index", opt_key: OPT ? OPT.key : "" }); }
  function resub() { try { if (ws && ws.readyState === 1) ws.send(subMsg()); } catch { } }
  function toOption(pk, why) {
    OPT = { key: pk.instrument_key, type: pk.type, name: pk.name || `${S.sym} ${pk.strike} ${pk.type}`, why, auto: true };
    autoIdle = null; showOpt(); loadCandles(); resub(); toast(`${why}: chart switched to ${OPT.name}`);
  }
  function toIndex(msg) { if (!OPT) return; OPT = null; autoIdle = null; showOpt(); loadCandles(); resub(); if (msg) toast(msg); }
  function autoView(a) {
    if (S.view !== "auto" || !a) return;
    if (mktOpen === null) return;                               // market state not known yet - decide once /api/status answers
    if (mktOpen === false) {                                   // closed: back to the index, no new switch
      if (OPT && OPT.auto) toIndex("Market closed: chart back to the index");
      return;
    }
    const pk = a.option_pick, lvl = a.final && a.final.level;
    if (pk && pk.instrument_key && ["WATCH", "PENDING", "CONFIRMED"].includes(lvl) && (!pk.symbol || pk.symbol === S.sym)) {
      autoIdle = null;
      if (OPT && OPT.key === pk.instrument_key) return;
      if (OPT && lastSide === pk.type && OPT.type === pk.type) return;      // same setup: stay on the chosen contract
      lastSide = pk.type; toOption(pk, `${lvl} ${pk.type}`); return;
    }
    if (!lvl || lvl === "WAIT") lastSide = null;
    if (!OPT) return;
    const opposite = !!(a.side && a.side !== OPT.type);
    if (!autoIdle) { autoIdle = Date.now(); return; }
    if (Date.now() - autoIdle >= (opposite ? 2 : 10) * 60000) { lastSide = null; toIndex(opposite ? `Engine now leans ${a.side}: chart back to the index` : "No setup for 10 minutes: chart back to the index"); }
  }
  function onAnalysis(a) { A = a; panel(); try { autoView(a); } catch { } }
  $("optBack").onclick = () => { lastSide = OPT ? OPT.type : null; toIndex(""); };

  let loadGen = 0;
  async function loadCandles() {
    const g = ++loadGen;
    $("live").textContent = "Loading candles…";
    try {
      const d = await api(`/api/candles?symbol=${S.sym}&interval=${S.tf}&days=${S.days}${optQ()}`);
      if (g !== loadGen) return;
      cs = prep(d.candles); tick = null;
      VOLINFO = { source: d.volume_source || "", note: d.volume_note || "" };
      if (!OPT && !cs.some(c => c.volume > 0)) {
        const fv = new Map((d.fut_volume || []).map(([t, v]) => [toT(t), +v]));
        cs.forEach(c => { c.volume = fv.has(c.time) ? fv.get(c.time) : null; });           // unknown, not 0
        if (fv.size) VOLINFO.source = "futures (partial)";
      }
      if (!cs.length) { $("live").textContent = "No candles yet"; return; }
      draw(true); renderDrawings();
      $("live").textContent = `${cs.length} candles · ${OPT ? "option premium" : d.volume_source ? "volume: " + d.volume_source : VOLINFO.note ? "volume: see note" : "no volume"}`;
    } catch (e) { $("live").textContent = "Candles unavailable: " + e.message; }
  }
  async function load() {
    const g = loadGen + 1;
    await loadCandles();
    api(`/api/analysis?symbol=${S.sym}&interval=${S.tf}&mode=${mode()}&score_min=${scoreMin()}&reconfirm=${reconfirm()}`).then(a => { if (g === loadGen) onAnalysis(a); }).catch(() => { });
    if (document.visibilityState === "visible" || (ws && ws.readyState <= 1)) connect();   // v1.20.67: a hidden tab connects when shown
  }
  function mergeBars(list) {
    if (!cs.length) return;
    let appended = false;
    for (const b of prep(list)) {
      const j = cs.findIndex((c, i) => i >= cs.length - 6 && c.time === b.time);
      if (!OPT && !(b.volume > 0)) b.volume = j >= 0 ? cs[j].volume : (cs.some(c => c.volume > 0) && !VOLINFO.note ? 0 : null);
      if (j >= 0) cs[j] = b; else if (b.time > cs.at(-1).time) { cs.push(b); appended = true; }
    }
    tick = null;
    draw(false);
    if (appended) C.main.timeScale().scrollToRealTime();
  }
  function applyTick(p) {
    const last = cs.at(-1), now = Date.now() / 1000;
    if (!last || !Number.isFinite(p) || now - last.time >= S.tf * 60) return;          // a new candle comes with the next bars update
    last.close = p; last.high = Math.max(last.high, p); last.low = Math.min(last.low, p); tick = p;
    if (Date.now() - lastTickDraw > 400) {
      lastTickDraw = Date.now();
      try { ser.price.update(S.candle === "line" ? { time: last.time, value: p } : { time: last.time, open: last.open, high: last.high, low: last.low, close: p }); } catch { }
      legend(null); panel();
    }
  }
  let ws = null, wsGen = 0, lastTickDraw = 0, lastMsg = 0;
  function connect() {
    const g = ++wsGen;
    if (ws) { ws.onclose = ws.onmessage = ws.onerror = null; try { ws.close(); } catch { } }
    ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/live?symbol=${S.sym}`);
    ws.onopen = () => { if (g === wsGen) ws.send(subMsg()); };
    ws.onmessage = ev => {
      if (g !== wsGen) return;
      let m; try { m = JSON.parse(ev.data); } catch { return; }
      lastMsg = Date.now();
      if (m.type === "analysis") { if (m.interval === S.tf && m.data) onAnalysis(m.data); return; }
      if (m.type === "bars") { if (!OPT && m.interval === S.tf) { mergeBars(m.candles || []); $("live").textContent = "● Live · " + new Date().toLocaleTimeString("en-IN", { hour12: false }); } return; }
      if (m.type === "index") { if (!OPT && m.index && m.index.fresh && !m.index.rest && cs.length) applyTick(Number(m.index.ltp)); return; }
      if (OPT && Array.isArray(m.ticks)) {             // option ticks ("opt" messages, and the 1-second tick table)
        const t = m.ticks.find(x => x.instrument_key === OPT.key);
        if (t && t.fresh !== false && Number.isFinite(Number(t.ltp))) { applyTick(Number(t.ltp)); $("live").textContent = "● Live option · " + new Date().toLocaleTimeString("en-IN", { hour12: false }); }
      }
    };
    ws.onclose = () => { if (g === wsGen) setTimeout(() => { if (g === wsGen && document.visibilityState === "visible") connect(); }, 4000); };
  }
  let hiddenTimer = null;                 // v1.20.67 bandwidth: no live socket while the Chart tab is off screen for 20 s
  document.addEventListener("visibilitychange", () => {
    clearTimeout(hiddenTimer);
    if (document.visibilityState === "visible") { if (!ws || ws.readyState > 1) connect(); return; }
    hiddenTimer = setTimeout(() => { if (document.visibilityState !== "visible" && ws) { wsGen++; ws.onopen = ws.onclose = ws.onmessage = ws.onerror = null; try { ws.close(); } catch { } ws = null; } }, 20000);
  });
  window.addEventListener("storage", e => { if (/^oi-(score-min|signal-mode)-v2$/.test(e.key || "")) { A = null; if (document.visibilityState === "visible") connect(); } });   // Terminal changed its signal settings
  let lastPoll = 0;
  setInterval(() => {                      // option candles (the socket only pushes index candles), or the index when the socket is quiet
    if (document.visibilityState !== "visible" || !cs.length) return;
    if (window.__oiMarketOpen && !window.__oiMarketOpen() && Date.now() - lastPoll < (window.__oiClosedWait || 900000)) return;   // v1.20.68
    const due = OPT ? Date.now() - lastPoll >= 5000 : Date.now() - lastMsg >= 20000 && Date.now() - lastPoll >= 15000;
    if (!due) return;
    lastPoll = Date.now();
    const key = OPT && OPT.key;
    api(`/api/candles?symbol=${S.sym}&interval=${S.tf}&days=1${optQ()}`).then(d => { if ((OPT && OPT.key) === key) mergeBars((d.candles || []).slice(-4)); }).catch(() => { });
  }, 1000);

  // ------------------------------------------------------------------ landscape: full screen, turned sideways
  async function landscape() {
    const fs = document.fullscreenElement;
    try {
      if (fs) { try { screen.orientation.unlock(); } catch { } await document.exitFullscreen(); $("rotate").textContent = "⟳ Landscape"; return; }
      await document.documentElement.requestFullscreen({ navigationUI: "hide" });
      try { await screen.orientation.lock("landscape"); } catch { toast("Full screen on. Turn the phone sideways (switch on auto-rotate) for landscape."); }
      $("rotate").textContent = "⟲ Exit full screen";
    } catch { toast("This phone does not allow full screen here - switch on auto-rotate and turn the phone sideways."); }
  }
  $("rotate").onclick = landscape;
  document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement) $("rotate").textContent = "⟳ Landscape"; });

  // ------------------------------------------------------------------ toolbar
  $("sym").value = S.sym; $("tf").value = String(S.tf); $("days").value = String(S.days); $("view").value = S.view;
  const reload = () => { S.sym = $("sym").value; S.tf = Number($("tf").value); S.days = Number($("days").value); save(); A = null; OPT = null; lastSide = null; showOpt(); load(); };
  window.addEventListener("storage", e => {                  // the Terminal changed its index / timeframe (any tab, same browser)
    if (e.key !== "oi-settings-v1") return;
    const t = termPick(); let ch = false;
    if (["NIFTY", "BANKNIFTY"].includes(t.sym) && t.sym !== S.sym) { $("sym").value = t.sym; ch = true; }
    if ([1, 3, 5, 15, 30].includes(t.tf) && t.tf !== S.tf) { $("tf").value = String(t.tf); ch = true; }
    if (ch) reload();
  });
  window.addEventListener("message", e => {
    const m = e.origin === location.origin && e.data && e.data.oiChartSync; if (!m) return;
    const sym = String(m.symbol || "").toUpperCase(), tf = Number(m.interval);
    if ((sym === S.sym || !["NIFTY", "BANKNIFTY"].includes(sym)) && (tf === S.tf || ![1, 3, 5, 15, 30].includes(tf))) return;
    if (["NIFTY", "BANKNIFTY"].includes(sym)) $("sym").value = sym;
    if ([1, 3, 5, 15, 30].includes(tf)) $("tf").value = String(tf);
    reload();
  });
  ["sym", "tf", "days"].forEach(id => $(id).onchange = reload);
  $("view").onchange = () => { S.view = $("view").value; save(); if (S.view === "index") toIndex("Index only: the chart stays on the index"); else if (A) autoView(A); };
  if (window.opener || window.self !== window.top) $("back").addEventListener("click", e => { if (window.opener) { e.preventDefault(); try { window.opener.focus(); } catch { } window.close(); } });
  showOpt();
  build();
  load();
})();
