let chart, candleSeries, ema9Series, ema21Series, vwapSeries, currentCandles=[], srLines=[], chartSource="index", chartRequestId=0;
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
  vwapSeries=chart.addLineSeries({color:"#c084fc",lineWidth:1,priceLineVisible:false,lineStyle:LightweightCharts.LineStyle.Dashed});
  window.addEventListener("resize",()=>chart.applyOptions({width:el.clientWidth,height:el.clientHeight}));
}
function epochSeconds(t){let d=Date.parse(t);return Math.floor(d/1000)}
function toChartTime(t){return epochSeconds(t)}
function calcEMA(vals,n){const a=2/(n+1),out=[];vals.forEach((v,i)=>out.push(i===0?v:a*v+(1-a)*out[i-1]));return out}
function clearSRLines(){for(const line of srLines){try{candleSeries.removePriceLine(line)}catch{}}srLines=[]}
function rsiValue(closes,n=14){if(closes.length<n+1)return null;let g=0,l=0;for(let i=closes.length-n;i<closes.length;i++){const d=closes[i]-closes[i-1];g+=Math.max(d,0);l+=Math.max(-d,0)}return l===0?100:100-100/(1+g/l)}
function atrValue(cs,n=14){if(cs.length<n+1)return null;const tr=[];for(let i=1;i<cs.length;i++)tr.push(Math.max(cs[i].high-cs[i].low,Math.abs(cs[i].high-cs[i-1].close),Math.abs(cs[i].low-cs[i-1].close)));return tr.slice(-n).reduce((a,b)=>a+b,0)/Math.min(n,tr.slice(-n).length)}
function calcVWAP(cs){let pv=0,vol=0;return cs.map(c=>{const v=Number(c.volume)||0;pv+=((c.high+c.low+c.close)/3)*v;vol+=v;return {time:c.time,value:vol?pv/vol:c.close}})}
function findSR(cs){if(cs.length<7)return {support:null,resistance:null};const start=Math.max(2,cs.length-80);let lows=[],highs=[];for(let i=start;i<cs.length-2;i++){if(cs[i].low<=cs[i-1].low&&cs[i].low<=cs[i-2].low&&cs[i].low<=cs[i+1].low&&cs[i].low<=cs[i+2].low)lows.push(cs[i].low);if(cs[i].high>=cs[i-1].high&&cs[i].high>=cs[i-2].high&&cs[i].high>=cs[i+1].high&&cs[i].high>=cs[i+2].high)highs.push(cs[i].high)}const last=cs.at(-1).close;const supports=lows.filter(v=>v<last).sort((a,b)=>b-a);const resistances=highs.filter(v=>v>last).sort((a,b)=>a-b);return {support:supports[0]??Math.min(...cs.slice(-20).map(c=>c.low)),resistance:resistances[0]??Math.max(...cs.slice(-20).map(c=>c.high))}}
function renderCandles(candles){
  currentCandles=candles||[];
  const valid=currentCandles.map(c=>({time:toChartTime(c.time),open:+c.open,high:+c.high,low:+c.low,close:+c.close,volume:+c.volume||0})).filter(c=>Number.isFinite(c.time)&&[c.open,c.high,c.low,c.close].every(Number.isFinite)).sort((a,b)=>a.time-b.time);
  const unique=[];for(const c of valid){if(!unique.length||unique[unique.length-1].time!==c.time)unique.push(c)}
  candleSeries.setData(unique);
  const closes=unique.map(c=>c.close), e9=calcEMA(closes,9),e21=calcEMA(closes,21),vw=calcVWAP(unique);
  ema9Series.setData(unique.map((c,i)=>({time:c.time,value:e9[i]})));ema21Series.setData(unique.map((c,i)=>({time:c.time,value:e21[i]})));vwapSeries.setData(vw);
  const showEma=$("showEma")?.checked!==false,showVwap=$("showVWAP")?.checked!==false,showSR=$("showSR")?.checked!==false;
  ema9Series.applyOptions({visible:showEma});ema21Series.applyOptions({visible:showEma});vwapSeries.applyOptions({visible:showVwap});clearSRLines();
  const sr=findSR(unique);if(showSR&&sr.support!==null&&Number.isFinite(sr.support))srLines.push(candleSeries.createPriceLine({price:sr.support,color:"#27c99a",lineWidth:2,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:true,title:"Support"}));if(showSR&&sr.resistance!==null&&Number.isFinite(sr.resistance))srLines.push(candleSeries.createPriceLine({price:sr.resistance,color:"#ff7185",lineWidth:2,lineStyle:LightweightCharts.LineStyle.Dashed,axisLabelVisible:true,title:"Resistance"}));
  chart.timeScale().fitContent();
  if(unique.length){const last=unique.at(-1),rsi=rsiValue(closes),atr=atrValue(unique),volume=last.volume,avgVol=unique.slice(-21,-1).reduce((a,c)=>a+c.volume,0)/Math.max(1,unique.slice(-21,-1).length);$("lastPrice").textContent=fmt(last.close);$("chartSubtitle").textContent=chartSource==="option"&&paperSelected?`${paperSelectedLabel()} · ${getInterval()}-minute premium OHLC`:`${getSymbol()==="NIFTY"?"NIFTY 50":"BANK NIFTY"} · ${getInterval()}-minute OHLC`;
    $("chartRsi").textContent=fmt(rsi);$("chartAtr").textContent=fmt(atr);$("chartSupport").textContent=fmt(sr.support);$("chartResistance").textContent=fmt(sr.resistance);$("chartVolume").textContent=avgVol>0?`${fmt(volume,0)} (${fmt(volume/avgVol,2)}x)`:`${fmt(volume,0)} · ratio N/A`;
    $("chartNote").textContent=chartSource==="option"?`Selected option premium candles · ${unique.length} bars. Indicators and support/resistance use this option's own candles. Historical data may be unavailable for illiquid/expired contracts.`:"Underlying index candles. Choose Selected option premium, then select CE/PE from the option chain to switch the chart to that contract.";
    $("ema9").textContent=fmt(e9.at(-1));$("ema21").textContent=fmt(e21.at(-1));$("vwap").textContent=fmt(vw.at(-1)?.value);$("rsi").textContent=fmt(rsi);$("atr").textContent=fmt(atr);$("relVol").textContent=avgVol>0?`${fmt(volume/avgVol,2)}x`:"—";$("breakHigh").textContent=fmt(Math.max(...unique.slice(-20,-1).map(c=>c.high)));$("breakLow").textContent=fmt(Math.min(...unique.slice(-20,-1).map(c=>c.low)));
  }else{$("chartNote").textContent=chartSource==="option"?"No historical candles returned for this option contract. Index candles are not substituted.":"No candle data returned."}
}
async function refreshChartSource(){
 const id=++chartRequestId;
 if($("chartView").value!=="option") {chartSource="index";await refresh();return}
 if(!paperSelected){chartSource="option";currentCandles=[];renderCandles([]);candleSeries.setData([]);ema9Series.setData([]);ema21Series.setData([]);vwapSeries.setData([]);$("lastPrice").textContent="—";["ema9","ema21","vwap","rsi","atr","relVol","breakHigh","breakLow","chartRsi","chartAtr","chartSupport","chartResistance","chartVolume"].forEach(id=>$(id).textContent="—");$("chartSubtitle").textContent="Selected option premium · no contract selected";$("chartNote").textContent="Select CE/PE in the option chain first. Index candles are intentionally not shown as option premium.";toast("Select an option contract from the chain first.");return}
 chartSource="option";$("chartNote").textContent="Loading selected option premium candles…";
 try{const data=await api(`/api/candles?symbol=${encodeURIComponent(paperSelected.symbol)}&instrument_key=${encodeURIComponent(paperSelected.instrument_key)}&interval=${getInterval()}&days=5`);if(id!==chartRequestId)return;renderCandles(data.candles||[]);if(!data.candles?.length)toast("No historical premium candles returned for this contract.");}
 catch(e){if(id!==chartRequestId)return;currentCandles=[];renderCandles([]);$("chartNote").textContent="Option premium chart unavailable: "+e.message;toast("Option chart failed: "+e.message)}
}

async function api(url){
  const res=await fetch(url);let data;try{data=await res.json()}catch{data={detail:await res.text()}}
  if(!res.ok)throw new Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail||data));
  return data;
}
async function refresh(){
  if($("chartView").value==="option"){await refreshChartSource();return}
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
let optionChainRows=[];
let optionChainSpot=null;
function getChainSpot(rows=[]){
  const candidates=[lastDerivatives?.spot, ...rows.map(r=>r.underlying_spot_price??r.underlying_spot??r.spot_price)].map(Number).filter(v=>Number.isFinite(v)&&v>0);
  return candidates.length?candidates[0]:null;
}
function moneynessOf(strike,type,spot,atmStrike){
  if(!spot)return "UNKNOWN";
  if(Math.abs(Number(strike)-atmStrike)<0.001)return "ATM";
  if(type==="CE")return Number(strike)<spot?"ITM":"OTM";
  return Number(strike)>spot?"ITM":"OTM";
}
function renderFilteredChain(){
  const body=$("chainRows");body.innerHTML="";
  const rows=optionChainRows;
  if(!rows.length){body.innerHTML='<tr><td colspan="8" class="empty">No option rows returned. Check expiry selection and Upstox API response.</td></tr>';return}
  const typeFilter=$("optionTypeFilter")?.value||"BOTH", moneyFilter=$("moneynessFilter")?.value||"ALL";
  optionChainSpot=getChainSpot(rows);
  const strikes=rows.map(r=>Number(r.strike_price)).filter(Number.isFinite);
  const atmStrike=optionChainSpot&&strikes.length?strikes.reduce((best,x)=>Math.abs(x-optionChainSpot)<Math.abs(best-optionChainSpot)?x:best,strikes[0]):null;
  const filtered=rows.filter(r=>{
    const strike=Number(r.strike_price), ce=r.call_options||{},pe=r.put_options||{};
    if(typeFilter==="CE"&&!ce.instrument_key)return false;
    if(typeFilter==="PE"&&!pe.instrument_key)return false;
    if(moneyFilter==="ALL")return true;
    if(!optionChainSpot||atmStrike===null)return false;
    const ceMatch=ce.instrument_key&&moneynessOf(strike,"CE",optionChainSpot,atmStrike)===moneyFilter;
    const peMatch=pe.instrument_key&&moneynessOf(strike,"PE",optionChainSpot,atmStrike)===moneyFilter;
    return typeFilter==="CE"?!!ceMatch:typeFilter==="PE"?!!peMatch:!!(ceMatch||peMatch);
  });
  if(!filtered.length){body.innerHTML=`<tr><td colspan="8" class="empty">No ${moneyFilter} strikes available for this selection. ${optionChainSpot?`Spot ${fmt(optionChainSpot)} · ATM ${fmt(atmStrike,0)}.`:"Underlying spot unavailable; choose All or refresh data."}</td></tr>`;return}
  filtered.forEach(r=>{
    const call=r.call_options||{},put=r.put_options||{},cm=call.market_data||{},pm=put.market_data||{};
    const tr=document.createElement("tr");
    const strike=Number(r.strike_price);
    const label=optionChainSpot&&atmStrike!==null?(Math.abs(strike-atmStrike)<0.001?"ATM":`${moneynessOf(strike,"CE",optionChainSpot,atmStrike)} CE / ${moneynessOf(strike,"PE",optionChainSpot,atmStrike)} PE`):"";
    [fmt(strike,0),fmt(cm.ltp),fmt(cm.oi,0),fmt(r.pcr,3),fmt(pm.oi,0),fmt(pm.ltp)].forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===0&&label)td.title=label;tr.appendChild(td)});
    const ceTd=document.createElement("td"),peTd=document.createElement("td");
    const ceBtn=document.createElement("button");ceBtn.type="button";ceBtn.className="chain-select";ceBtn.textContent="Use CE";
    ceBtn.disabled=!call.instrument_key||!(Number(cm.ltp)>0)||typeFilter==="PE";
    ceBtn.addEventListener("click",()=>selectPaperContract("CE",r.strike_price,call.instrument_key,cm.ltp,r.expiry||$("expiry").value,ceBtn));
    const peBtn=document.createElement("button");peBtn.type="button";peBtn.className="chain-select pe";peBtn.textContent="Use PE";
    peBtn.disabled=!put.instrument_key||!(Number(pm.ltp)>0)||typeFilter==="CE";
    peBtn.addEventListener("click",()=>selectPaperContract("PE",r.strike_price,put.instrument_key,pm.ltp,r.expiry||$("expiry").value,peBtn));
    ceTd.appendChild(ceBtn);peTd.appendChild(peBtn);tr.appendChild(ceTd);tr.appendChild(peTd);body.appendChild(tr);
  });
  $("chainStatus").textContent=`Loaded ${filtered.length} of ${rows.length} strikes${optionChainSpot?` · spot ${fmt(optionChainSpot)} · ATM ${fmt(atmStrike,0)}`:" · spot unavailable"}. Select Use CE/Use PE. Prices are API snapshots, not guaranteed live ticks.`;
}
function renderChain(data){
  optionChainRows=data.data||data.chain||[];
  if(!optionChainRows.length){$("chainStatus").textContent="Upstox returned zero option rows for this expiry. Try another listed expiry or check Render Logs.";renderFilteredChain();return}
  renderFilteredChain();
}
async function loadContracts(autoLoadChain=true){
  const btn=$("loadContracts");if(btn){btn.disabled=true;btn.textContent="Loading…"}
  $("chainStatus").textContent="Fetching actual expiry dates from Upstox…";
  try{
    const data=await api(`/api/option-contracts?symbol=${encodeURIComponent(getSymbol())}`);
    const rows=data.data||[];
    const today=new Date();today.setHours(0,0,0,0);
    const dates=[...new Set(rows.map(x=>String(x.expiry||"").slice(0,10)).filter(d=>/^\d{4}-\d{2}-\d{2}$/.test(d)&&new Date(d+"T00:00:00")>=today))].sort();
    const select=$("expiry"),previous=select.value;select.innerHTML="";
    if(!dates.length){select.innerHTML='<option value="">No expiry dates returned</option>';$("chainStatus").textContent="Upstox did not return upcoming expiries. Check token permissions and Render Logs.";toast("No upcoming expiry dates returned by Upstox.");return}
    dates.forEach(d=>{const op=document.createElement("option");op.value=d;op.textContent=d;select.appendChild(op)});
    select.value=dates.includes(previous)?previous:dates[0];
    toast(`Loaded ${dates.length} actual expiry dates.`);
    if(autoLoadChain)await loadChain(true);
  }catch(e){$("chainStatus").textContent="Expiry request failed: "+e.message;toast("Could not load expiries: "+e.message)}
  finally{if(btn){btn.disabled=false;btn.textContent="Load expiries"}}
}
async function loadChain(skipExpiryCheck=false){
  if(!/^\d{4}-\d{2}-\d{2}$/.test($("expiry").value||"")){
    await loadContracts(false);
    if(!/^\d{4}-\d{2}-\d{2}$/.test($("expiry").value||""))return;
  }
  const btn=$("loadChain");if(btn){btn.disabled=true;btn.textContent="Loading…"}
  $("chainStatus").textContent=`Loading ${getSymbol()} option chain for expiry ${$("expiry").value}…`;
  try{
    const data=await api(`/api/option-chain?symbol=${encodeURIComponent(getSymbol())}&expiry_date=${encodeURIComponent($("expiry").value)}`);
    renderChain(data);
    await refreshDerivatives();
    renderFilteredChain();
  }catch(e){optionChainRows=[];renderFilteredChain();$("chainStatus").textContent="Option-chain request failed: "+e.message;toast("Option chain failed: "+e.message)}
  finally{if(btn){btn.disabled=false;btn.textContent="Load chain"}}
}
$("refreshBtn").addEventListener("click",refresh);
$("symbol").addEventListener("change",()=>{optionChainRows=[];$("chainRows").innerHTML='<tr><td colspan="8" class="empty">Load expiries for this underlying.</td></tr>';loadContracts(true);refresh();});
$("interval").addEventListener("change",refresh);
$("loadContracts").addEventListener("click",()=>loadContracts(true));
$("loadChain").addEventListener("click",()=>loadChain());
$("chartView").addEventListener("change",refreshChartSource);
["showEma","showVWAP","showSR"].forEach(id=>$(id).addEventListener("change",()=>renderCandles(currentCandles)));
["optionTypeFilter","moneynessFilter"].forEach(id=>$(id)?.addEventListener("change",renderFilteredChain));
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
  liveSocket.onmessage=ev=>{try{const m=JSON.parse(ev.data);const ticks=(m.ticks||[]).map(t=>({...t,fresh:!!t.fresh}));renderLiveTicks(ticks);if(m.stream==="LIVE"){$("streamStatus").textContent="LIVE TICKS";$("streamStatus").className="pill live"}else{$("streamStatus").textContent=m.stream||"CONNECTING";$("streamStatus").className="pill stale"}if(m.error)$ ("streamError").textContent=m.error;else if(ticks.length)$ ("streamError").textContent="WebSocket feed connected";}catch{}}
  liveSocket.onerror=()=>{$("streamStatus").textContent="UNAVAILABLE";$("streamStatus").className="pill bad"};
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
    if(d.websocket==="LIVE"){$("streamStatus").textContent="LIVE TICKS";$("streamStatus").className="pill live"}
    else if(d.websocket_error){$("streamStatus").textContent="UNAVAILABLE";$("streamStatus").className="pill bad"}
    renderLiveTicks((d.live_near_atm||[]).map(t=>({...t,age_seconds:d.websocket_last_tick_age_seconds,fresh:d.websocket_last_tick_age_seconds!==null&&d.websocket_last_tick_age_seconds<=15})));
  }catch(e){$("livePCR").textContent="UNAVAILABLE";$("liveCallOI").textContent="UNAVAILABLE";$("livePutOI").textContent="UNAVAILABLE";$("liveIV").textContent="UNAVAILABLE";$("streamStatus").textContent="UNAVAILABLE";$("streamError").textContent=e.message;$("derivTimestamp").textContent="Snapshot timestamp: UNAVAILABLE"}
}
$("symbol").addEventListener("change",()=>{connectLiveSocket();refreshDerivatives()});
$("expiry").addEventListener("change",()=>{refreshDerivatives();loadChain()});
connectLiveSocket();refreshDerivatives();setInterval(()=>{if(document.visibilityState==="visible")refreshDerivatives()},15000);

// ---------------- Local-browser paper trading ----------------
// These are simulated positions only. They never place broker orders.
const PAPER_STORAGE_KEY = "option-intelligence-paper-trades-v1";
const PAPER_CAPITAL_KEY = "option-intelligence-paper-capital-v1";
const PAPER_DEFAULT_CAPITAL = 100000;
let paperSelected = null;
let paperAtr = null;
let paperRefreshBusy = false;
function paperRead(){try{return JSON.parse(localStorage.getItem(PAPER_STORAGE_KEY)||"[]")}catch{return []}}
function paperSave(trades){localStorage.setItem(PAPER_STORAGE_KEY,JSON.stringify(trades))}
function paperGetCapital(){const v=Number(localStorage.getItem(PAPER_CAPITAL_KEY));return Number.isFinite(v)&&v>0?v:PAPER_DEFAULT_CAPITAL}
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
  if($("chartView").value==="option")refreshChartSource();
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
  const trades=paperRead();
  const capital=paperGetCapital();
  const realized=trades.filter(t=>t.status==="CLOSED").reduce((sum,t)=>sum+(Number(t.realized_pnl)||0),0);
  const committed=trades.filter(t=>t.status==="OPEN").reduce((sum,t)=>sum+(Number(t.entry)||0)*(Number(t.qty)||0),0);
  const available=capital+realized-committed;
  const required=p.entry*qty;
  if(required>available){toast(`Insufficient paper cash. Required ${paperMoney(required)}, available ${paperMoney(Math.max(0,available))}.`);return}
  trades.unshift(trade);paperSave(trades);paperRender();
  toast(`Paper BUY opened: ${trade.symbol} ${trade.strike} ${trade.type}`);
}
function paperRender(){
  const trades=paperRead(),open=trades.filter(t=>t.status==="OPEN"),closed=trades.filter(t=>t.status==="CLOSED");
  const openPnl=open.reduce((s,t)=>s+(Number.isFinite(Number(t.last_price))?(Number(t.last_price)-t.entry)*t.qty:0),0);
  const realized=closed.reduce((s,t)=>s+(Number(t.realized_pnl)||0),0);
  const capital=paperGetCapital();
  const committed=open.reduce((s,t)=>s+(Number(t.entry)||0)*(Number(t.qty)||0),0);
  const available=capital+realized-committed;
  const equity=capital+realized+openPnl;
  $("paperInitialDisplay").textContent=paperMoney(capital);
  $("paperAvailableCash").textContent=paperMoney(available);
  $("paperEquity").textContent=paperMoney(equity);
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
$("paperSaveCapital").addEventListener("click",()=>{
  const value=Number($("paperInitialCapital").value);
  if(!Number.isFinite(value)||value<=0){toast("Initial capital ₹1 ya usse zyada enter karein.");return}
  const trades=paperRead(),open=trades.filter(t=>t.status==="OPEN");
  const committed=open.reduce((sum,t)=>sum+(Number(t.entry)||0)*(Number(t.qty)||0),0);
  const closedPnl=trades.filter(t=>t.status==="CLOSED").reduce((sum,t)=>sum+(Number(t.realized_pnl)||0),0);
  if(value+closedPnl<committed){toast("Starting capital itna kam nahi ho sakta: existing open positions ki cost cover honi chahiye.");return}
  localStorage.setItem(PAPER_CAPITAL_KEY,String(value));
  $("paperCapitalNote").textContent="Saved in this browser · "+new Date().toLocaleTimeString();
  paperRender();toast("Paper starting balance saved.");
});
$("paperRiskMode").addEventListener("change",paperUpdateMode);
["paperEntry","paperQty","paperSlMult","paperT1Mult","paperT2Mult","paperT3Mult","paperManualSl","paperManualT1","paperManualT2","paperManualT3","paperExitTarget"].forEach(id=>$(id).addEventListener("input",()=>{if(id==="paperEntry")$("paperEntry").dataset.userEdited="1";paperUpdatePreview()}));
$("paperEntry").addEventListener("change",paperUpdatePreview);
$("paperCalcAtr").addEventListener("click",paperCalculateAtr);
$("paperBuyBtn").addEventListener("click",paperOpenTrade);
$("paperRefreshBtn").addEventListener("click",paperRefreshQuotes);
$("paperExportBtn").addEventListener("click",paperExportCsv);
$("paperClearBtn").addEventListener("click",()=>{if(!confirm("Clear all CLOSED paper-trade history from this browser? Open trades will be kept."))return;paperSave(paperRead().filter(t=>t.status==="OPEN"));paperRender();});
$("paperInitialCapital").value=String(paperGetCapital());
paperUpdateMode();paperRender();setInterval(()=>{if(document.visibilityState==="visible")paperRefreshQuotes()},15000);
