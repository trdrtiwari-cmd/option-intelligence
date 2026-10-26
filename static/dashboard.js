const $ = id => document.getElementById(id);
const IST_OFFSET = 19800;
let summaryBusy = false, graphBusy = false, marketOpen = false, graphChart = null, ceSeries = null, peSeries = null, lastGraph = null, fitKey = null;

function fmt(v, n = 2) { return v === null || v === undefined || !Number.isFinite(Number(v)) ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: n, minimumFractionDigits: n }); }
function pctText(v, n = 2) { return v === null || v === undefined || !Number.isFinite(Number(v)) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(n)}%`; }
function clsPct(v) { return v > 0 ? "green" : v < 0 ? "red" : ""; }
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 4000); }
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
async function api(url) {
  const res = await fetch(url); let data;
  try { data = await res.json(); } catch { data = { detail: await res.text() }; }
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function chip(node, sig) { node.textContent = sig ? sig.label : "—"; node.className = "dc-chip " + (sig ? sig.state : ""); node.title = sig ? sig.detail || "" : ""; }
function unavailableBox(node, block) { clear(node).className = ""; node.appendChild(el("div", "dc-unavail", "UNAVAILABLE — " + (block && block.reason ? block.reason : "no data"))); }
function sym() { return $("symbol").value; }
function symLabel() { return sym() === "BANKNIFTY" ? "Bank Nifty" : "Nifty"; }

// ---------------- summary table ----------------
function renderSummary(d) {
  const rows = [
    ["breadth", "Advance / decline ratio", b => b.status === "OK" ? `${b.advances} up · ${b.declines} down · ratio ${b.ratio_text} (${b.universe})` : ""],
    ["sectors", "Sector heat map", b => b.status === "OK" ? `${b.green} green / ${b.red} red of ${b.count} · avg ${pctText(b.avg_pct)} · best ${b.leader}, worst ${b.laggard}` : ""],
    ["premium", "Option premium % change", b => b.status === "OK" ? `ATM ${fmt(b.atm_strike, 0)}: CE ${pctText(b.ce_pct, 1)} · PE ${pctText(b.pe_pct, 1)}` : ""],
    ["graph", "Option premium graph (CE / PE)", b => b.status === "OK" ? `CE ${b.ce.state || "—"} · PE ${b.pe.state || "—"} (${b.interval}m)` : ""],
    ["zone", `${symLabel()} price — Up / Mid / Down`, b => b.status === "OK" ? `${b.zone} · ${fmt(b.price)} (${pctText(b.change_pct)}) · score ${b.score > 0 ? "+" : ""}${b.score}/5` : ""],
  ];
  const body = clear($("summaryRows"));
  rows.forEach(([key, label, reading], i) => {
    const block = d[key];
    const tr = el("tr");
    tr.appendChild(el("td", "", String(i + 1)));
    tr.appendChild(el("td", "", label));
    tr.appendChild(el("td", "", block.status === "OK" ? reading(block) : (block.reason || "—")));
    const td = el("td", "dc-sig"); const c = el("span", "dc-chip " + block.signal.state, block.signal.label); c.title = block.signal.detail || ""; td.appendChild(c); tr.appendChild(td);
    tr.addEventListener("click", () => { const t = $(key === "graph" ? "card-graph" : "card-" + key); if (t) t.scrollIntoView({ behavior: "smooth", block: "center" }); });
    body.appendChild(tr);
  });
}
// ---------------- final signal + interpretation ----------------
function fillList(id, items, emptyText) {
  const ul = clear($(id));
  (items && items.length ? items : [emptyText]).forEach(t => ul.appendChild(el("li", items && items.length ? "" : "dc-none", t)));
}
function renderFinal(d) {
  const f = d.final, o = d.overall;
  const color = f.state === "BULLISH" ? "var(--green)" : f.state === "BEARISH" ? "var(--red)" : f.state === "UNAVAILABLE" ? "var(--muted)" : "var(--yellow)";
  $("finalAction").textContent = f.action; $("finalAction").style.color = color;
  $("finalLevel").textContent = f.score === null ? f.level : `${f.level} · ${f.agree ?? 0} agree / ${f.oppose ?? 0} oppose of ${f.available} checks with data`;
  const ft = f.timing;
  $("finalTime").textContent = ft ? `Signal since ${ft.since_text} · ${ft.age_minutes < 1 ? "just now" : Math.round(ft.age_minutes) + " min ago"}` +
    (ft.exact ? "" : ` (est. — watching since ${ft.observed_from}; may be older)`) +
    (ft.price_at_change ? ` · index ${Number(ft.price_at_change).toLocaleString("en-IN", {maximumFractionDigits: 2})} then` : "") : "";
  $("finalScore").textContent = f.score === null ? "—" : (f.score > 0 ? "+" : "") + f.score.toFixed(0);
  $("finalScore").style.color = color;
  $("finalMarker").style.left = f.score === null ? "50%" : Math.max(0, Math.min(100, (f.score + 100) / 2)) + "%";
  $("finalBox").className = "panel dc-final " + f.state;
  $("finalHeadline").textContent = f.headline;
  fillList("finalReasons", f.reasons, "No check clearly supports a direction.");
  fillList("finalAgainst", f.against, "Nothing disagrees.");
  fillList("finalCautions", f.cautions, "No extra cautions.");
  fillList("finalPlan", f.plan, "No action — nothing to prepare.");
  fillList("finalInval", f.invalidation, "—");
  const box = clear($("finalContrib"));
  f.contributions.forEach(c => {
    const row = el("div", "dc-check"); const left = el("div"); left.appendChild(el("span", "", c.name)); left.appendChild(el("small", "", `weight ${c.weight}`)); row.appendChild(left);
    const right = el("div"); right.style.textAlign = "right"; const chipEl = el("span", "dc-chip " + c.state, c.label); right.appendChild(chipEl);
    right.appendChild(el("small", "", (c.points === null ? "no data" : (c.points > 0 ? "+" : "") + c.points + " pts") + (c.timing ? ` · since ${c.timing.since_text.slice(0, 5)}${c.timing.exact ? "" : "*"}` : ""))); row.appendChild(right); box.appendChild(row);
  });
  $("finalDisclaimer").textContent = f.disclaimer;
  $("cntBull").textContent = `${o.bull} bullish`; $("cntBear").textContent = `${o.bear} bearish`; $("cntNeu").textContent = `${o.neutral + o.caution} neutral / caution`;
}

// ---------------- 1. breadth ----------------
function renderBreadth(b) {
  const body = $("breadthBody"); chip($("breadthChip"), b.signal);
  if (b.status !== "OK") { unavailableBox(body, b); $("breadthSub").textContent = "—"; return; }
  body.className = ""; clear(body);
  $("breadthSub").textContent = `${b.universe} · ${b.quoted} stocks quoted${b.missing ? ` (${b.missing} without quote)` : ""} · ${b.universe_source}`;
  const bar = el("div", "dc-bar"); [["a", b.advances], ["u", b.unchanged], ["d", b.declines]].forEach(([c, n]) => { const i = el("i", c); i.style.width = (n / b.quoted * 100) + "%"; i.title = n; bar.appendChild(i); });
  body.appendChild(bar);
  const big = el("div", "dc-big");
  [["Advances", b.advances, "green"], ["Declines", b.declines, "red"], ["Unchanged", b.unchanged, ""], ["A/D ratio", b.ratio_text, ""]].forEach(([k, v, c]) => { const d = el("div"); d.appendChild(el("span", "", k)); d.appendChild(el("strong", c, String(v))); big.appendChild(d); });
  body.appendChild(big);
  const two = el("div", "dc-two");
  [["Top gainers", b.gainers], ["Top losers", b.losers]].forEach(([title, list]) => {
    const col = el("div"); col.appendChild(el("h4", "", title)); const l = el("div", "dc-list");
    list.forEach(r => { const row = el("div"); row.appendChild(el("span", "", r.symbol)); row.appendChild(el("b", clsPct(r.pct), pctText(r.pct))); l.appendChild(row); });
    col.appendChild(l); two.appendChild(col);
  });
  body.appendChild(two);
}

// ---------------- 2. sectors ----------------
function heatStyle(pct) {
  if (pct === null || pct === undefined) return "background:#101b2a;color:#91a4ba";
  const a = Math.min(Math.abs(pct) / 2, 1) * 0.75 + 0.1;
  return pct >= 0 ? `background:rgba(37,197,138,${a});color:#fff` : `background:rgba(255,102,122,${a});color:#fff`;
}
function renderSectors(s) {
  const body = $("sectorBody"); chip($("sectorChip"), s.signal);
  if (!s.tiles) { unavailableBox(body, s); return; }
  body.className = ""; clear(body);
  const grid = el("div", "dc-heat");
  s.tiles.forEach(t => { const d = el("div", "dc-tile"); d.setAttribute("style", heatStyle(t.pct)); d.appendChild(el("b", "", t.name)); d.appendChild(el("strong", "", pctText(t.pct))); d.appendChild(el("small", "", t.ltp ? fmt(t.ltp) : "no quote")); grid.appendChild(d); });
  body.appendChild(grid);
  if (s.status === "OK") $("sectorSub").textContent = `${s.green} green · ${s.red} red · avg ${pctText(s.avg_pct)} · % change vs previous close`;
  else { $("sectorSub").textContent = s.reason; }
  if (s.unresolved && s.unresolved.length) body.appendChild(el("div", "sub small", "Index not found in the instrument file: " + s.unresolved.join(", ")));
}

// ---------------- 3. option premium % change ----------------
function renderPremium(p) {
  const body = $("premiumBody"); chip($("premiumChip"), p.signal);
  if (p.status !== "OK") { unavailableBox(body, p); $("premiumSub").textContent = "—"; return; }
  body.className = ""; clear(body);
  $("premiumSub").textContent = `Expiry ${p.expiry} · spot ${fmt(p.spot)} · % vs previous close · chain snapshot ${fmt(p.snapshot_age_seconds, 0)}s old`;
  const big = el("div", "dc-big");
  [["ATM strike", fmt(p.atm_strike, 0), ""], ["ATM call", pctText(p.ce_pct, 1), clsPct(p.ce_pct)], ["ATM put", pctText(p.pe_pct, 1), clsPct(p.pe_pct)], ["3-strike avg CE / PE", `${pctText(p.ce_avg3_pct, 1)} / ${pctText(p.pe_avg3_pct, 1)}`, ""]].forEach(([k, v, c]) => { const d = el("div"); d.appendChild(el("span", "", k)); d.appendChild(el("strong", c, v)); big.appendChild(d); });
  body.appendChild(big);
  const wrap = el("div", "table-wrap"); const tbl = el("table", "dc-opt"); const thead = el("thead"); const hr = el("tr");
  ["Strike", "CE LTP", "CE %", "PE LTP", "PE %"].forEach(h => hr.appendChild(el("th", "", h))); thead.appendChild(hr); tbl.appendChild(thead);
  const tb = el("tbody");
  p.table.slice().reverse().forEach(r => {
    const tr = el("tr", r.atm ? "atm" : ""); tr.appendChild(el("td", "", fmt(r.strike, 0) + (r.atm ? " · ATM" : "")));
    tr.appendChild(el("td", "", fmt(r.ce.ltp))); tr.appendChild(el("td", clsPct(r.ce.pct), pctText(r.ce.pct, 1)));
    tr.appendChild(el("td", "", fmt(r.pe.ltp))); tr.appendChild(el("td", clsPct(r.pe.pct), pctText(r.pe.pct, 1))); tb.appendChild(tr);
  });
  tbl.appendChild(tb); wrap.appendChild(tbl); body.appendChild(wrap);
  body.appendChild(el("div", "sub small", "Signal: " + p.signal.detail));
}

// ---------------- 5. index zone ----------------
function renderZone(z) {
  $("zoneTitle").textContent = `5 · ${symLabel()} price — Up / Mid / Down`;
  const body = $("zoneBody"); chip($("zoneChip"), z.signal);
  if (z.status !== "OK") { unavailableBox(body, z); $("zoneSub").textContent = "—"; return; }
  body.className = ""; clear(body);
  $("zoneSub").textContent = `Session ${z.session} · five checks, each +1 / 0 / −1 · UP ≥ +3, DOWN ≤ −3`;
  const big = el("div", "dc-big");
  [["Price", fmt(z.price), ""], ["Change", pctText(z.change_pct), clsPct(z.change_pct)], ["Zone", z.zone, z.zone === "UP" ? "green" : z.zone === "DOWN" ? "red" : ""], ["Score", (z.score > 0 ? "+" : "") + z.score + " / 5", ""]].forEach(([k, v, c]) => { const d = el("div"); d.appendChild(el("span", "", k)); d.appendChild(el("strong", c, v)); big.appendChild(d); });
  body.appendChild(big);
  const track = el("div", "dc-zone-track"); [["dn", "DOWN"], ["md", "MID"], ["up", "UP"]].forEach(([c, t]) => track.appendChild(el("span", c, t)));
  const marker = el("i"); marker.style.left = Math.max(0, Math.min(100, (z.score + 5) / 10 * 100)) + "%"; track.appendChild(marker); body.appendChild(track);
  z.checks.forEach(c => {
    const row = el("div", "dc-check"); const left = el("div"); left.appendChild(el("span", "", c.label)); left.appendChild(el("small", "", c.detail)); row.appendChild(left);
    row.appendChild(el("b", c.score > 0 ? "green" : c.score < 0 ? "red" : "", c.score > 0 ? "▲ +1" : c.score < 0 ? "▼ −1" : "— 0")); body.appendChild(row);
  });
  const L = z.levels;
  body.appendChild(el("div", "sub small", `Day range ${fmt(L.day_low)} – ${fmt(L.day_high)} · price at ${fmt(z.day_range_pos, 0)}% of range · ${L.avg_basis} ${fmt(L.avg_price)}`));
}

// ---------------- 4. premium graph ----------------
function istFmt(time) { const d = new Date((time + IST_OFFSET) * 1000); return String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0"); }
function initGraph() {
  const node = $("graph");
  graphChart = LightweightCharts.createChart(node, { width: node.clientWidth, height: node.clientHeight, layout: { background: { color: "#121d2d" }, textColor: "#91a4ba" }, grid: { vertLines: { color: "#1e2d40" }, horzLines: { color: "#1e2d40" } }, rightPriceScale: { borderColor: "#27384d" }, timeScale: { borderColor: "#27384d", timeVisible: true, secondsVisible: false, tickMarkFormatter: istFmt }, localization: { timeFormatter: t => istFmt(t) + " IST" }, crosshair: { mode: LightweightCharts.CrosshairMode.Normal } });
  ceSeries = graphChart.addLineSeries({ color: "#25c58a", lineWidth: 2, priceLineVisible: true, lastValueVisible: true, title: "CE" });
  peSeries = graphChart.addLineSeries({ color: "#ff667a", lineWidth: 2, priceLineVisible: true, lastValueVisible: true, title: "PE" });
  window.addEventListener("resize", () => graphChart.applyOptions({ width: node.clientWidth, height: node.clientHeight }));
}
function seriesData(leg, mode) {
  const cs = leg.candles || []; if (!cs.length) return [];
  const base = cs[0].open;
  return cs.map(c => ({ time: c.time, value: mode === "pct" ? (base > 0 ? (c.close - base) / base * 100 : 0) : c.close }));
}
function renderGraph(g) {
  lastGraph = g; chip($("graphChip"), g.signal);
  if (g.status !== "OK") { ceSeries.setData([]); peSeries.setData([]); $("graphSub").textContent = "—"; $("graphNote").textContent = "UNAVAILABLE — " + (g.reason || "no data"); return; }
  const mode = $("graphMode").value;
  ceSeries.setData(seriesData(g.ce, mode)); peSeries.setData(seriesData(g.pe, mode));
  const fmtLeg = (side, l) => `${side} ${fmt(l.strike, 0)}`;
  $("legendCe").textContent = `━ ${fmtLeg("CE", g.ce)} (${pctText(g.ce.from_open_pct, 1)} from open)`;
  $("legendPe").textContent = `━ ${fmtLeg("PE", g.pe)} (${pctText(g.pe.from_open_pct, 1)} from open)`;
  $("graphSub").textContent = `Expiry ${g.expiry} · ATM ${fmt(g.atm_strike, 0)} · ${g.interval}-minute closes · session ${g.session}`;
  $("graphNote").textContent = g.signal.detail + ". Strike follows the live ATM each refresh; use the Premium strike selector to chart OTM / ITM legs.";
  const key = [sym(), g.expiry, g.interval, g.offset, g.ce.strike, g.pe.strike, mode].join("|");
  if (fitKey !== key) { fitKey = key; graphChart.timeScale().fitContent(); }
}

// ---------------- ATR-based underlying risk reference ----------------
let atrRiskBusy = false;
function wilderAtr(candles, period=14) {
  const rows=(candles||[]).map(c=>({t:Date.parse(c.time),h:Number(c.high),l:Number(c.low),c:Number(c.close)}))
    .filter(x=>Number.isFinite(x.t)&&[x.h,x.l,x.c].every(Number.isFinite)).sort((a,b)=>a.t-b.t);
  // Exclude a still-forming candle so the ATR and reference levels do not flicker/repaint.
  const now=Date.now();
  while(rows.length&&rows[rows.length-1].t+Number($("interval").value||5)*60000>now) rows.pop();
  if(rows.length<period+1)return null;
  const tr=[];
  for(let i=1;i<rows.length;i++)tr.push(Math.max(rows[i].h-rows[i].l,Math.abs(rows[i].h-rows[i-1].c),Math.abs(rows[i].l-rows[i-1].c)));
  if(tr.length<period)return null;
  let a=tr.slice(0,period).reduce((s,v)=>s+v,0)/period;
  for(let i=period;i<tr.length;i++)a=(a*(period-1)+tr[i])/period;
  return {atr:a, close:rows[rows.length-1].c, lastTime:rows[rows.length-1].t};
}
async function loadAtrRisk(){
  if(atrRiskBusy)return;atrRiskBusy=true;
  try{
    const data=await api(`/api/candles?symbol=${sym()}&interval=${$("interval").value}&days=3`);
    const r=wilderAtr(data.candles||[]);
    if(!r){$("atrRiskNote").textContent="ATR unavailable: not enough closed candles. No levels are being fabricated.";return}
    let entry=r.close, source="last closed candle";
    try{const q=await api(`/api/index-ltp?symbol=${sym()}`);if(q.ltp!=null&&Number.isFinite(Number(q.ltp))){entry=Number(q.ltp);source=q.status==="LIVE_TICK"?"live index tick":"latest REST quote"}}
    catch{}
    const risk=r.atr, target=1.5*r.atr;
    $("atrRiskEntry").textContent=fmt(entry);$("atrRiskAtr").textContent=fmt(r.atr);
    $("atrCeSl").textContent=fmt(entry-risk);$("atrCeTarget").textContent=fmt(entry+target);
    $("atrPeSl").textContent=fmt(entry+risk);$("atrPeTarget").textContent=fmt(entry-target);
    const f=lastSummary?.final;
    const state=f?.state||"WAIT";
    $("atrRiskChip").textContent=state==="BULLISH"?"BULLISH":state==="BEARISH"?"BEARISH":"WAIT";
    $("atrRiskChip").className="dc-chip "+(state==="BULLISH"?"BULLISH":state==="BEARISH"?"BEARISH":"NEUTRAL");
    $("atrRiskNote").textContent=`ATR uses closed ${$("interval").value}-minute ${sym()} candles; reference price source: ${source}. Stop = 1.0× ATR; target = 1.5× ATR. These are index-point levels, not option premium prices. Use the selected CE/PE's own live quote and premium ATR before trading.`;
  }catch(e){$("atrRiskNote").textContent="ATR risk reference unavailable: "+e.message}
  finally{atrRiskBusy=false}
}
// ---------------- data loading ----------------
function qs() { return `symbol=${sym()}&universe=${$("universe").value}&expiry=${$("expiry").value}&interval=${$("interval").value}&offset=${$("offset").value}`; }
let lastSummary = null;
async function loadSummary() {
  if (summaryBusy) return; summaryBusy = true;
  const btn = $("refreshBtn"); btn.disabled = true;
  try {
    const d = await api("/api/dashboard/summary?" + qs()); lastSummary = d;
    marketOpen = !!(d.market && d.market.open);
    $("marketStatus").textContent = marketOpen ? "Market open (IST)" : "Market closed (IST)"; $("marketStatus").className = marketOpen ? "pill live" : "pill muted";
    renderBreadth(d.breadth); renderSectors(d.sectors); renderPremium(d.premium); renderZone(d.zone); renderGraph(d.graph);
    renderSummary(d); renderFinal(d);
    $("dcNote").textContent = d.note;
    loadAtrRisk();
    $("lastUpdated").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (e) { toast("Dashboard load failed: " + e.message); $("lastUpdated").textContent = "Load failed: " + e.message; }
  finally { summaryBusy = false; btn.disabled = false; }
}
let timer = null;
function startTimer() {
  clearInterval(timer);
  const sec = Number($("refreshEvery").value) || 10;
  try { localStorage.setItem("oi-dc-refresh", String(sec)); } catch {}
  timer = setInterval(() => { if (document.visibilityState === "visible") loadSummary(); }, sec * 1000);
}
$("symbol").addEventListener("change", () => { $("universe").value = sym() === "BANKNIFTY" ? "niftybank" : "nifty50"; lastGraph = null; fitKey = null; loadSummary(); });
["universe", "expiry", "offset", "interval"].forEach(id => $(id).addEventListener("change", () => { if (id !== "universe") { lastGraph = null; fitKey = null; } loadSummary(); }));
$("graphMode").addEventListener("change", () => { if (lastGraph) renderGraph(lastGraph); });
$("refreshBtn").addEventListener("click", loadSummary);
$("refreshEvery").addEventListener("change", startTimer);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") loadSummary(); });
try { const saved = localStorage.getItem("oi-dc-refresh"); if (saved && [...$("refreshEvery").options].some(o => o.value === saved)) $("refreshEvery").value = saved; } catch {}
initGraph(); startTimer(); loadSummary();
