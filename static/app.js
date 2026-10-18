const FRONTEND_VERSION="1.5.0";
let chart, candleSeries, ema9Series, ema21Series, vwapSeries, currentCandles=[], srLines=[], chartSource="index", chartRequestId=0;
let lastVolumeSource=null, lastAnalysis=null, lastSR=null, lastIndexSR=null, lastOptionVol=null, marketOpen=false, chartFitKey=null, chainLotSize=null;
const $ = id => document.getElementById(id);
function toast(msg){const el=$("toast");el.textContent=msg;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),4500)}
function fmt(v,n=2){return v===null||v===undefined||!Number.isFinite(Number(v))?"—":Number(v).toLocaleString("en-IN",{maximumFractionDigits:n,minimumFractionDigits:n})}
function getSymbol(){return $("symbol").value}
function getInterval(){return Number($("interval").value)}
function initChart(){
  const el=$("chart");
  chart=LightweightCharts.createChart(el,{width:el.clientWidth,height:el.clientHeight,layout:{background:{color:"#121d2d"},textColor:"#91a4ba"},grid:{vertLines:{color:"#1e2d40"},horzLines:{color:"#1e2d40"}},rightPriceScale:{borderColor:"#27384d"},timeScale:{borderColor:"#27384d",timeVisible:true,secondsVisible:false,tickMarkFormatter:istTickMarkFormatter},localization:{timeFormatter:istCrosshairTimeFormatter},crosshair:{mode:LightweightCharts.CrosshairMode.Normal}});
  candleSeries=chart.addCandlestickSeries({upColor:"#25c58a",downColor:"#ff667a",borderUpColor:"#25c58a",borderDownColor:"#ff667a",wickUpColor:"#25c58a",wickDownColor:"#ff667a"});
  ema9Series=chart.addLineSeries({color:"#25c58a",lineWidth:2,priceLineVisible:false,lastValueVisible:false});
  ema21Series=chart.addLineSeries({color:"#ff4d5e",lineWidth:2,priceLineVisible:false,lastValueVisible:false});
  vwapSeries=chart.addLineSeries({color:"#ffffff",lineWidth:2,priceLineVisible:false,lineStyle:LightweightCharts.LineStyle.Solid,lastValueVisible:false});
  window.addEventListener("resize",()=>chart.applyOptions({width:el.clientWidth,height:el.clientHeight}));
}
// Keep candle timestamps as true Unix seconds. Lightweight Charts can format numeric
// timestamps in UTC depending on its formatter, so explicitly render the axis in India time.
const IST_OFFSET_SECONDS = 5 * 60 * 60 + 30 * 60;
function epochSeconds(t){
  if(typeof t === "number" && Number.isFinite(t)) return Math.floor(t > 1e12 ? t / 1000 : t);
  const ms = Date.parse(t);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : NaN;
}
function toChartTime(t){return epochSeconds(t)}
function istDateFromChartTime(time){
  const seconds = typeof time === "number"
    ? time
    : (time && typeof time === "object"
      ? Date.UTC(time.year, time.month - 1, time.day) / 1000
      : NaN);
  return Number.isFinite(seconds) ? new Date((seconds + IST_OFFSET_SECONDS) * 1000) : null;
}
function istTickMarkFormatter(time, tickMarkType){
  const d = istDateFromChartTime(time);
  if(!d) return "";
  const day = String(d.getUTCDate()).padStart(2,"0");
  const month = d.toLocaleString("en-GB",{month:"short",timeZone:"UTC"});
  const year = d.getUTCFullYear();
  const hour = String(d.getUTCHours()).padStart(2,"0");
  const minute = String(d.getUTCMinutes()).padStart(2,"0");
  const kind = LightweightCharts.TickMarkType;
  if(tickMarkType === kind.Year) return String(year);
  if(tickMarkType === kind.Month) return `${month} '${String(year).slice(-2)}`;
  if(tickMarkType === kind.DayOfMonth) return `${day} ${month}`;
  return `${hour}:${minute}`;
}
function istCrosshairTimeFormatter(time){
  const d = istDateFromChartTime(time);
  if(!d) return "";
  const date = `${String(d.getUTCDate()).padStart(2,"0")} ${d.toLocaleString("en-GB",{month:"short",timeZone:"UTC"})}`;
  const timeText = `${String(d.getUTCHours()).padStart(2,"0")}:${String(d.getUTCMinutes()).padStart(2,"0")}`;
  return `${date} ${timeText} IST`;
}
function calcEMA(vals,n){const a=2/(n+1),out=[];vals.forEach((v,i)=>out.push(i===0?v:a*v+(1-a)*out[i-1]));return out}
// RSI / ATR use Wilder smoothing so values line up with TradingView's ta.rsi / ta.atr.
function rsiValue(closes,n=14){
  if(closes.length<n+1)return null;
  let g=0,l=0;
  for(let i=1;i<=n;i++){const d=closes[i]-closes[i-1];g+=Math.max(d,0);l+=Math.max(-d,0)}
  g/=n;l/=n;
  for(let i=n+1;i<closes.length;i++){const d=closes[i]-closes[i-1];g=(g*(n-1)+Math.max(d,0))/n;l=(l*(n-1)+Math.max(-d,0))/n}
  return l===0?(g>0?100:50):100-100/(1+g/l);
}
function atrValue(cs,n=14){
  if(cs.length<n+1)return null;
  const tr=[];for(let i=1;i<cs.length;i++)tr.push(Math.max(cs[i].high-cs[i].low,Math.abs(cs[i].high-cs[i-1].close),Math.abs(cs[i].low-cs[i-1].close)));
  let atr=tr.slice(0,n).reduce((a,b)=>a+b,0)/n;
  for(let i=n;i<tr.length;i++)atr=(atr*(n-1)+tr[i])/n;
  return atr;
}
function istDayKey(t){return Math.floor((t+IST_OFFSET_SECONDS)/86400)}
function calcVWAP(cs){
  // Anchored to each IST session. Index feeds carry no volume, so fall back to a running
  // typical-price average (and say so in the legend) instead of a line that just tracks the close.
  let day=null,pv=0,vol=0,tp=0,n=0;
  return cs.map(c=>{
    const d=istDayKey(c.time);if(d!==day){day=d;pv=vol=tp=n=0}
    const typical=(c.high+c.low+c.close)/3,v=Number(c.volume)||0;
    pv+=typical*v;vol+=v;tp+=typical;n++;
    return {time:c.time,value:vol>0?pv/vol:tp/n,volumeBased:vol>0};
  });
}
// ---------------- Support / resistance engine ----------------
// Levels are ZONES built from (a) 7-bar swing pivots over the last ~5 sessions, (b) previous-day
// high / low / close and today's high / low. Nearby candidates are merged (zone width ~0.6 ATR) and
// scored by touches, key-level status and recency. Nearest 3 per side are returned, each with an origin time.
const SR_PER_SIDE=3;
function prepCandles(raw){
  const valid=(raw||[]).map(c=>({time:toChartTime(c.time),open:+c.open,high:+c.high,low:+c.low,close:+c.close,volume:+c.volume||0})).filter(c=>Number.isFinite(c.time)&&[c.open,c.high,c.low,c.close].every(Number.isFinite)).sort((a,b)=>a.time-b.time);
  const unique=[];for(const c of valid){if(!unique.length||unique[unique.length-1].time!==c.time)unique.push(c)}
  return unique;
}
function computeSR(cs,interval,symbol,isIndex){
  const n=cs.length,empty={supports:[],resistances:[],support:null,resistance:null,last:n?cs[n-1].close:null,atr:null,note:"Not enough candles to map levels."};
  if(n<30)return empty;
  const last=cs[n-1].close;
  const atr=atrValue(cs)||cs.slice(-30).reduce((a,c)=>a+(c.high-c.low),0)/30;
  const tol=Math.max(atr*0.6,last*0.0004);
  const perSession=Math.max(1,Math.round(375/interval)),start=Math.max(0,n-perSession*5),L=3;
  const cands=[];
  for(let i=Math.max(L,start);i<n-L;i++){
    let isH=true,isL=true;
    for(let k=1;k<=L;k++){
      if(cs[i].high<=cs[i-k].high||cs[i].high<cs[i+k].high)isH=false;
      if(cs[i].low>=cs[i-k].low||cs[i].low>cs[i+k].low)isL=false;
    }
    if(isH)cands.push({price:cs[i].high,idx:i,kind:"Swing",w:2,swing:true});
    if(isL)cands.push({price:cs[i].low,idx:i,kind:"Swing",w:2,swing:true});
  }
  // key levels from IST sessions
  const days=[];for(let i=0;i<n;i++){const d=istDayKey(cs[i].time);if(!days.length||days[days.length-1].d!==d)days.push({d,from:i,to:i});else days[days.length-1].to=i}
  const extreme=(from,to,hi)=>{let b=from;for(let i=from;i<=to;i++)if(hi?cs[i].high>cs[b].high:cs[i].low<cs[b].low)b=i;return b};
  if(days.length>=2){
    const p=days.at(-2),t=days.at(-1),ph=extreme(p.from,p.to,true),pl=extreme(p.from,p.to,false);
    cands.push({price:cs[ph].high,idx:ph,kind:"PDH",w:3},{price:cs[pl].low,idx:pl,kind:"PDL",w:3},{price:cs[p.to].close,idx:p.to,kind:"PDC",w:2});
    const th=extreme(t.from,t.to,true),tl=extreme(t.from,t.to,false);
    if(th<n-1)cands.push({price:cs[th].high,idx:th,kind:"Day H",w:2});
    if(tl<n-1)cands.push({price:cs[tl].low,idx:tl,kind:"Day L",w:2});
  }
  // cluster into zones
  cands.sort((a,b)=>a.price-b.price);
  const zones=[];
  for(const c of cands){
    let z=zones[zones.length-1];
    if(!z||Math.abs(c.price-z.mean)>tol){z={items:[],mean:c.price};zones.push(z)}
    z.items.push(c);z.mean=z.items.reduce((a,x)=>a+x.price,0)/z.items.length;
  }
  const step=!isIndex?null:(symbol==="BANKNIFTY"?100:50);
  const levels=zones.map(z=>{
    const prices=z.items.map(x=>x.price),totalW=z.items.reduce((a,x)=>a+x.w,0),price=z.items.reduce((a,x)=>a+x.price*x.w,0)/totalW;
    const lastIdx=Math.max(...z.items.map(x=>x.idx)),firstIdx=Math.min(...z.items.map(x=>x.idx));
    const kinds=[...new Set(z.items.filter(x=>!x.swing).map(x=>x.kind))],swings=z.items.filter(x=>x.swing).length;
    const recency=lastIdx>=n-perSession?2:lastIdx>=n-perSession*3?1:0;
    const round=step&&Math.abs(price-Math.round(price/step)*step)<=tol*0.6;
    const score=totalW+recency+(round?1:0);
    const basis=[...kinds,swings?`${swings} swing${swings>1?"s":""}`:null,round?"round no.":null].filter(Boolean).join(" · ");
    return {price,low:Math.min(...prices),high:Math.max(...prices),score,strength:score>=8?"Strong":score>=5?"Medium":"Weak",touches:z.items.length,basis,originTime:cs[firstIdx].time,lastTime:cs[lastIdx].time,distance:Math.abs(last-price)};
  });
  const withMeta=l=>({...l,pct:l.distance/last*100,atrMult:atr?l.distance/atr:null});
  // Nearest zones first, but weak single-touch zones only fill slots that stronger zones cannot,
  // so a stray micro-pivot just beside price never displaces a real level.
  const pick=arr=>{const byDist=arr.sort((a,b)=>a.distance-b.distance);return [...byDist.filter(l=>l.score>=5),...byDist.filter(l=>l.score<5)].slice(0,SR_PER_SIDE).sort((a,b)=>a.distance-b.distance).map(withMeta)};
  const supports=pick(levels.filter(l=>l.price<=last)),resistances=pick(levels.filter(l=>l.price>last));
  const notes=[];
  if(!resistances.length)notes.push("No resistance above price in the loaded window — price is at/near its recent high.");
  if(!supports.length)notes.push("No support below price in the loaded window — price is at/near its recent low.");
  return {supports,resistances,support:supports[0]?.price??null,resistance:resistances[0]?.price??null,last,atr,tol,note:notes.join(" ")};
}
// ---------------- Signal confirmation (backend checks + S/R room gate) ----------------
function renderSignal(){
  const a=lastAnalysis,gl=$("gateList"),rows=$("checkRows");
  if(!gl||!rows)return;
  gl.innerHTML="";rows.innerHTML="";$("extraInd").innerHTML="";
  if(!a||a.status!=="ok"){$("finalSignal").textContent="WAIT";$("finalSignalSub").textContent=(a&&a.message)||"Waiting for enough candles.";$("confirmBadge").textContent="WAIT";$("confirmBadge").className="pill muted";return}
  const side=a.side,dom=side==="CE"?a.bull_score:a.bear_score,gates=(a.gates||[]).map(g=>({...g}));
  const isr=lastIndexSR;
  if(side&&isr&&isr.atr){
    const lvl=side==="CE"?isr.resistances[0]:isr.supports[0],room=lvl?lvl.distance/isr.atr:Infinity;
    gates.push({label:`Index room to ${side==="CE"?"resistance R1":"support S1"} ≥ 1 ATR`,pass:room>=1,detail:lvl?`${fmt(lvl.price)} is ${fmt(lvl.distance)} pts (${fmt(room,1)} ATR) away`:"No level in the loaded window"});
  }
  const ov=lastOptionVol,optRows=[];
  if(!ov||ov.none)optRows.push({label:"Option volume (selected contract)",reading:"excluded",detail:"Select a CE/PE contract to add its volume (the index itself has no volume).",cls:"sr-missing"});
  else if(ov.error)optRows.push({label:`Option volume (${ov.name})`,reading:"excluded",detail:"Option candles unavailable.",cls:"sr-missing"});
  else{
    const rel=ov.rel,reading=rel>=1.2?"▲ Expanding":rel>=0.6?"— Normal":"▼ Dry";
    optRows.push({label:`Option volume (${ov.name})`,reading,detail:`${fmt(rel,2)}× (last 3 bars vs prior 20) · ${Math.round(ov.total).toLocaleString("en-IN")} total`,cls:rel>=1.2?"sr-support":rel<0.6?"sr-resistance":""});
    if(side&&ov.type===side)gates.push({label:"Option volume not dry (≥ 0.6×)",pass:rel>=0.6,detail:`${fmt(rel,2)}× average — thin volume means wide spreads and unreliable fills`});
    else if(side)optRows.push({label:"Option volume gate",reading:"skipped",detail:`Selected contract is a ${ov.type}; the signal is ${side}. Select a ${side} to apply this gate.`,cls:"sr-missing"});
  }
  const failed=gates.filter(g=>!g.pass);
  let text="WAIT",sub="No side has enough aligned evidence. Stand aside.",cls="muted",color="var(--yellow)";
  if(side&&gates.length&&!failed.length){text=`CONFIRMED ${side} SETUP`;sub="Every gate passes. Enter only on a retest that holds, with the stop-loss and size set before entry.";cls="live";color=side==="CE"?"var(--green)":"var(--red)"}
  else if(side&&dom>=55&&failed.length<=2){text=`WATCH ${side}`;sub="Waiting for: "+failed.map(g=>g.label).join("; ");cls="stale"}
  $("finalSignal").textContent=text;$("finalSignal").style.color=color;$("finalSignalSub").textContent=sub;
  $("confirmBadge").textContent=text.split(" SETUP")[0];$("confirmBadge").className="pill "+cls;
  $("decision").textContent=text;$("bigDecision").textContent=text;$("signalExplanation").textContent=sub;
  gates.forEach(g=>{const li=document.createElement("li");li.className=g.pass?"gate-pass":"gate-fail";
    const b=document.createElement("b");b.textContent=g.pass?"✓":"✗";const t=document.createElement("span");t.textContent=` ${g.label}`;const d=document.createElement("small");d.textContent=g.detail||"";
    li.appendChild(b);li.appendChild(t);li.appendChild(d);gl.appendChild(li)});
  if(!gates.length){const li=document.createElement("li");li.className="gate-fail";li.textContent="No directional edge: bull and bear scores are tied.";gl.appendChild(li)}
  (a.checks||[]).forEach(c=>{
    const tr=document.createElement("tr"),reading=!c.ok?"excluded":c.bull?"▲ Bullish":c.bear?"▼ Bearish":"— Neutral";
    tr.className=!c.ok?"sr-missing":c.bull?"sr-support":c.bear?"sr-resistance":"";
    [c.label,String(c.weight),reading,c.detail||""].forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===3)td.style.textAlign="left";tr.appendChild(td)});rows.appendChild(tr);
    if(c.key==="volume")optRows.forEach(o=>{const r2=document.createElement("tr");r2.className=o.cls||"";[o.label,"gate",o.reading,o.detail].forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===3)td.style.textAlign="left";r2.appendChild(td)});rows.appendChild(r2)})});
  const extra=[["ADX / +DI / −DI",a.adx?`${fmt(a.adx.adx,1)} / ${fmt(a.adx.plus_di,1)} / ${fmt(a.adx.minus_di,1)}`:"—"],
    ["MACD histogram",a.macd?fmt(a.macd.hist):"—"],["Supertrend",a.supertrend?`${a.supertrend.direction} @ ${fmt(a.supertrend.level)}`:"—"],
    ["Bollinger %B · width",a.bollinger?`${fmt(a.bollinger.percent_b)} · ${fmt(a.bollinger.bandwidth_pct)}%`:"—"],
    ["Volatility squeeze",a.bollinger?(a.bollinger.squeeze?"YES — breakout may follow":"No"):"—"],["Higher timeframe",a.htf?a.htf.state:"unavailable"]];
  extra.forEach(([k,v])=>{const d=document.createElement("div"),sp=document.createElement("span"),b=document.createElement("b");sp.textContent=k;b.textContent=v;d.appendChild(sp);d.appendChild(b);$("extraInd").appendChild(d)});
}
let srSeries=[],srReq=0,srTfCache={key:null,at:0,sr:null};
function clearSRLines(){
  for(const s of srSeries){try{chart.removeSeries(s)}catch{}}srSeries=[];
  for(const line of srLines){try{candleSeries.removePriceLine(line)}catch{}}srLines=[];
}
function drawSR(sr,lastTime){
  clearSRLines();
  if($("showSR")?.checked===false||!sr)return;
  const LS=LightweightCharts.LineStyle,styles=[LS.Solid,LS.Dashed,LS.Dotted];
  const draw=(lv,i,sup)=>{
    const color=sup?"#25c58a":"#ff4d5e",tag=(sup?"S":"R")+(i+1);
    // Line starts where the level formed (not full width) and is excluded from auto-scaling.
    if(lv.originTime<lastTime){
      const s=chart.addLineSeries({color,lineWidth:lv.strength==="Strong"?3:lv.strength==="Medium"?2:1,lineStyle:styles[i]??LS.Dotted,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,autoscaleInfoProvider:()=>null});
      s.setData([{time:lv.originTime,value:lv.price},{time:lastTime,value:lv.price}]);srSeries.push(s);
    }
    srLines.push(candleSeries.createPriceLine({price:lv.price,lineVisible:false,axisLabelVisible:true,title:tag,axisLabelColor:sup?"#167c59":"#b82b3c",axisLabelTextColor:"#ffffff"}));
  };
  sr.supports.forEach((l,i)=>draw(l,i,true));sr.resistances.forEach((l,i)=>draw(l,i,false));
}
function srRow(label,cls,cells){
  const tr=document.createElement("tr");tr.className=cls;
  [label,...cells].forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===0)td.className="sr-tag";tr.appendChild(td)});
  return tr;
}
function renderSRDashboard(sr,sourceLabel){
  const body=$("srRows");if(!body)return;body.innerHTML="";
  const sub=$("srSubtitle");
  if(!sr||sr.last==null){body.innerHTML='<tr><td colspan="9" class="empty">Not enough candles to map support / resistance.</td></tr>';$("srSummary").textContent="";return}
  const f=v=>fmt(v),row=(l,i,sup)=>srRow((sup?"S":"R")+(i+1),sup?"sr-support":"sr-resistance",[f(l.price),`${f(l.low)} – ${f(l.high)}`,`${sup?"−":"+"}${f(l.distance)}`,`${fmt(l.pct)}%`,l.atrMult==null?"—":`${fmt(l.atrMult,1)}×`,String(l.touches),l.strength,l.basis||"—"]);
  const blank=(t,sup,i)=>srRow((sup?"S":"R")+(i+1),"sr-missing",["—","—","—","—","—","—","—","none in loaded window"]);
  for(let i=SR_PER_SIDE-1;i>=0;i--)body.appendChild(sr.resistances[i]?row(sr.resistances[i],i,false):blank("R",false,i));
  const px=srRow("PRICE","sr-price",[f(sr.last),"","","","","","",`ATR ${f(sr.atr)}`]);body.appendChild(px);
  for(let i=0;i<SR_PER_SIDE;i++)body.appendChild(sr.supports[i]?row(sr.supports[i],i,true):blank("S",true,i));
  sub.textContent=`Nearest ${SR_PER_SIDE} zones each side · ${sourceLabel} · swing pivots + previous-day levels`;
  const s1=sr.supports[0],r1=sr.resistances[0];
  let text="";
  if(s1&&r1){const span=r1.price-s1.price,pos=span>0?(sr.last-s1.price)/span*100:50;text=`Price sits ${pos.toFixed(0)}% of the way from S1 to R1 · range ${f(span)} pts${sr.atr?` (${fmt(span/sr.atr,1)} ATR)`:""}. ${pos>=70?"Closer to resistance — long entries have less room.":pos<=30?"Closer to support — short entries have less room.":"Mid-range — wait for a break or a reaction at a level."}`;const bar=$("srPosFill");if(bar)bar.style.left=Math.max(0,Math.min(100,pos))+"%"}
  $("srSummary").textContent=text;
  $("srNote").textContent=sr.note||"Levels are zones, not exact prices. Strength = touches + key-level status (PDH/PDL/PDC, day H/L) + recency. Weak (single-touch) zones appear only when fewer than three stronger ones exist on that side. A level price has just broken flips role automatically.";
}
function applySR(sr,lastTime,sourceLabel){
  lastSR=sr;if(!(chartSource==="option"&&paperSelected))lastIndexSR=sr;drawSR(sr,lastTime);renderSRDashboard(sr,sourceLabel);if(lastAnalysis)renderSignal();
  $("chartSupport").textContent=fmt(sr?.support);$("chartResistance").textContent=fmt(sr?.resistance);
}
async function srFromHigherTf(tf){
  const optionMode=chartSource==="option"&&paperSelected;
  const key=`${optionMode?paperSelected.instrument_key:getSymbol()}|${tf}`;
  if(srTfCache.key===key&&Date.now()-srTfCache.at<60000)return srTfCache.sr;
  const url=optionMode?`/api/candles?symbol=${encodeURIComponent(paperSelected.symbol)}&instrument_key=${encodeURIComponent(paperSelected.instrument_key)}&interval=${tf}&days=20`:`/api/candles?symbol=${getSymbol()}&interval=${tf}&days=20`;
  const data=await api(url),cs=prepCandles(data.candles);
  const sr=computeSR(cs,tf,optionMode?paperSelected.symbol:getSymbol(),!optionMode);
  srTfCache={key,at:Date.now(),sr};return sr;
}
// Show ~1.6 sessions on load / when the source changes, then leave the user's zoom and scroll alone
// (the old code re-fitted all 5 days on every 60s refresh, squashing the latest session).
function applyChartRange(n){
  const key=`${chartSource}|${getSymbol()}|${getInterval()}|${paperSelected?.instrument_key||""}`;
  if(chartFitKey===key||n<2)return;
  chartFitKey=key;
  const perSession=Math.max(1,Math.round(375/getInterval()));
  const show=Math.min(n,Math.round(perSession*1.6));
  chart.timeScale().setVisibleLogicalRange({from:n-show,to:n+3});
}
function renderCandles(candles){
  currentCandles=candles||[];
  const valid=currentCandles.map(c=>({time:toChartTime(c.time),open:+c.open,high:+c.high,low:+c.low,close:+c.close,volume:+c.volume||0})).filter(c=>Number.isFinite(c.time)&&[c.open,c.high,c.low,c.close].every(Number.isFinite)).sort((a,b)=>a.time-b.time);
  const unique=[];for(const c of valid){if(!unique.length||unique[unique.length-1].time!==c.time)unique.push(c)}
  candleSeries.setData(unique);
  const closes=unique.map(c=>c.close), e9=calcEMA(closes,9),e21=calcEMA(closes,21),vw=calcVWAP(unique);
  ema9Series.setData(unique.map((c,i)=>({time:c.time,value:e9[i]})));ema21Series.setData(unique.map((c,i)=>({time:c.time,value:e21[i]})));vwapSeries.setData(vw.map(({time,value})=>({time,value})));
  const showEma=$("showEma")?.checked!==false,showVwap=$("showVWAP")?.checked!==false,showSR=$("showSR")?.checked!==false;
  ema9Series.applyOptions({visible:showEma});ema21Series.applyOptions({visible:showEma});vwapSeries.applyOptions({visible:showVwap});clearSRLines();
  const optMode=chartSource==="option"&&paperSelected;
  const sr=computeSR(unique,getInterval(),optMode?paperSelected.symbol:getSymbol(),!optMode),lastTime=unique.length?unique.at(-1).time:0;
  const tf=$("srTf")?$("srTf").value:"chart",tfNum=Number(tf);
  applySR(sr,lastTime,`${getInterval()}-minute ${optMode?"premium":"index"} candles`);
  if(tf!=="chart"&&tfNum!==getInterval()&&unique.length){
    const id=++srReq;
    srFromHigherTf(tfNum).then(h=>{if(id===srReq&&$("srTf").value===tf&&h)applySR(h,lastTime,`${tfNum}-minute candles`)}).catch(()=>{});
  }
  applyChartRange(unique.length);
  const vwapLegend=document.querySelector(".vwap-legend"),vwapIsVol=!!vw.at(-1)?.volumeBased;
  if(vwapLegend)vwapLegend.textContent=vwapIsVol?"━ VWAP":"━ Session avg price (no index volume)";
  if($("vwapLabel"))$("vwapLabel").textContent=vwapIsVol?"VWAP (session)":"Session avg price (no volume)";
  if(unique.length){const last=unique.at(-1),rsi=rsiValue(closes),atr=atrValue(unique),volume=last.volume,avgVol=unique.slice(-21,-1).reduce((a,c)=>a+c.volume,0)/Math.max(1,unique.slice(-21,-1).length);$("lastPrice").textContent=fmt(last.close);$("chartSubtitle").textContent=chartSource==="option"&&paperSelected?`${paperSelectedLabel()} · ${getInterval()}-minute premium OHLC`:`${getSymbol()==="NIFTY"?"NIFTY 50":"BANK NIFTY"} · ${getInterval()}-minute OHLC`;
    $("chartRsi").textContent=fmt(rsi);$("chartAtr").textContent=fmt(atr);$("chartVolume").textContent=avgVol>0?`${fmt(volume,0)} (${fmt(volume/avgVol,2)}x)`:`${fmt(volume,0)} · ratio N/A`;
    $("chartNote").textContent=chartSource==="option"?`Selected option premium candles · ${unique.length} bars. Indicators and support/resistance use this option's own candles. Historical data may be unavailable for illiquid/expired contracts.`:"Underlying index candles. Support/resistance zones are listed in the S/R map below; the nearest three per side are drawn. Choose Selected option premium, then select CE/PE from the option chain to switch the chart to that contract.";
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
// Relative volume of the selected option: average of the last 3 bars vs the prior 20 (a single bar is too noisy).
function optionRelVolume(cs){
  if(cs.length<24)return null;
  const v=cs.map(c=>c.volume),last3=v.slice(-3).reduce((a,b)=>a+b,0)/3,prior=v.slice(-23,-3).reduce((a,b)=>a+b,0)/20;
  return prior>0?{rel:last3/prior,total:v.reduce((a,b)=>a+b,0)}:null;
}
async function loadOptionVolume(){
  if(!paperSelected){lastOptionVol={none:true};renderSignal();return}
  const sel=paperSelected,name=`${sel.type} ${sel.strike}`;
  try{
    const d=await api(`/api/candles?symbol=${encodeURIComponent(sel.symbol)}&instrument_key=${encodeURIComponent(sel.instrument_key)}&interval=${getInterval()}&days=5`);
    const r=optionRelVolume(prepCandles(d.candles));
    if(paperSelected!==sel)return;
    lastOptionVol=r?{...r,type:sel.type,name}:{error:true,name};
  }catch{lastOptionVol={error:true,name}}
  renderSignal();
}
// Index analysis + index S/R + option volume. Runs in BOTH chart views so the signal never goes stale
// or borrows the option-premium chart's support/resistance.
async function refreshSignalContext(){
  const sym=getSymbol(),interval=getInterval();
  const [c,a]=await Promise.all([api(`/api/candles?symbol=${sym}&interval=${interval}&days=10`),api(`/api/analysis?symbol=${sym}&interval=${interval}`)]);
  lastIndexSR=computeSR(prepCandles(c.candles),interval,sym,true);
  lastAnalysis=a;
  $("score").textContent=a.status==="ok"?`${a.score}/100`:"—";$("regime").textContent=a.status==="ok"?(a.regime||"—"):"—";
  $("regime").style.color=a.regime==="BULLISH"?"var(--green)":a.regime==="BEARISH"?"var(--red)":"var(--yellow)";
  $("bullScore").textContent=a.bull_score??"—";$("bearScore").textContent=a.bear_score??"—";
  renderSignal();
}
async function refresh(){
  if($("chartView").value==="option"){await refreshChartSource();try{await refreshSignalContext()}catch(e){$("analysisNote").textContent=e.message}loadOptionVolume();return}
  $("refreshBtn").disabled=true;$("refreshBtn").textContent="Loading…";
  try{
    const sym=getSymbol(),interval=getInterval();
    const [c,a]=await Promise.all([
      api(`/api/candles?symbol=${sym}&interval=${interval}&days=10`),
      api(`/api/analysis?symbol=${sym}&interval=${interval}`)
    ]);
    lastVolumeSource=c.volume_source||null;
    renderCandles(c.candles);
    if(lastVolumeSource)$("chartNote").textContent+=` Volume and VWAP weights come from ${lastVolumeSource} (the index itself has no volume).`;
    $("lastUpdated").textContent=`Updated ${new Date().toLocaleTimeString()}`;
    $("score").textContent=a.status==="ok"?`${a.score}/100`:"—";
    $("regime").textContent=a.status==="ok"?(a.regime||"—"):"—";
    $("decision").textContent=a.decision||"WAIT";
    $("bigDecision").textContent=a.decision||"WAIT";$("decisionSub").textContent=marketOpen?"Bias only; confirm retest":"Last session data (market closed) — bias only";
    $("bullScore").textContent=a.bull_score??"—";$("bearScore").textContent=a.bear_score??"—";
    $("ema9").textContent=fmt(a.ema9);$("ema21").textContent=fmt(a.ema21);$("vwap").textContent=fmt(a.vwap_approx);
    $("rsi").textContent=fmt(a.rsi);$("atr").textContent=fmt(a.atr);$("relVol").textContent=a.relative_volume==null?"—":`${fmt(a.relative_volume)}x`;if($("vwapLabel"))$("vwapLabel").textContent=String(a.vwap_source||"").startsWith("VOLUME_VWAP")?"VWAP (session)":"Session avg price (no volume)";
    $("breakHigh").textContent=fmt(a.breakout_high);$("breakLow").textContent=fmt(a.breakdown_low);
    $("analysisNote").textContent=[a.note||a.message||"Analysis loaded.",a.score_basis].filter(Boolean).join(" · ");
    $("signalExplanation").textContent=a.note||a.message||"Confirm breakout and retest before any trade.";
    $("regime").style.color=a.regime==="BULLISH"?"var(--green)":a.regime==="BEARISH"?"var(--red)":"var(--yellow)";
    lastAnalysis=a;renderSignal();loadOptionVolume();
  }catch(e){toast("Data load failed: "+e.message);$("analysisNote").textContent=e.message}
  finally{$("refreshBtn").disabled=false;$("refreshBtn").textContent="Refresh data"}
}
function renderMarketPill(m){
  const el=$("marketStatus");if(!el||!m)return;
  el.textContent=m.open?"Market open (IST)":"Market closed (IST)";
  el.className=m.open?"pill live":"pill muted";
  el.title=(m.ist_time||"")+" IST · "+(m.note||"");
}
async function checkStatus(){
  try{
    const s=await api("/api/status"),el=$("apiStatus");
    $("buildInfo").textContent=`page v${FRONTEND_VERSION} · server v${s.version||"?"}`;
    if(s.version&&s.version!==FRONTEND_VERSION&&true){const b=$("staleBanner");b.textContent="⚠ This page is out of date (page v"+FRONTEND_VERSION+" vs server v"+s.version+"). Press Ctrl+F5 to load the latest version — the old script is cached in your browser.";b.hidden=false}
    if(s.token_state==="VALID"){el.textContent="Upstox token valid";el.className="pill ok"}
    else if(s.token_state==="EXPIRED"){el.textContent="Upstox token expired — click Connect Upstox";el.className="pill bad"}
    else if(s.token_configured){el.textContent="Upstox token set (could not verify)";el.className="pill muted"}
    else{el.textContent="Upstox setup required";el.className="pill bad"}
    marketOpen=!!(s.market&&s.market.open);renderMarketPill(s.market);
  }catch{$("apiStatus").textContent="Backend unavailable"}
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
function renderFilteredChain(opts={}){
  const scrollToAtm=opts.scroll!==false;
  const wrap=$("chainRows").closest(".table-wrap"),keepTop=wrap?wrap.scrollTop:0;
  const body=$("chainRows");body.innerHTML="";
  const rows=optionChainRows;
  if(!rows.length){body.innerHTML='<tr><td colspan="8" class="empty">No option rows returned. Check expiry selection and Upstox API response.</td></tr>';return}
  const typeFilter=$("optionTypeFilter")?.value||"BOTH", moneyFilter=$("moneynessFilter")?.value||"ALL";
  optionChainSpot=getChainSpot(rows);
  const strikes=rows.map(r=>Number(r.strike_price)).filter(Number.isFinite);
  const atmStrike=optionChainSpot&&strikes.length?strikes.reduce((best,x)=>Math.abs(x-optionChainSpot)<Math.abs(best-optionChainSpot)?x:best,strikes[0]):null;
  const sortedStrikes=[...new Set(strikes)].sort((a,b)=>a-b),atmIdx=atmStrike===null?-1:sortedStrikes.indexOf(atmStrike);
  const nearSet=new Set(atmIdx<0?[]:sortedStrikes.slice(Math.max(0,atmIdx-10),atmIdx+11));
  const filtered=rows.filter(r=>{
    const strike=Number(r.strike_price), ce=r.call_options||{},pe=r.put_options||{};
    if(typeFilter==="CE"&&!ce.instrument_key)return false;
    if(typeFilter==="PE"&&!pe.instrument_key)return false;
    if(moneyFilter==="ALL")return true;
    if(moneyFilter==="NEAR")return atmIdx<0?true:nearSet.has(strike);
    if(!optionChainSpot||atmStrike===null)return false;
    const ceMatch=ce.instrument_key&&moneynessOf(strike,"CE",optionChainSpot,atmStrike)===moneyFilter;
    const peMatch=pe.instrument_key&&moneynessOf(strike,"PE",optionChainSpot,atmStrike)===moneyFilter;
    return typeFilter==="CE"?!!ceMatch:typeFilter==="PE"?!!peMatch:!!(ceMatch||peMatch);
  });
  if(!filtered.length){body.innerHTML=`<tr><td colspan="8" class="empty">No ${moneyFilter} strikes available for this selection. ${optionChainSpot?`Spot ${fmt(optionChainSpot)} · ATM ${fmt(atmStrike,0)}.`:"Underlying spot unavailable; choose All or refresh data."}</td></tr>`;return}
  let atmRow=null;
  filtered.forEach(r=>{
    const call=r.call_options||{},put=r.put_options||{},cm=call.market_data||{},pm=put.market_data||{};
    const tr=document.createElement("tr");
    const strike=Number(r.strike_price);
    if(atmStrike!==null&&Math.abs(strike-atmStrike)<0.001){tr.classList.add("atm-row");atmRow=tr}
    if(paperSelected&&(paperSelected.instrument_key===call.instrument_key||paperSelected.instrument_key===put.instrument_key))tr.classList.add("chain-selected");
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
  if(wrap){if(scrollToAtm&&atmRow){requestAnimationFrame(()=>{wrap.scrollTop=Math.max(0,atmRow.getBoundingClientRect().top-wrap.getBoundingClientRect().top+wrap.scrollTop-wrap.clientHeight/2+atmRow.clientHeight/2)})}else wrap.scrollTop=keepTop}
  $("chainStatus").textContent=`Showing ${filtered.length} of ${rows.length} strikes${chainLotSize?` · lot size ${chainLotSize}`:""}${optionChainSpot?` · spot ${fmt(optionChainSpot)} · ATM ${fmt(atmStrike,0)}`:" · spot unavailable"}. ATM row highlighted. Select Use CE/Use PE. Prices are API snapshots (refreshed every 30s while the market is open), not guaranteed live ticks.`;
}
function renderChain(data){
  optionChainRows=data.data||data.chain||[];chainLotSize=data.lot_size||null;
  if(!optionChainRows.length){$("chainStatus").textContent="Upstox returned zero option rows for this expiry. Try another listed expiry or check Render Logs.";renderFilteredChain();return}
  renderFilteredChain();
}
async function loadContracts(autoLoadChain=true){
  const btn=$("loadContracts");if(btn){btn.disabled=true;btn.textContent="Loading…"}
  $("chainStatus").textContent="Fetching actual expiry dates from Upstox…";
  try{
    const data=await api(`/api/option-contracts?symbol=${encodeURIComponent(getSymbol())}`);
    const rows=Array.isArray(data.data)?data.data:(Array.isArray(data.contracts)?data.contracts:[]);
    const now=new Date();const todayKey=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-${String(now.getDate()).padStart(2,"0")}`;
    const dates=[...new Set(rows.map(x=>String(x.expiry||x.expiry_date||x.expiryDate||"").slice(0,10)).filter(d=>/^\d{4}-\d{2}-\d{2}$/.test(d)&&d>=todayKey))].sort();
    const select=$("expiry"),previous=select.value;select.innerHTML="";
    if(!dates.length){select.innerHTML='<option value="">No expiry dates returned</option>';const detail=data.detail?JSON.stringify(data.detail):`Upstox returned ${rows.length} contracts; no upcoming expiry field was found.`;$("chainStatus").textContent=detail+" Check /api/option-contracts?symbol="+getSymbol()+" and Render Logs.";toast("No upcoming expiry dates returned. Check the API diagnostic.");return}
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
async function refreshChainQuiet(){
  if(!/^\d{4}-\d{2}-\d{2}$/.test($("expiry").value||"")||!optionChainRows.length)return;
  try{
    const data=await api(`/api/option-chain?symbol=${encodeURIComponent(getSymbol())}&expiry_date=${encodeURIComponent($("expiry").value)}`);
    optionChainRows=data.data||[];chainLotSize=data.lot_size||chainLotSize;renderFilteredChain({scroll:false});
  }catch{}
}
$("refreshBtn").addEventListener("click",refresh);
$("symbol").addEventListener("change",()=>{
  optionChainRows=[];chainLotSize=null;chartFitKey=null;lastDerivatives=null;
  $("chainRows").innerHTML='<tr><td colspan="8" class="empty">Loading expiries for this underlying…</td></tr>';
  renderLiveTicks([]);connectLiveSocket();refresh();
  loadContracts(true); // expiry list -> chain -> derivatives for that real expiry
});
$("interval").addEventListener("change",refresh);
$("loadContracts").addEventListener("click",()=>loadContracts(true));
$("loadChain").addEventListener("click",()=>loadChain());
$("chartView").addEventListener("change",refreshChartSource);
["showEma","showVWAP","showSR"].forEach(id=>$(id).addEventListener("change",()=>renderCandles(currentCandles)));
$("srTf").addEventListener("change",()=>{srTfCache={key:null,at:0,sr:null};renderCandles(currentCandles)});
const chartPanel=document.querySelector(".chart-panel");
const chartWrap=$("chart");
function resizeChartSoon(){requestAnimationFrame(()=>{if(chart&&chartWrap){chart.applyOptions({width:chartWrap.clientWidth,height:chartWrap.clientHeight});}})}
$("chartMaximize").addEventListener("click",()=>{
  const maximized=chartPanel.classList.toggle("chart-maximized");
  $("chartMaximize").textContent=maximized?"↙ Restore":"⛶ Maximize";
  document.body.classList.toggle("chart-modal-open",maximized);
  resizeChartSoon();
});
$("chartFit").addEventListener("click",()=>{chartFitKey=null;renderCandles(currentCandles)});
$("chartMinimize").addEventListener("click",()=>{
  const collapsed=chartPanel.classList.toggle("chart-collapsed");
  $("chartMinimize").textContent=collapsed?"＋ Show chart":"− Minimize chart";
  resizeChartSoon();
});
window.addEventListener("keydown",e=>{if(e.key==="Escape"&&chartPanel.classList.contains("chart-maximized"))$("chartMaximize").click()});
["optionTypeFilter","moneynessFilter"].forEach(id=>$(id)?.addEventListener("change",renderFilteredChain));
if(new URLSearchParams(location.search).get("connected")==="1")toast("Upstox OAuth completed for this running instance. Add a durable token method before production use.");
initChart();checkStatus();refresh();
setInterval(()=>{if(document.visibilityState==="visible"&&marketOpen)refresh()},60000);
let liveSocket=null, lastDerivatives=null, liveSocketGen=0, liveReconnectTimer=null;
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
  // One socket at a time. Old handlers are detached first so closing it cannot schedule a second reconnect.
  clearTimeout(liveReconnectTimer);
  const gen=++liveSocketGen;
  if(liveSocket){liveSocket.onopen=liveSocket.onmessage=liveSocket.onerror=liveSocket.onclose=null;try{liveSocket.close()}catch{}}
  const proto=location.protocol==="https:"?"wss:":"ws:";
  const ws=new WebSocket(`${proto}//${location.host}/ws/live?symbol=${encodeURIComponent(getSymbol())}`);
  liveSocket=ws;
  ws.onopen=()=>{if(gen!==liveSocketGen)return;$("streamStatus").textContent="CONNECTED";$("streamStatus").className="pill ok"};
  ws.onmessage=ev=>{
    if(gen!==liveSocketGen)return;
    try{
      const m=JSON.parse(ev.data),ticks=(m.ticks||[]).map(t=>({...t,fresh:!!t.fresh}));
      renderLiveTicks(ticks);
      if(m.stream==="LIVE"){$("streamStatus").textContent="LIVE TICKS";$("streamStatus").className="pill live"}
      else{$("streamStatus").textContent=m.stream||"CONNECTING";$("streamStatus").className="pill stale"}
      if(ticks.length){const ages=ticks.map(t=>t.age_seconds).filter(Number.isFinite);if(ages.length)$("tickAge").textContent=`${fmt(Math.min(...ages),1)}s`}
      if(m.error)$("streamError").textContent=m.error;else if(ticks.length)$("streamError").textContent=m.stream==="LIVE"?"WebSocket feed connected":"Ticks are stale (market closed or feed idle)";
    }catch{}
  };
  ws.onerror=()=>{if(gen!==liveSocketGen)return;$("streamStatus").textContent="UNAVAILABLE";$("streamStatus").className="pill bad"};
  ws.onclose=()=>{if(gen!==liveSocketGen)return;liveReconnectTimer=setTimeout(()=>{if(gen===liveSocketGen&&document.visibilityState==="visible")connectLiveSocket()},4000)};
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
    $("oiStatus").textContent=d.oi_status||"UNAVAILABLE";$("ivStatus").textContent=(d.iv_status||"UNAVAILABLE")+(d.atm_iv?` · ATM IV ${fmt(d.atm_iv)}%`:"");
    $("tickAge").textContent=d.websocket_last_tick_age_seconds==null?"—":`${fmt(d.websocket_last_tick_age_seconds,1)}s`;if(d.market)renderMarketPill(d.market),marketOpen=!!d.market.open;
    $("streamError").textContent=d.websocket_error||d.note||"Waiting for ticks";
    $("derivTimestamp").textContent=`Snapshot timestamp: ${d.rest_snapshot_timestamp||d.timestamp||"UNAVAILABLE"} · expiry: ${d.expiry||"UNAVAILABLE"} · WS: ${d.websocket||"UNAVAILABLE"}`;
    if(d.websocket==="LIVE"){$("streamStatus").textContent="LIVE TICKS";$("streamStatus").className="pill live"}
    else if(d.websocket_error){$("streamStatus").textContent="UNAVAILABLE";$("streamStatus").className="pill bad"}
    else if(d.websocket==="MARKET_CLOSED"){$("streamStatus").textContent="MARKET CLOSED";$("streamStatus").className="pill muted"}
    if(!liveSocket||liveSocket.readyState!==1)renderLiveTicks(d.live_near_atm||[]); // the 1s WebSocket push owns this table while connected
  }catch(e){$("livePCR").textContent="UNAVAILABLE";$("liveCallOI").textContent="UNAVAILABLE";$("livePutOI").textContent="UNAVAILABLE";$("liveIV").textContent="UNAVAILABLE";$("streamStatus").textContent="UNAVAILABLE";$("streamError").textContent=e.message;$("derivTimestamp").textContent="Snapshot timestamp: UNAVAILABLE"}
}
$("expiry").addEventListener("change",()=>loadChain()); // loadChain also refreshes derivatives for the same expiry
connectLiveSocket();loadContracts(true);
setInterval(()=>{if(document.visibilityState==="visible")refreshDerivatives()},15000);
setInterval(()=>{if(document.visibilityState==="visible"&&marketOpen)refreshChainQuiet()},30000);
setInterval(checkStatus,60000);
document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible"&&(!liveSocket||liveSocket.readyState>1))connectLiveSocket()});

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
function paperSelectedLabel(){return paperSelected?`${paperSelected.symbol} ${paperSelected.expiry} ${paperSelected.strike} ${paperSelected.type}`:"No option selected"}
function selectPaperContract(type,strike,instrumentKey,ltp,expiry,button){
  if(!instrumentKey){toast("This option has no instrument key in the chain response.");return}
  paperSelected={symbol:getSymbol(),type,strike:Number(strike),instrument_key:instrumentKey,expiry:String(expiry||$("expiry").value),ltp:Number(ltp)||null,quoteStatus:"CHAIN_SNAPSHOT",lot:chainLotSize||null};
  {const lot=paperSelected.lot||1;$("paperQty").value=String(lot);$("paperQty").step=String(lot);$("paperLotHint").textContent=paperSelected.lot?` · 1 lot = ${paperSelected.lot}`:""}
  paperAtr=null;$("paperAtrValue").textContent="UNAVAILABLE";
  $("paperSelectedContract").textContent=`Selected: ${paperSelectedLabel()} · chain LTP ${paperMoney(paperSelected.ltp)} (snapshot)`;
  $("paperEntry").value=paperSelected.ltp||"";
  $("paperBuyBtn").disabled=false;
  document.querySelectorAll(".chain-select").forEach(b=>b.closest("tr")?.classList.remove("chain-selected"));
  if(button)button.closest("tr")?.classList.add("chain-selected");
  paperUpdateMode();paperUpdatePreview();
  if($("chartView").value==="option")refreshChartSource();
  loadOptionVolume();
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
  const cs=candles.map(c=>({t:Date.parse(c.time),high:Number(c.high),low:Number(c.low),close:Number(c.close)}))
    .filter(c=>Number.isFinite(c.t)&&[c.high,c.low,c.close].every(Number.isFinite)).sort((a,b)=>a.t-b.t);
  return atrValue(cs); // Wilder ATR(14), same as the chart / TradingView
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
  else{
    const rr=risk>0?(p.t1-p.entry)/risk:null,lot=paperSelected.lot;
    let note=`${p.mode} plan ready. Risk ${paperMoney(risk)} per unit; max planned loss at selected quantity: ${paperMoney(risk*qty)} (excluding costs/slippage).`;
    if(rr!==null&&rr<1)note+=` ⚠ Target 1 pays only ${rr.toFixed(2)}R: with "Auto-exit at Target 1" you need a win rate above ${(100/(1+rr)).toFixed(0)}% just to break even.`;
    if(lot&&qty%lot!==0)note+=` ⚠ Quantity is not a multiple of the lot size (${lot}); real exchange orders need whole lots.`;
    $("paperRiskNote").textContent=note;
  }
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
  toast(`Paper BUY opened: ${trade.symbol} ${trade.strike} ${trade.type}${marketOpen?"":" — market is closed, entry uses the last snapshot price"}`);
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
        if(price<=t.sl)paperCloseTrade(t,Math.min(price,t.sl),"Stop-loss hit (filled at observed quote)");
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
