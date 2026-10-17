let chart, candleSeries, ema9Series, ema21Series, currentCandles=[];
const $ = id => document.getElementById(id);
function toast(msg){const el=$("toast");el.textContent=msg;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),4500)}
function fmt(v,n=2){return v===null||v===undefined||!Number.isFinite(Number(v))?"—":Number(v).toLocaleString("en-IN",{maximumFractionDigits:n,minimumFractionDigits:n})}
function getSymbol(){return $("symbol").value}
function getInterval(){return Number($("interval").value)}
function initChart(){
  const el=$("chart");
  chart=LightweightCharts.createChart(el,{width:el.clientWidth,height:el.clientHeight,layout:{background:{color:"#121d2d"},textColor:"#91a4ba"},grid:{vertLines:{color:"#1e2d40"},horzLines:{color:"#1e2d40"}},rightPriceScale:{borderColor:"#27384d"},timeScale:{borderColor:"#27384d",timeVisible:true,secondsVisible:false},crosshair:{mode:LightweightCharts.CrosshairMode.Normal}});
  candleSeries=chart.addCandlestickSeries({upColor:"#25c58a",downColor:"#ff667a",borderUpColor:"#25c58a",borderDownColor:"#ff667a",wickUpColor:"#25c58a",wickDownColor:"#ff667a"});
  ema9Series=chart.addLineSeries({color:"#5aa7ff",lineWidth:2,priceLineVisible:false});
  ema21Series=chart.addLineSeries({color:"#f3c969",lineWidth:2,priceLineVisible:false});
  window.addEventListener("resize",()=>chart.applyOptions({width:el.clientWidth,height:el.clientHeight}));
}
function epochSeconds(t){let d=Date.parse(t);return Math.floor(d/1000)}
function toChartTime(t){return epochSeconds(t)}
function calcEMA(vals,n){const a=2/(n+1),out=[];vals.forEach((v,i)=>out.push(i===0?v:a*v+(1-a)*out[i-1]));return out}
function renderCandles(candles){
  currentCandles=candles||[];
  const valid=currentCandles.map(c=>({time:toChartTime(c.time),open:+c.open,high:+c.high,low:+c.low,close:+c.close,volume:+c.volume||0})).filter(c=>Number.isFinite(c.time)&&Number.isFinite(c.close)).sort((a,b)=>a.time-b.time);
  // Avoid duplicate timestamps that can cause chart series errors.
  const unique=[];for(const c of valid){if(!unique.length||unique[unique.length-1].time!==c.time)unique.push(c)}
  candleSeries.setData(unique);
  const closes=unique.map(c=>c.close);
  ema9Series.setData(unique.map((c,i)=>({time:c.time,value:calcEMA(closes,9)[i]})));
  ema21Series.setData(unique.map((c,i)=>({time:c.time,value:calcEMA(closes,21)[i]})));
  chart.timeScale().fitContent();
  if(unique.length){$("lastPrice").textContent=fmt(unique.at(-1).close);$("chartSubtitle").textContent=`${getSymbol()==="NIFTY"?"NIFTY 50":"BANK NIFTY"} · ${getInterval()}-minute OHLC`; }
}
async function api(url){
  const res=await fetch(url);let data;try{data=await res.json()}catch{data={detail:await res.text()}}
  if(!res.ok)throw new Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail||data));
  return data;
}
async function refresh(){
  $("refreshBtn").disabled=true;$("refreshBtn").textContent="Loading…";
  try{
    const sym=getSymbol(),interval=getInterval();
    const [c,a]=await Promise.all([
      api(`/api/candles?symbol=${sym}&interval=${interval}&days=5`),
      api(`/api/analysis?symbol=${sym}&interval=${interval}`)
    ]);
    renderCandles(c.candles);
    $("lastUpdated").textContent=`Updated ${new Date().toLocaleTimeString()}`;
    $("score").textContent=a.status==="ok"?`${a.score}/100`:"—";
    $("regime").textContent=a.status==="ok"?(a.sideways?"SIDEWAYS":a.bull_score>a.bear_score?"BULLISH":"BEARISH"):"—";
    $("decision").textContent=a.decision||"WAIT";
    $("bigDecision").textContent=a.decision||"WAIT";
    $("bullScore").textContent=a.bull_score??"—";$("bearScore").textContent=a.bear_score??"—";
    $("ema9").textContent=fmt(a.ema9);$("ema21").textContent=fmt(a.ema21);$("vwap").textContent=fmt(a.vwap_approx);
    $("rsi").textContent=fmt(a.rsi);$("atr").textContent=fmt(a.atr);$("relVol").textContent=a.relative_volume===undefined?"—":`${fmt(a.relative_volume)}x`;
    $("breakHigh").textContent=fmt(a.breakout_high);$("breakLow").textContent=fmt(a.breakdown_low);
    $("analysisNote").textContent=a.note||a.message||"Analysis loaded.";
    $("signalExplanation").textContent=a.note||a.message||"Confirm breakout and retest before any trade.";
    if(a.sideways){$("regime").style.color="var(--yellow)"}else{$("regime").style.color=a.bull_score>a.bear_score?"var(--green)":"var(--red)"}
  }catch(e){toast("Data load failed: "+e.message);$("analysisNote").textContent=e.message}
  finally{$("refreshBtn").disabled=false;$("refreshBtn").textContent="Refresh data"}
}
async function checkStatus(){
  try{const s=await api("/api/status");const el=$("apiStatus");if(s.token_configured){el.textContent="Upstox token configured";el.className="pill ok"}else{el.textContent="Upstox setup required";el.className="pill bad"}}
  catch{$("apiStatus").textContent="Backend unavailable"}
}
function renderChain(data){
  const rows=data.data||data.chain||[];
  const body=$("chainRows");body.innerHTML="";
  if(!rows.length){body.innerHTML='<tr><td colspan="8" class="empty">No chain rows returned. Check expiry and API permissions.</td></tr>';return}
  rows.forEach(r=>{
    const call=r.call_options||{},put=r.put_options||{},cm=call.market_data||{},pm=put.market_data||{};
    const tr=document.createElement("tr");
    [fmt(r.strike_price,0),fmt(cm.ltp),fmt(cm.oi,0),fmt(r.pcr,3),fmt(pm.oi,0),fmt(pm.ltp)].forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.appendChild(td)});
    const ceTd=document.createElement("td"), peTd=document.createElement("td");
    const ceBtn=document.createElement("button");ceBtn.type="button";ceBtn.className="chain-select";ceBtn.textContent="Use CE";
    ceBtn.disabled=!call.instrument_key || !(Number(cm.ltp)>0);
    ceBtn.addEventListener("click",()=>selectPaperContract("CE",r.strike_price,call.instrument_key,cm.ltp,r.expiry||$("expiry").value,ceBtn));
    const peBtn=document.createElement("button");peBtn.type="button";peBtn.className="chain-select pe";peBtn.textContent="Use PE";
    peBtn.disabled=!put.instrument_key || !(Number(pm.ltp)>0);
    peBtn.addEventListener("click",()=>selectPaperContract("PE",r.strike_price,put.instrument_key,pm.ltp,r.expiry||$("expiry").value,peBtn));
    ceTd.appendChild(ceBtn);peTd.appendChild(peBtn);tr.appendChild(ceTd);tr.appendChild(peTd);
    body.appendChild(tr);
  });
  $("chainStatus").textContent=`Loaded ${rows.length} rows. Select Use CE/Use PE to prepare a paper trade. Prices reflect the API response time.`;
}
async function loadContracts(){
  try{
    const data=await api(`/api/option-contracts?symbol=${getSymbol()}`);
    const rows=data.data||[];
    const dates=[...new Set(rows.map(x=>x.expiry).filter(Boolean))].sort();
    if(!dates.length){toast("No expiry dates returned.");return}
    const select=$("expiry");select.innerHTML="";
    dates.forEach(d=>{const op=document.createElement("option");op.value=d;op.textContent=d;select.appendChild(op)});
    toast(`Loaded ${dates.length} expiry dates.`);
  }catch(e){toast("Could not load expiries: "+e.message)}
}
async function loadChain(){
  if(!$("expiry").value){await loadContracts();if(!$("expiry").value)return}
  try{const data=await api(`/api/option-chain?symbol=${getSymbol()}&expiry_date=${encodeURIComponent($("expiry").value)}`);renderChain(data)}
  catch(e){toast("Option chain failed: "+e.message)}
}
$("refreshBtn").addEventListener("click",refresh);
$("symbol").addEventListener("change",()=>{refresh();$("chainRows").innerHTML='<tr><td colspan="6" class="empty">Load option chain for this underlying.</td></tr>'});
$("interval").addEventListener("change",refresh);
$("loadContracts").addEventListener("click",loadContracts);
$("loadChain").addEventListener("click",loadChain);
$("chartView").addEventListener("change",()=>{if($("chartView").value==="option")toast("Option chart requires selecting a contract instrument key; use the option chain. This control is reserved for the next build.");else refresh()});
if(new URLSearchParams(location.search).get("connected")==="1")toast("Upstox OAuth completed for this running instance. Add a durable token method before production use.");
initChart();checkStatus();refresh();
setInterval(()=>{if(document.visibilityState==="visible")refresh()},60000);
let liveSocket=null, lastDerivatives=null;
function renderLiveTicks(ticks){
  const body=$("liveTicksRows");
  if(!ticks||!ticks.length){body.innerHTML='<tr><td colspan="7" class="empty">No fresh WebSocket ticks yet. Verify API token, SDK subscription and market hours.</td></tr>';return}
  body.innerHTML="";
  ticks.slice().sort((a,b)=>(a.strike||0)-(b.strike||0)).forEach(t=>{
    const tr=document.createElement("tr");
    const fresh=!!t.fresh;
    const vals=[t.trading_symbol||t.instrument_key||"—",t.type||"—",fmt(t.strike,0),fmt(t.ltp),fmt(t.oi,0),fmt(t.iv),t.age_seconds===undefined?"—":`${fmt(t.age_seconds,1)}s`];
    vals.forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===6)td.className=fresh?"fresh-tick":"stale-tick";tr.appendChild(td)});
    body.appendChild(tr);
  });
}
function connectLiveSocket(){
  if(liveSocket){try{liveSocket.close()}catch{}}
  const proto=location.protocol==="https:"?"wss:":"ws:";
  liveSocket=new WebSocket(`${proto}//${location.host}/ws/live?symbol=${encodeURIComponent(getSymbol())}`);
  liveSocket.onopen=()=>{$("streamStatus").textContent="CONNECTED";$("streamStatus").className="pill ok"};
  liveSocket.onmessage=ev=>{try{const m=JSON.parse(ev.data);const ticks=(m.ticks||[]).map(t=>({...t,fresh:!!t.fresh}));renderLiveTicks(ticks);if(m.stream==="LIVE"){$("streamStatus").textContent="LIVE TICKS";$ ("streamStatus").className="pill live"}else{$("streamStatus").textContent=m.stream||"CONNECTING";$ ("streamStatus").className="pill stale"}if(m.error)$ ("streamError").textContent=m.error;else if(ticks.length)$ ("streamError").textContent="WebSocket feed connected";}catch{}}
  liveSocket.onerror=()=>{$("streamStatus").textContent="UNAVAILABLE";$ ("streamStatus").className="pill bad"};
  liveSocket.onclose=()=>{if(document.visibilityState==="visible")setTimeout(connectLiveSocket,4000)};
}
async function refreshDerivatives(){
  try{
    const expiry=$("expiry").value||"current_week";
    const d=await api(`/api/live-derivatives?symbol=${getSymbol()}&expiry_date=${encodeURIComponent(expiry)}`);lastDerivatives=d;
    $("livePCR").textContent=typeof d.pcr==="number"?fmt(d.pcr,4):"UNAVAILABLE";
    $("liveCallOI").textContent=typeof d.total_call_oi==="number"?fmt(d.total_call_oi,0):"UNAVAILABLE";
    $("livePutOI").textContent=typeof d.total_put_oi==="number"?fmt(d.total_put_oi,0):"UNAVAILABLE";
    $("liveIV").textContent=typeof d.iv_average==="number"?`${fmt(d.iv_average)}%`:"UNAVAILABLE";
    $("pcrStatus").textContent=d.pcr!=="UNAVAILABLE"?`REST snapshot · ${d.expiry}`:"No valid same-expiry PCR";
    $("oiStatus").textContent=d.oi_status||"UNAVAILABLE";$("ivStatus").textContent=d.iv_status||"UNAVAILABLE";
    $("tickAge").textContent=d.websocket_last_tick_age_seconds===null?"—":`${fmt(d.websocket_last_tick_age_seconds,1)}s`;
    $("streamError").textContent=d.websocket_error||d.note||"Waiting for ticks";
    $("derivTimestamp").textContent=`Snapshot timestamp: ${d.rest_snapshot_timestamp||d.timestamp||"UNAVAILABLE"} · expiry: ${d.expiry||"UNAVAILABLE"} · WS: ${d.websocket||"UNAVAILABLE"}`;
    if(d.websocket==="LIVE"){$("streamStatus").textContent="LIVE TICKS";$ ("streamStatus").className="pill live"}
    else if(d.websocket_error){$("streamStatus").textContent="UNAVAILABLE";$ ("streamStatus").className="pill bad"}
    renderLiveTicks((d.live_near_atm||[]).map(t=>({...t,age_seconds:d.websocket_last_tick_age_seconds,fresh:d.websocket_last_tick_age_seconds!==null&&d.websocket_last_tick_age_seconds<=15})));
  }catch(e){$("livePCR").textContent="UNAVAILABLE";$("liveCallOI").textContent="UNAVAILABLE";$("livePutOI").textContent="UNAVAILABLE";$("liveIV").textContent="UNAVAILABLE";$("streamStatus").textContent="UNAVAILABLE";$("streamError").textContent=e.message;$("derivTimestamp").textContent="Snapshot timestamp: UNAVAILABLE"}
}
const originalLoadContracts=loadContracts;
loadContracts=async function(){
  try{
    const data=await api(`/api/option-contracts?symbol=${getSymbol()}&expiry_date=current_week`);
    const rows=data.data||[];const dates=[...new Set(rows.map(x=>x.expiry).filter(Boolean))].sort();
    const select=$("expiry");const previous=select.value;select.innerHTML="";
    [["current_week","Current week (auto)"],["next_week","Next week (auto)"],["current_month","Current month (auto)"],["next_month","Next month (auto)"]].forEach(([v,label])=>{const op=document.createElement("option");op.value=v;op.textContent=label;select.appendChild(op)});
    dates.forEach(d=>{const op=document.createElement("option");op.value=d;op.textContent=d;select.appendChild(op)});
    if([...select.options].some(o=>o.value===previous))select.value=previous;else select.value="current_week";
    toast(`Loaded current-week contracts; ${dates.length} explicit expiry date(s) found.`);await refreshDerivatives();
  }catch(e){toast("Could not load expiries: "+e.message)}
};
$("symbol").addEventListener("change",()=>{connectLiveSocket();refreshDerivatives()});
$("expiry").addEventListener("change",()=>{refreshDerivatives();loadChain()});
$("loadChain").addEventListener("click",refreshDerivatives);
connectLiveSocket();refreshDerivatives();setInterval(()=>{if(document.visibilityState==="visible")refreshDerivatives()},15000);
// Rebind the expiry loader after replacing its initial snapshot-only implementation.
$("loadContracts").removeEventListener("click", originalLoadContracts);
$("loadContracts").addEventListener("click", loadContracts);


// ---------------- Local-browser paper trading ----------------
// These are simulated positions only. They never place broker orders.
const PAPER_STORAGE_KEY = "option-intelligence-paper-trades-v1";
let paperSelected = null;
let paperAtr = null;
let paperRefreshBusy = false;
function paperRead(){try{return JSON.parse(localStorage.getItem(PAPER_STORAGE_KEY)||"[]")}catch{return []}}
function paperSave(trades){localStorage.setItem(PAPER_STORAGE_KEY,JSON.stringify(trades))}
function paperMoney(v){return v===null||v===undefined||!Number.isFinite(Number(v))?"—":"₹"+Number(v).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2})}
function paperTime(v){if(!v)return "—";try{return new Date(v).toLocaleString()}catch{return String(v)}}
function paperNum(id){const v=Number($(id).value);return Number.isFinite(v)&&v>0?v:null}
function paperSelectedLabel(){return paperSelected?`${getSymbol()} ${paperSelected.expiry} ${paperSelected.strike} ${paperSelected.type}`:"No option selected"}
function selectPaperContract(type,strike,instrumentKey,ltp,expiry,button){
  if(!instrumentKey){toast("This option has no instrument key in the chain response.");return}
  paperSelected={symbol:getSymbol(),type,strike:Number(strike),instrument_key:instrumentKey,expiry:String(expiry||$("expiry").value),ltp:Number(ltp)||null,quoteStatus:"CHAIN_SNAPSHOT"};
  paperAtr=null;$("paperAtrValue").textContent="UNAVAILABLE";
  $("paperSelectedContract").textContent=`Selected: ${paperSelectedLabel()} · chain LTP ${paperMoney(paperSelected.ltp)} (snapshot)`;
  $("paperEntry").value=paperSelected.ltp||"";
  $("paperBuyBtn").disabled=false;
  document.querySelectorAll(".chain-select").forEach(b=>b.closest("tr")?.classList.remove("chain-selected"));
  if(button)button.closest("tr")?.classList.add("chain-selected");
  paperUpdateMode();paperUpdatePreview();
  paperFetchQuote(true).then(()=>paperUpdatePreview()).catch(()=>{});
  toast(`Selected ${paperSelectedLabel()} for paper trading.`);
}
function paperUpdateMode(){
  const atrMode=$("paperRiskMode").value==="atr";
  $("paperAtrSettings").hidden=!atrMode;$("paperManualSettings").hidden=atrMode;
  paperUpdatePreview();
}
function paperCalcAtrFromCandles(candles){
  if(!Array.isArray(candles)||candles.length<15)return null;
  const cs=candles.slice().sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  const trs=[];
  for(let i=1;i<cs.length;i++){
    const h=Number(cs[i].high),l=Number(cs[i].low),pc=Number(cs[i-1].close);
    if([h,l,pc].every(Number.isFinite))trs.push(Math.max(h-l,Math.abs(h-pc),Math.abs(l-pc)));
  }
  if(trs.length<14)return null;
  return trs.slice(-14).reduce((a,b)=>a+b,0)/14;
}
async function paperCalculateAtr(){
  if(!paperSelected){toast("पहले option chain से CE या PE select करें.");return}
  $("paperCalcAtr").disabled=true;$("paperCalcAtr").textContent="Calculating…";
  try{
    const interval=Number($("paperAtrInterval").value);
    const data=await api(`/api/candles?symbol=${encodeURIComponent(paperSelected.symbol)}&instrument_key=${encodeURIComponent(paperSelected.instrument_key)}&interval=${interval}&days=3`);
    paperAtr=paperCalcAtrFromCandles(data.candles||[]);
    $("paperAtrValue").textContent=paperAtr?paperMoney(paperAtr):"UNAVAILABLE";
    if(!paperAtr)throw new Error("Not enough option-premium candles for ATR. Try a liquid contract or another timeframe.");
    $("paperRiskNote").textContent=`Premium ATR(${interval}m) = ${paperMoney(paperAtr)}. SL/targets are based on this option's own candles.`;
    paperUpdatePreview();toast("Option premium ATR calculated.");
  }catch(e){paperAtr=null;$("paperAtrValue").textContent="UNAVAILABLE";$("paperRiskNote").textContent=e.message;paperUpdatePreview();toast("ATR unavailable: "+e.message)}
  finally{$("paperCalcAtr").disabled=false;$("paperCalcAtr").textContent="Calculate ATR"}
}
function paperRiskPlan(){
  const entry=paperNum("paperEntry");
  if(!paperSelected||!entry)return null;
  if($("paperRiskMode").value==="atr"){
    if(!paperAtr)return null;
    const sm=paperNum("paperSlMult"),m1=paperNum("paperT1Mult"),m2=paperNum("paperT2Mult"),m3=paperNum("paperT3Mult");
    if(!sm||!m1||!m2||!m3)return null;
    return {entry,sl:entry-paperAtr*sm,t1:entry+paperAtr*m1,t2:entry+paperAtr*m2,t3:entry+paperAtr*m3,atr:paperAtr,mode:"ATR"};
  }
  const sl=paperNum("paperManualSl"),t1=paperNum("paperManualT1");
  const t2=paperNum("paperManualT2"),t3=paperNum("paperManualT3");
  if(!sl||!t1||sl>=entry||t1<=entry)return null;
  if(t2!==null&&t2<=entry)return null;
  if(t3!==null&&t3<=entry)return null;
  return {entry,sl,t1,t2,t3,atr:null,mode:"MANUAL"};
}
function paperUpdatePreview(){
  const p=paperRiskPlan(),qty=paperNum("paperQty")||1;
  $("paperPreviewContract").textContent=paperSelected?paperSelectedLabel():"—";
  $("paperPreviewEntry").textContent=p?paperMoney(p.entry):"—";
  $("paperPreviewSl").textContent=p?paperMoney(p.sl):"—";
  $("paperPreviewT1").textContent=p?paperMoney(p.t1):"—";
  $("paperPreviewT2").textContent=p&&p.t2?paperMoney(p.t2):"—";
  $("paperPreviewT3").textContent=p&&p.t3?paperMoney(p.t3):"—";
  const risk=p?(p.entry-p.sl):null;
  $("paperPreviewRisk").textContent=risk>0?paperMoney(risk*qty):"—";
  $("paperPreviewRR").textContent=risk>0&&p.t1?( ((p.t1-p.entry)/risk).toFixed(2)+"R"):"—";
  const valid=!!(p&&risk>0&&Number.isInteger(qty)&&qty>0&&paperSelected);
  $("paperBuyBtn").disabled=!valid;
  if(!paperSelected)$("paperRiskNote").textContent="Select CE or PE from the option chain.";
  else if(!p)$("paperRiskNote").textContent=$("paperRiskMode").value==="atr"?"Calculate option-premium ATR and confirm entry price; targets/SL will then be previewed.":"Enter a valid entry, stop-loss below entry and target above entry.";
  else $("paperRiskNote").textContent=`${p.mode} plan ready. Risk ${paperMoney(risk)} per unit; max planned loss at selected quantity: ${paperMoney(risk*qty)} (excluding costs/slippage).`;
}
async function paperFetchQuote(force){
  if(!paperSelected)return null;
  try{
    const q=await api(`/api/option-quote?instrument_key=${encodeURIComponent(paperSelected.instrument_key)}`);
    if(q.ltp!==null&&q.ltp!==undefined&&Number(q.ltp)>0){
      paperSelected.ltp=Number(q.ltp);paperSelected.quoteStatus=q.status||"UNAVAILABLE";paperSelected.quoteTimestamp=q.timestamp;
      if(!$("paperEntry").dataset.userEdited||force)$("paperEntry").value=paperSelected.ltp.toFixed(2);
      $("paperSelectedContract").textContent=`Selected: ${paperSelectedLabel()} · LTP ${paperMoney(paperSelected.ltp)} · ${paperSelected.quoteStatus} · ${paperTime(q.timestamp)}`;
      return {price:paperSelected.ltp,status:q.status,timestamp:q.timestamp};
    }
  }catch(e){
    paperSelected.quoteStatus="UNAVAILABLE";
    $("paperSelectedContract").textContent=`Selected: ${paperSelectedLabel()} · quote UNAVAILABLE (${e.message})`;
  }
  return null;
}
function paperCloseTrade(trade,price,reason){
  trade.status="CLOSED";trade.exit_price=Number(price);trade.exit_time=new Date().toISOString();trade.exit_reason=reason;
  trade.realized_pnl=(trade.exit_price-trade.entry)*trade.qty;
  trade.last_price=trade.exit_price;
}
async function paperOpenTrade(){
  if(!paperSelected){toast("Option chain से CE/PE select करें.");return}
  if(!$("paperEntry").value){const q=await paperFetchQuote(true);if(!q){toast("Fresh option quote unavailable. Entry manually भरें या बाद में retry करें.");return}}
  const p=paperRiskPlan(),qty=paperNum("paperQty");
  if(!p||!qty||!Number.isInteger(qty)||qty<1){toast("Entry/SL/target/quantity ठीक करें; ATR mode में पहले ATR calculate करें.");return}
  if(p.sl>=p.entry||p.t1<=p.entry||(p.t2&&p.t2<=p.entry)||(p.t3&&p.t3<=p.entry)){toast("BUY trade में SL entry से नीचे और targets entry से ऊपर होने चाहिए.");return}
  const trade={
    id:(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random()),symbol:paperSelected.symbol,type:paperSelected.type,
    strike:paperSelected.strike,expiry:paperSelected.expiry,instrument_key:paperSelected.instrument_key,
    qty,entry:p.entry,sl:p.sl,t1:p.t1,t2:p.t2,t3:p.t3,atr:p.atr,risk_mode:p.mode,
    exit_target:$("paperExitTarget").value,status:"OPEN",opened_at:new Date().toISOString(),
    last_price:paperSelected.ltp||p.entry,last_quote_status:paperSelected.quoteStatus||"UNAVAILABLE",last_quote_time:paperSelected.quoteTimestamp||null,
    exit_price:null,exit_time:null,exit_reason:null,realized_pnl:null
  };
  const trades=paperRead();trades.unshift(trade);paperSave(trades);paperRender();
  toast(`Paper BUY opened: ${trade.symbol} ${trade.strike} ${trade.type}`);
}
function paperRender(){
  const trades=paperRead(),open=trades.filter(t=>t.status==="OPEN"),closed=trades.filter(t=>t.status==="CLOSED");
  const openPnl=open.reduce((s,t)=>s+(Number.isFinite(Number(t.last_price))?(Number(t.last_price)-t.entry)*t.qty:0),0);
  const realized=closed.reduce((s,t)=>s+(Number(t.realized_pnl)||0),0);
  $("paperOpenCount").textContent=open.length;$("paperOpenPnl").textContent=paperMoney(openPnl);$("paperRealizedPnl").textContent=paperMoney(realized);$("paperClosedCount").textContent=closed.length;
  $("paperOpenPnl").className=openPnl>=0?"pnl-positive":"pnl-negative";$("paperRealizedPnl").className=realized>=0?"pnl-positive":"pnl-negative";
  const openBody=$("paperOpenRows");openBody.innerHTML="";
  if(!open.length)openBody.innerHTML='<tr><td colspan="10" class="empty">No open paper trades.</td></tr>';
  open.forEach(t=>{
    const target=t.exit_target==="t1"?t.t1:t.exit_target==="t2"?t.t2:t.exit_target==="t3"?t.t3:null;
    const ltp=Number(t.last_price),pnl=(Number.isFinite(ltp)?(ltp-t.entry)*t.qty:null);
    const values=[paperTime(t.opened_at),`${t.symbol} ${t.strike} ${t.type} · ${t.expiry}`,t.qty,paperMoney(t.entry),paperMoney(ltp),paperMoney(t.sl),target?paperMoney(target):"Manual",pnl===null?"UNAVAILABLE":paperMoney(pnl),t.last_quote_status||"UNAVAILABLE"];
    const tr=document.createElement("tr");values.forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===7&&pnl!==null)td.className=pnl>=0?"pnl-positive":"pnl-negative";if(i===8)td.className="paper-muted";tr.appendChild(td)});
    const action=document.createElement("td"),btn=document.createElement("button");btn.className="secondary";btn.textContent="Close";btn.addEventListener("click",()=>paperManualClose(t.id));action.appendChild(btn);tr.appendChild(action);openBody.appendChild(tr);
  });
  const hist=$("paperHistoryRows");hist.innerHTML="";
  if(!closed.length)hist.innerHTML='<tr><td colspan="7" class="empty">No closed trades yet.</td></tr>';
  closed.slice(0,100).forEach(t=>{
    const tr=document.createElement("tr"),values=[`${paperTime(t.opened_at)} / ${paperTime(t.exit_time)}`,`${t.symbol} ${t.strike} ${t.type} · ${t.expiry}`,t.qty,paperMoney(t.entry),paperMoney(t.exit_price),t.exit_reason||"Manual",paperMoney(t.realized_pnl)];
    values.forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===6)td.className=Number(t.realized_pnl)>=0?"pnl-positive":"pnl-negative";tr.appendChild(td)});hist.appendChild(tr);
  });
}
async function paperManualClose(id){
  const trades=paperRead(),t=trades.find(x=>x.id===id&&x.status==="OPEN");if(!t)return;
  let price=Number(t.last_price);
  try{const q=await api(`/api/option-quote?instrument_key=${encodeURIComponent(t.instrument_key)}`);if(q.ltp>0){price=Number(q.ltp);t.last_quote_status=q.status;t.last_quote_time=q.timestamp}}catch{}
  const typed=prompt(`Paper exit price for ${t.symbol} ${t.strike} ${t.type}:`,Number.isFinite(price)?price.toFixed(2):"");
  if(typed===null)return;
  const exit=Number(typed);if(!Number.isFinite(exit)||exit<=0){toast("Valid exit premium required.");return}
  paperCloseTrade(t,exit,"Manual exit");paperSave(trades);paperRender();toast("Paper position closed.");
}
async function paperRefreshQuotes(){
  if(paperRefreshBusy)return;paperRefreshBusy=true;
  try{
    const trades=paperRead(),open=trades.filter(t=>t.status==="OPEN");
    for(const t of open){
      try{
        const q=await api(`/api/option-quote?instrument_key=${encodeURIComponent(t.instrument_key)}`);
        if(!(Number(q.ltp)>0)||q.status==="UNAVAILABLE") {t.last_quote_status="UNAVAILABLE";continue}
        const price=Number(q.ltp);t.last_price=price;t.last_quote_status=q.status;t.last_quote_time=q.timestamp;
        // Simulated threshold exits only when the backend returned a usable quote.
        if(price<=t.sl)paperCloseTrade(t,t.sl,"Stop-loss touched (observed quote)");
        else {
          const target=t.exit_target==="t1"?t.t1:t.exit_target==="t2"?t.t2:t.exit_target==="t3"?t.t3:null;
          if(target&&price>=target)paperCloseTrade(t,target,`Target ${t.exit_target.toUpperCase()} touched (observed quote)`);
        }
      }catch{t.last_quote_status="UNAVAILABLE"}
    }
    paperSave(trades);paperRender();
  }finally{paperRefreshBusy=false}
}
function paperExportCsv(){
  const trades=paperRead();if(!trades.length){toast("No paper trades to export.");return}
  const cols=["symbol","type","strike","expiry","qty","entry","sl","t1","t2","t3","risk_mode","opened_at","status","last_price","exit_price","exit_time","exit_reason","realized_pnl"];
  const esc=v=>`"${String(v??"").replace(/"/g,'""')}"`;
  const csv=[cols.join(","),...trades.map(t=>cols.map(k=>esc(t[k])).join(","))].join("\r\n");
  const blob=new Blob([csv],{type:"text/csv;charset=utf-8;"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="option-intelligence-paper-trades.csv";a.click();URL.revokeObjectURL(url);
}
$("paperRiskMode").addEventListener("change",paperUpdateMode);
["paperEntry","paperQty","paperSlMult","paperT1Mult","paperT2Mult","paperT3Mult","paperManualSl","paperManualT1","paperManualT2","paperManualT3","paperExitTarget"].forEach(id=>$(id).addEventListener("input",()=>{if(id==="paperEntry")$("paperEntry").dataset.userEdited="1";paperUpdatePreview()}));
$("paperEntry").addEventListener("change",paperUpdatePreview);
$("paperCalcAtr").addEventListener("click",paperCalculateAtr);
$("paperBuyBtn").addEventListener("click",paperOpenTrade);
$("paperRefreshBtn").addEventListener("click",paperRefreshQuotes);
$("paperExportBtn").addEventListener("click",paperExportCsv);
$("paperClearBtn").addEventListener("click",()=>{if(!confirm("Clear all CLOSED paper-trade history from this browser? Open trades will be kept."))return;paperSave(paperRead().filter(t=>t.status==="OPEN"));paperRender();});
paperUpdateMode();paperRender();setInterval(()=>{if(document.visibilityState==="visible")paperRefreshQuotes()},15000);
