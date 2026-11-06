const FRONTEND_VERSION="1.20.27";
let paperSelected = null;   // declared first: the startup refresh may read it before the paper-trading section below runs
let lastGateState=null,lastAnalysisAt=0,lastIdxLtp=null;
let liveSubSent="",wsBarsAt=0,wsAnalysisAt=0,lastFullRefresh=0,lastBarsRender=0;   // v1.19.6 live push state
let chart, candleSeries, ema9Series, ema21Series, vwapSeries, currentCandles=[], srLines=[], chartSource="index", chartRequestId=0;
let lastOptionAnalysis=null, lastVolumeSource=null, lastAnalysis=null, lastSR=null, lastIndexSR=null, lastOptionVol=null, marketOpen=false, chartFitKey=null, chainLotSize=null;
const $ = id => document.getElementById(id);
function toast(msg){const el=$("toast");el.textContent=msg;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),4500)}
function fmt(v,n=2){return v===null||v===undefined||!Number.isFinite(Number(v))?"—":Number(v).toLocaleString("en-IN",{maximumFractionDigits:n,minimumFractionDigits:n})}
function getSymbol(){return $("symbol").value}
function getInterval(){return Number($("interval").value)}
function getMode(){const m=$("signalMode");return m?m.value:"standard"}
function getReconfirm(){const m=$("reconfirmSec");return m?Number(m.value||0):60}
function getScoreMin(){const m=$("scoreMin");return m?Number(m.value||70):70}
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
// ---------------- v1.9: signal generation time + Data Check dashboard read ----------------
// ---------------- Alerts (v1.14): beep + browser notification + tab-title flash on signal transitions ----------------
let audioCtx=null,alertSoundOn=true,alertPrev=null,titleTimer=null;const baseTitle=document.title,lastAlertAt={};
try{alertSoundOn=localStorage.getItem("oi-alert-sound")!=="0"}catch{}
function unlockAudio(){try{if(!audioCtx)audioCtx=new(window.AudioContext||window.webkitAudioContext)();if(audioCtx.state==="suspended")audioCtx.resume()}catch{}}
document.addEventListener("click",unlockAudio,{once:true});document.addEventListener("keydown",unlockAudio,{once:true});
function tone(hz,at,dur,vol,type){const o=audioCtx.createOscillator(),g=audioCtx.createGain();o.type=type||"sine";o.frequency.value=hz;
  g.gain.setValueAtTime(0.0001,at);g.gain.exponentialRampToValueAtTime(vol,at+0.02);g.gain.exponentialRampToValueAtTime(0.0001,at+dur);o.connect(g);g.connect(audioCtx.destination);o.start(at);o.stop(at+dur+0.05)}
function playAlert(kind,side,force){
  if(!alertSoundOn&&!force)return;unlockAudio();if(!audioCtx)return;const t=audioCtx.currentTime+0.03;
  if(kind==="confirmed"){const f=side==="PE"?[1175,880,660]:[660,880,1175];for(let r=0;r<2;r++)f.forEach((hz,i)=>tone(hz,t+r*0.95+i*0.24,0.2,0.35,"square"))}   // CE rises, PE falls; played twice
  else if(kind==="start"){tone(880,t,0.12,0.22);tone(880,t+0.2,0.12,0.22)}
  else if(kind==="failed"){tone(220,t,0.55,0.25,"sawtooth")}
}
function flashTitle(msg){clearInterval(titleTimer);let on=false;titleTimer=setInterval(()=>{document.title=(on=!on)?`🔔 ${msg}`:baseTitle},900);
  const stop=()=>{clearInterval(titleTimer);document.title=baseTitle;window.removeEventListener("focus",stop)};window.addEventListener("focus",stop);setTimeout(stop,60000)}
function showNotice(title,body){
  try{if("Notification"in window&&Notification.permission==="granted")new Notification(title,{body,tag:"oi-signal"})}catch{}
  try{navigator.vibrate&&navigator.vibrate([200,100,200])}catch{}
  if(document.hidden)flashTitle(title)}
// Fires once per transition (never on the first paint after loading the page, never while the market is closed, and at most once per 20 s per kind).
function alertTransitions(a,gstatus,side,fin){
  const rc=a.reconfirm&&a.reconfirm.state||"NONE";
  const cur={side:side||null,confirmed:gstatus==="CONFIRMED",pending:!!(fin&&fin.level==="PENDING"),failed:rc==="FAILED"};
  const prev=alertPrev;alertPrev=cur;
  if(!prev||!marketOpen)return;
  const fire=(kind,fn)=>{const k=kind+"|"+cur.side,now=Date.now();if(now-(lastAlertAt[k]||0)<20000)return;lastAlertAt[k]=now;fn()};
  const bp=fin&&fin.buy,bo=bp&&bp.option;
  if(cur.confirmed&&(!prev.confirmed||prev.side!==cur.side))fire("confirmed",()=>{playAlert("confirmed",side);showNotice(bo?`BUY ${bo.name} @ ${fmt(bo.entry)}`:`CONFIRMED ${side} — ${getSymbol()}`,bo?`SL ${fmt(bo.stop)} · target ${fmt(bo.target)} · index ${fmt(bp.index.entry)} → ${fmt(bp.index.target)}`:`Reconfirmed, all gates pass. Index ${fmt(a.last_close)}.`)});
  else if(cur.pending&&(!prev.pending||prev.side!==cur.side))fire("start",()=>{playAlert("start",side);showNotice(`${side} setup detected — reconfirming`,`Not an entry yet: it must hold ${a.reconfirm?a.reconfirm.window:""}s.`)});
  else if(cur.failed&&prev.pending)fire("failed",()=>playAlert("failed",side));
}
(function(){const m=$("alertSound");if(!m)return;m.value=alertSoundOn?"1":"0";m.addEventListener("change",()=>{alertSoundOn=m.value==="1";try{localStorage.setItem("oi-alert-sound",m.value)}catch{}unlockAudio();if(alertSoundOn)playAlert("start","CE")})})();
async function loadAlertConfig(){
  try{const c=await api("/api/alerts/config"),on=Object.entries(c.channels||{}).filter(([,v])=>v).map(([k])=>({telegram:"Telegram",ntfy:"ntfy",email_api:"Email (API)",email_smtp:"Email (SMTP)"}[k]||k));
    $("alertChannels").textContent=on.length?`Alerts → beep + ${on.join(", ")}${c.background_poller?" · server watches even when this tab is closed":""}`:"Alerts → beep only (set TELEGRAM_*, NTFY_TOPIC or RESEND_API_KEY on the server for phone/email)"}catch{}}
(function(){const b=$("testAlert");if(!b)return;b.addEventListener("click",async()=>{
  unlockAudio();playAlert("confirmed","CE",true);
  try{if("Notification"in window&&Notification.permission==="default")await Notification.requestPermission()}catch{}
  b.disabled=true;b.textContent="Sending…";
  try{const r=await fetch("/api/alerts/test",{method:"POST"});const d=await r.json();
    toast(d.summary||d.message||"No server channel configured — beep only.");try{window.oiLoadPushStatus&&window.oiLoadPushStatus()}catch{}}
  catch(e){toast("Test alert failed: "+e.message)}finally{b.disabled=false;b.textContent="Test alert"}})})();
loadAlertConfig();
function renderSignalTime(a){
  const box=$("signalTimeBox");if(!box)return;
  const t=a&&a.timing;
  if(!t||!t.generated_at_text||!a.side){box.hidden=true;return}
  box.hidden=false;
  const stateTxt={FRESH:"OPEN — fresh signal",LATE:"CLOSED — late, don't chase",EXPIRED:"CLOSED — expired, wait for a new signal",UNKNOWN:"UNKNOWN — age can't be verified",FORMING:"Not confirmed yet"}[t.state]||"—";
  $("stGenerated").textContent=`${t.generated_at_text} (${t.side||a.side})`+(t.estimated?" · est.":"");
  $("stAge").textContent=t.age_minutes==null?"—":`${fmt(t.age_minutes,0)} min · ${fmt(t.age_bars,1)} bars`;
  $("stPrice").textContent=t.signal_price==null?"—":`${fmt(t.signal_price)} → ${fmt(t.price_now)}${t.moved_atr==null?"":` (${t.moved_atr>=0?"+":""}${fmt(t.moved_atr,2)} ATR)`}`;
  const st=$("stState");st.textContent=stateTxt;st.className=t.state==="FRESH"?"fresh":t.state==="LATE"?"late":t.state==="EXPIRED"||t.state==="UNKNOWN"?"expired":"";
  const r=t.rules||{};
  $("stNote").textContent=[`Rule: enter within ${r.fresh_minutes} min of the signal and before price moves ${r.max_chase_atr} ATR.`,
    t.lean_since_text?`${a.side} lean building since ${t.lean_since_text}.`:"",
    t.estimated?"Time is an estimate (server restarted or signal older than the replay window); the older time is used on purpose.":"",
    t.basis?`Basis: ${t.basis}.`:""].filter(Boolean).join(" ");
}
function renderDashChecks(a){
  const ul=$("dashChecks"),sm=$("dashSummary");if(!ul||!sm)return;
  ul.innerHTML="";
  const d=a&&a.dashboard;
  if(!d){sm.textContent="Dashboard read unavailable — the terminal cannot confirm a signal without it.";return}
  sm.textContent=d.available?`${d.action} · score ${d.score>=0?"+":""}${fmt(d.score,0)} · ${d.agree??0} of ${d.checks_available??0} checks agree${a.final?` · final: ${a.final.action}`:""}`:(d.headline||"Dashboard unavailable");
  (d.checks||[]).forEach(c=>{
    const li=document.createElement("li");li.className=c.state==="BULLISH"?"dc-bull":c.state==="BEARISH"?"dc-bear":"dc-flat";
    const b=document.createElement("b");b.textContent=c.state==="BULLISH"?"▲":c.state==="BEARISH"?"▼":c.state==="UNAVAILABLE"?"·":"—";
    const t=document.createElement("span");t.textContent=` ${c.name}: ${c.label}`;const sm2=document.createElement("small");sm2.textContent=c.detail||"";
    li.appendChild(b);li.appendChild(t);li.appendChild(sm2);ul.appendChild(li)});
}

// ---------------- v1.18.11: price / score / trend / decision follow the CHART VIEW and the selected CE/PE ----------------
// Index view  -> the cards describe the index (as before).
// Option view -> the cards describe the selected contract's own premium chart; the index trend and the index signal stay
//                visible next to it, and the decision combines both (a BUY needs the index signal AND the premium trend to agree).
const trendColor=t=>/UP|BULL/i.test(t||"")?"var(--green)":/DOWN|BEAR/i.test(t||"")?"var(--red)":"var(--yellow)";
function applyChartViewCards(){
  const idx=lastAnalysis,idxOk=idx&&idx.status==="ok",idxTrend=idxOk?(idx.regime_label||idx.regime||"—"):"—";
  const scoreSub=$("score")&&$("score").nextElementSibling,priceSub=$("priceChange");
  const optView=$("chartView").value==="option"&&paperSelected&&chartSource==="option";
  if(!optView){
    $("decision").style.color="";
    if($("regimeSub")&&idxOk)$("regimeSub").textContent=`Chart view: index · ${idx.regime_note||"Trend / sideways filter"}`;
    if(scoreSub&&idxOk)scoreSub.textContent="Index score · 0–100 · not win probability";
    return;
  }
  const sel=paperSelected,name=`${sel.type} ${sel.strike}`,o=lastOptionAnalysis&&lastOptionAnalysis._sel===sel?lastOptionAnalysis:null;
  if(priceSub&&idxOk)priceSub.textContent=`${name} premium · index ${fmt(idx.last_close)}`;
  if(!o){return}
  if(o.status!=="ok"){
    $("score").textContent="—";if(scoreSub)scoreSub.textContent=`${name}: ${o.message||"not enough premium candles for a score"}`;
    $("regime").textContent=idxTrend;$("regime").style.color=trendColor(idxTrend);
    if($("regimeSub"))$("regimeSub").textContent=`Index trend: ${idxTrend} · ${name} premium trend unavailable (thin / new contract)`;
    const t=`WAIT — no ${name} read`,s=`The ${name} premium has too few candles to score, so no buy decision is made for it. Index: ${(lastGateState&&lastGateState.status)||"WAIT"} ${(lastGateState&&lastGateState.side)||""}.`;
    $("decision").textContent=t;$("decision").style.color="var(--yellow)";$("bigDecision").textContent=t;
    if($("decisionSub"))$("decisionSub").textContent=s;if($("signalExplanation"))$("signalExplanation").textContent=s;
    return;
  }
  const optTrend=o.regime_label||o.regime||"—",bull=o.bull_score??0,bear=o.bear_score??0;
  $("score").textContent=`${bull}/100`;
  if(scoreSub)scoreSub.textContent=`${name} premium · buy-strength · bear ${bear}`+(idxOk?` · index ${idx.score}/100`:"");
  $("regime").textContent=optTrend;$("regime").style.color=trendColor(optTrend);
  if($("regimeSub"))$("regimeSub").textContent=`Chart (${name} premium): ${optTrend} · Index: ${idxTrend}`+(idxOk&&idx.regime_note?` — ${idx.regime_note}`:"");
  // decision: index signal side/status + premium direction
  const st=lastGateState||{},idxSide=st.side||null,idxStatus=st.status||"WAIT";
  const optUp=bull>bear&&bull>=55,optDown=bear>bull&&bear>=55;
  let text="WAIT",sub=`Premium trend ${optTrend}; index ${idxSide?idxSide+" "+idxStatus:"no side"}.`,color="var(--yellow)";
  const fb=st.final&&st.final.buy;
  if(idxStatus==="CONFIRMED"&&fb&&fb.option&&fb.option.instrument_key===sel.instrument_key){text=`BUY ${name}`;sub=fb.summary;color="var(--green)"}
  else if(idxStatus==="CONFIRMED"&&fb&&fb.option){text=`BUY is ${fb.option.name}`;sub=`This chart shows ${name}; the signal bought ${fb.option.name} (${fb.summary}).`;color="var(--yellow)"}
  else if(idxStatus==="CONFIRMED"&&idxSide===sel.type&&optUp){text=`BUY ${name}`;sub="Index signal CONFIRMED and the premium chart trend agree — enter only on a held retest.";color="var(--green)"}
  else if(idxStatus==="CONFIRMED"&&idxSide===sel.type){text=`WAIT — ${name} not confirming`;sub=`Index says ${idxSide} but the ${name} premium is not trending up yet (bull ${bull} / bear ${bear}).`}
  else if(idxSide&&idxSide!==sel.type&&idxStatus!=="WAIT"){text=`AVOID ${name}`;sub=`The index signal is ${idxSide} (${idxStatus}), the opposite side.`;color="var(--red)"}
  else if(idxSide===sel.type&&optUp){text=`WATCH ${name}`;sub="Index leans the same way and the premium is rising; hard gates not all met yet.";color="var(--blue)"}
  else if(optDown){text=`NO BUY — ${name} falling`;sub=`Premium trend is down (bear ${bear} vs bull ${bull}).`;color="var(--red)"}
  $("decision").textContent=text;$("decision").style.color=color;$("bigDecision").textContent=text;
  if($("decisionSub"))$("decisionSub").textContent=sub;
  if($("signalExplanation"))$("signalExplanation").textContent=sub;
}
// ---------------- Signal confirmation (backend checks + S/R room gate) ----------------
function renderSignal(){_renderSignalBase();try{applyChartViewCards()}catch(e){console.warn("chart-view cards",e)}}
function _renderSignalBase(){
  const a=lastAnalysis,gl=$("gateList"),rows=$("checkRows");
  if(!gl||!rows)return;
  gl.innerHTML="";rows.innerHTML="";$("extraInd").innerHTML="";
  if(!a||a.status!=="ok"){lastGateState=null;renderSignalTime(null);renderDashChecks(null);$("finalSignal").textContent="WAIT";$("finalSignalSub").textContent=(a&&a.message)||"Waiting for enough candles.";$("confirmBadge").textContent="WAIT";$("confirmBadge").className="pill muted";return}
  const side=a.side,dom=side==="CE"?a.bull_score:a.bear_score,gates=(a.gates||[]).map(g=>({...g}));
  // v1.19: the room-to-S/R check now runs on the server as part of the profit check (it also sets the target), so the
  // terminal, the alerts and the signal log all use the same decision.
  const ov=lastOptionVol,optRows=[];
  if(!ov||ov.none)optRows.push({label:"Option volume (selected contract)",reading:"excluded",detail:"Select a CE/PE contract to add its volume (the index itself has no volume).",cls:"sr-missing"});
  else if(ov.error)optRows.push({label:`Option volume (${ov.name})`,reading:"excluded",detail:"Option candles unavailable.",cls:"sr-missing"});
  else{
    const rel=ov.rel,reading=rel>=1.2?"▲ Expanding":rel>=0.6?"— Normal":"▼ Dry";
    optRows.push({label:`Option volume (${ov.name})`,reading,detail:`${fmt(rel,2)}× · last-3-bar avg ${fmt(ov.last3,0)} vs prior-20 avg ${fmt(ov.prior,0)} (closed bars only)`,cls:rel>=1.2?"sr-support":rel<0.6?"sr-resistance":""});
    if(side&&ov.type===side)gates.push({label:"Option volume not dry (≥ 0.6×)",pass:rel>=0.6,detail:`${fmt(rel,2)}× average — thin volume means wide spreads and unreliable fills`});
    else if(side)optRows.push({label:"Option volume gate",reading:"skipped",detail:`Selected contract is a ${ov.type}; the signal is ${side}. Select a ${side} to apply this gate.`,cls:"sr-missing"});
  }
  const failed=gates.filter(g=>!g.pass);
  let text="WAIT",sub="No side has enough aligned evidence. Stand aside.",cls="muted",color="var(--yellow)";
  if(side&&gates.length&&!failed.length){text=`CONFIRMED ${side} SETUP`;sub="Every gate passes. Enter only on a retest that holds, with the stop-loss and size set before entry.";cls="live";color=side==="CE"?"var(--green)":"var(--red)"}
  else if(side&&dom>=55&&failed.length<=2){text=`WATCH ${side}`;sub="No entry yet — waiting for: "+failed.map(g=>g.label).join("; ");cls="stale"}
  const gstatus=(side&&gates.length&&!failed.length)?"CONFIRMED":(side&&dom>=55&&failed.length<=2)?"WATCH":"WAIT";
  const fin=a.final||null;
  if(fin&&gstatus!=="CONFIRMED"&&(fin.level==="STALE"||fin.level==="CONFLICT"||fin.level==="PENDING"||fin.profit_rejected)){text=fin.action;sub=fin.headline;cls="stale";color="var(--yellow)"}
  if(fin&&fin.level==="CONFIRMED"&&fin.buy&&gstatus==="CONFIRMED"){text=fin.action;sub=fin.buy.summary;cls="live";color=side==="CE"?"var(--green)":"var(--red)"}
  lastGateState={side,text,status:gstatus,failed:failed.map(g=>g.label),dom,gates:gates.map(g=>({label:g.label,pass:g.pass,detail:g.detail||""})),timing:a.timing||null,final:fin};
  alertTransitions(a,gstatus,side,fin);
  const tm=a.timing&&a.timing.generated_at_text?a.timing:null;
  if($("decisionSub"))$("decisionSub").textContent=(gstatus==="CONFIRMED"&&fin&&fin.buy?`Buy ${fmt(fin.buy.option.entry)} · SL ${fmt(fin.buy.option.stop)} · target ${fmt(fin.buy.option.target)}`:gstatus==="CONFIRMED"?"Gates pass — enter only on a held retest":fin&&fin.profit_rejected?"No buy — profit check failed":"No entry — hard gates not met")+(tm&&side?` · signal ${tm.generated_at_text}`:"");
  renderSignalTime(a);renderDashChecks(a);renderBuyPlan(a);autoSwitchOption(a);
  $("finalSignal").textContent=text;$("finalSignal").style.color=color;$("finalSignalSub").textContent=sub;
  $("confirmBadge").textContent=gstatus==="CONFIRMED"&&fin&&fin.buy?"BUY":text.split(" SETUP")[0].split(" —")[0];$("confirmBadge").className="pill "+cls;
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
  renderGuardStatus();
}
// ---------------- v1.19: buy plan card + automatic switch to the signal's option ----------------
function renderBuyPlan(a){
  const box=$("buyPlanBox");if(!box)return;
  const fin=a&&a.final,plan=(fin&&fin.buy)||(a&&a.buy_plan)||null,pick=a&&a.option_pick;
  if(!plan&&!pick){box.hidden=true;return}
  box.hidden=false;
  const o=plan&&plan.option,ix=plan&&plan.index,ok=!!(plan&&plan.ok&&fin&&fin.level==="CONFIRMED");
  const st=$("bpState");
  st.textContent=ok?"BUY":plan?"NO BUY":(fin&&fin.level)||"WATCH";st.className="pill "+(ok?"live":plan?"stale":"muted");
  $("bpTitle").textContent=ok?"Buy signal":plan?"Confirmed setup — profit check":"Option lined up for this signal";
  const nm=o?o.name:pick?`${pick.name||pick.strike+" "+pick.type}`:"—";
  $("bpOption").textContent=nm+((o&&o.label)||(pick&&pick.label)?` · ${(o&&o.label)||pick.label}`:"");
  $("bpBuy").textContent=o?fmt(o.entry):pick?`${fmt(pick.ltp)} (now)`:"—";
  $("bpSl").textContent=o?fmt(o.stop):"after confirmation";
  $("bpTgt").textContent=o?fmt(o.target):"after confirmation";
  $("bpIndex").textContent=ix?`${fmt(ix.entry)} / ${fmt(ix.stop)} / ${fmt(ix.target)}`:"—";
  $("bpRr").textContent=ix?`${fmt(ix.rr,2)} (risk ${fmt(ix.risk_pts,1)} pts, reward ${fmt(ix.reward_pts,1)} pts)`:"—";
  $("bpMoney").textContent=o&&o.qty?`+₹${fmt(o.exp_profit_rs,0)} / −₹${fmt(o.exp_loss_rs,0)} (qty ${o.qty})`:"—";
  $("bpBasis").textContent=ix?ix.target_basis:"—";
  const bad=plan?(plan.checks||[]).filter(c=>!c.pass):[];
  $("bpNote").textContent=ok?"Paper signal, recorded in the Daily Signal Log with these levels. Index levels decide the exit; the premium levels are delta/gamma estimates.":
    plan?("No buy: "+bad.map(c=>`${c.label} — ${c.detail}`).join(" · ")):"Strike chosen automatically (2 strikes in the money on the signal side, by default). Stop-loss and target are set when the signal is confirmed and passes the profit check.";
}
let lastAutoOptionKey=null,lastAutoSide=null,autoIdleSince=null;
try{lastAutoOptionKey=sessionStorage.getItem("oiAutoPick")||null}catch{}
// v1.20.27: once the setup is over, an auto-picked option is no longer relevant (6 Nov: the chart stayed on a 24600 PE from the
// morning while NIFTY rallied to 25,030). If the contract on screen was picked automatically (not by you) and there has been no
// setup for 10 minutes - or 2 minutes when the engine now leans the other side - the chart goes back to the index.
function autoRevertOption(a){
  if(($("autoOption")&&$("autoOption").value)==="off"||$("chartView").value!=="option"||!paperSelected||!lastAutoOptionKey||paperSelected.instrument_key!==lastAutoOptionKey){autoIdleSince=null;return}
  if(!autoIdleSince){autoIdleSince=Date.now();return}
  const opposite=!!(a&&a.side&&a.side!==paperSelected.type),idle=Date.now()-autoIdleSince;
  if(idle<(opposite?2:10)*60000)return;
  autoIdleSince=null;lastAutoSide=null;lastAutoOptionKey=null;try{sessionStorage.removeItem("oiAutoPick")}catch{}   // the next setup may pick again
  $("chartView").value="index";try{$("chartView").dispatchEvent(new Event("change"))}catch{refreshChartSource()}
  toast(opposite?`Engine now leans ${a.side}: chart back to the index (the auto-picked ${paperSelected.type} no longer fits)`:"No setup for 10 minutes: chart back to the index");
}
function autoSwitchOption(a){
  const pk=a&&a.option_pick,lvl=a&&a.final&&a.final.level;
  if(!pk||!pk.instrument_key||!["WATCH","PENDING","CONFIRMED"].includes(lvl)){if(!lvl||lvl==="WAIT")lastAutoSide=null;try{autoRevertOption(a)}catch{}return}
  autoIdleSince=null;
  if(($("autoOption")&&$("autoOption").value)==="off"||pk.symbol!==getSymbol())return;
  if(lastAutoOptionKey===pk.instrument_key)return;              // once per contract: after that your own choice is left alone
  // v1.20.2: stay on the contract chosen for this setup. The "2 strikes ITM" pick moves with every 50-pt index move, and following it
  // made the chart / last price jump between strikes (very different premiums). Switch again only when the side changes or a new
  // setup starts after a WAIT.
  if(lastAutoSide===pk.type&&paperSelected&&paperSelected.type===pk.type)return;
  lastAutoSide=pk.type;
  lastAutoOptionKey=pk.instrument_key;try{sessionStorage.setItem("oiAutoPick",pk.instrument_key)}catch{}
  if(pk.lot_size)chainLotSize=pk.lot_size;
  if(!(paperSelected&&paperSelected.instrument_key===pk.instrument_key))selectPaperContract(pk.type,pk.strike,pk.instrument_key,pk.ltp,pk.expiry,null);
  if($("chartView").value!=="option"){$("chartView").value="option";try{$("chartView").dispatchEvent(new Event("change"))}catch{refreshChartSource()}}
  toast(`${lvl} ${pk.type}: chart switched to ${pk.name||pk.strike+" "+pk.type} (${pk.label}) @ ${fmt(pk.ltp)}`);
}
// ---------------- v1.19.3: automatic trendlines (static/trendlines.js finds them; drawn here) ----------------
let tlSeries=[],lastTrendlines=null;
function drawTrendlines(cs){
  for(const s of tlSeries){try{chart.removeSeries(s)}catch{}}tlSeries=[];
  const cell=$("chartTrend");
  if($("showTL")?.checked===false||!window.OITrendlines){lastTrendlines=null;if(cell)cell.textContent="off";return}
  const t=window.OITrendlines.computeTrendlines(cs);lastTrendlines=t;
  const LS=LightweightCharts.LineStyle,parts=[];
  for(const tl of [t.support,t.resistance]){
    if(!tl)continue;
    const sup=tl.kind==="support",color=sup?"#3fd0c9":"#ffb547";
    const s=chart.addLineSeries({color,lineWidth:2,lineStyle:tl.broken?LS.Dashed:LS.Solid,priceLineVisible:false,lastValueVisible:true,
      title:(sup?"TL S":"TL R")+(tl.broken?" ✕":""),crosshairMarkerVisible:false,autoscaleInfoProvider:()=>null});
    s.setData([{time:tl.from.time,value:tl.from.value},{time:tl.to.time,value:tl.to.value}]);tlSeries.push(s);
    const arrow=tl.direction==="rising"?"↗":tl.direction==="falling"?"↘":"→";
    parts.push(`${sup?"Support":"Resistance"} ${arrow} ${fmt(tl.value)}${tl.broken?(sup?" — BROKEN below":" — BROKEN above"):` (${tl.touches} touches)`}`);
  }
  if(cell)cell.textContent=parts.length?parts.join(" · "):"none clear";
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

function analyzeRecentPatterns(candles){
  const rows=[]; const xs=(candles||[]).map(c=>({time:toChartTime(c.time),open:+c.open,high:+c.high,low:+c.low,close:+c.close})).filter(c=>Number.isFinite(c.time)&&[c.open,c.high,c.low,c.close].every(Number.isFinite)).sort((a,b)=>a.time-b.time);
  const start=Math.max(1,xs.length-8);
  for(let i=start;i<xs.length;i++){
    const p=xs[i-1],c=xs[i],body=Math.abs(c.close-c.open),range=Math.max(c.high-c.low,1e-9),ratio=body/range;
    let name="Bullish candle",bias=c.close>c.open?"BULLISH":c.close<p.close?"BEARISH":"NEUTRAL",meaning="Limited standalone information";
    if(ratio<=.12){name="Doji / indecision";bias="NEUTRAL";meaning="Buyers and sellers are balanced; wait for a break."}
    else if(c.close>c.open&&p.close<p.open&&c.open<=p.close&&c.close>=p.open){name="Bullish engulfing";bias="BULLISH";meaning="Potential bullish reversal; needs follow-through."}
    else if(c.close<c.open&&p.close>p.open&&c.open>=p.close&&c.close<=p.open){name="Bearish engulfing";bias="BEARISH";meaning="Potential bearish reversal; needs follow-through."}
    else if(c.close>c.open&&c.close>p.high&&ratio>=.55){name="Bullish breakout candle";bias="BULLISH";meaning="Strong close above prior high; check nearby resistance."}
    else if(c.close<c.open&&c.close<p.low&&ratio>=.55){name="Bearish breakdown candle";bias="BEARISH";meaning="Strong close below prior low; check nearby support."}
    else if(c.close<c.open){name="Bearish candle";bias="BEARISH";meaning="Selling pressure on this candle; trend context still matters."}
    rows.push({time:c.time,name,bias,meaning,price:c.close});
  }
  const last=xs.slice(-5); let up=0,down=0;
  last.forEach(c=>{if(c.close>c.open)up++;else if(c.close<c.open)down++});
  const closes=xs.map(c=>c.close);const e9=calcEMA(closes,9).at(-1),e21=calcEMA(closes,21).at(-1),lastClose=closes.at(-1);
  let trend="MIXED / WAIT";if(up>=3&&lastClose>e9&&e9>e21)trend="BULLISH MOMENTUM";else if(down>=3&&lastClose<e9&&e9<e21)trend="BEARISH MOMENTUM";
  return {rows,trend,up,down};
}
function renderRecentPatterns(candles){
  const out=analyzeRecentPatterns(candles),trendEl=$("patternTrend"),summary=$("patternSummary"),tbody=$("patternRows");
  if(!trendEl||!summary||!tbody)return;
  trendEl.textContent=out.trend;trendEl.className="pattern-badge "+(out.trend.startsWith("BULLISH")?"bull":out.trend.startsWith("BEARISH")?"bear":"neutral");
  summary.textContent=`Last 5 candles: ${out.up} bullish, ${out.down} bearish. Trend requires candle direction plus EMA 9/21 alignment. This is context only; wait when patterns conflict with the broader signal or data is stale.`;
  if(!out.rows.length){tbody.innerHTML='<tr><td colspan="4" class="empty">Need at least two candles.</td></tr>';return}
  tbody.innerHTML=out.rows.slice().reverse().map(r=>`<tr><td>${new Date(r.time*1000).toLocaleString()}</td><td>${r.name}</td><td class="${r.bias==='BULLISH'?'green':r.bias==='BEARISH'?'red':''}">${r.bias}</td><td>${r.meaning}</td></tr>`).join('');
  // Candle patterns remain available in the separate analysis panel and signal engine.
  // Do not draw pattern labels/markers over the price chart (prevents overlapping annotations).
  try{candleSeries.setMarkers([]);}catch{}
}

function renderCandles(candles){
  currentCandles=candles||[];
  const valid=currentCandles.map(c=>({time:toChartTime(c.time),open:+c.open,high:+c.high,low:+c.low,close:+c.close,volume:+c.volume||0})).filter(c=>Number.isFinite(c.time)&&[c.open,c.high,c.low,c.close].every(Number.isFinite)).sort((a,b)=>a.time-b.time);
  const unique=[];for(const c of valid){if(!unique.length||unique[unique.length-1].time!==c.time)unique.push(c)}
  candleSeries.setData(unique);
  renderRecentPatterns(unique);
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
  try{drawTrendlines(unique)}catch(e){console.warn("trendlines",e)}
  applyChartRange(unique.length);
  const vwapLegend=document.querySelector(".vwap-legend"),vwapIsVol=!!vw.at(-1)?.volumeBased;
  if(vwapLegend)vwapLegend.textContent=vwapIsVol?"━ VWAP":"━ Session avg price (no index volume)";
  if($("vwapLabel"))$("vwapLabel").textContent=vwapIsVol?"VWAP (session)":"Session avg price (no volume)";
  if(unique.length){const last=unique.at(-1),rsi=rsiValue(closes),atr=atrValue(unique),volume=last.volume,avgVol=unique.slice(-21,-1).reduce((a,c)=>a+c.volume,0)/Math.max(1,unique.slice(-21,-1).length);$("lastPrice").textContent=fmt(liveOptLtp()??last.close);$("chartSubtitle").textContent=chartSource==="option"&&paperSelected?`${paperSelectedLabel()} · ${getInterval()}-minute premium OHLC`:`${getSymbol()==="NIFTY"?"NIFTY 50":"BANK NIFTY"} · ${getInterval()}-minute OHLC`;
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

async function api(url, options={}){
  // Preserve caller-supplied method, headers and body. Push registration uses POST;
  // dropping options here silently converted it to GET and caused HTTP 405.
  // v1.18.11: every request has a deadline. When a phone suspends the tab mid-request the promise can stay pending forever,
  // which used to leave refreshBusy stuck on and froze ALL later refreshes ("Updated 7:41" while it was already 7:54).
  const ctl=new AbortController(),limit=options.timeout||20000,timer=setTimeout(()=>ctl.abort(),limit);
  let res;try{res=await fetch(url,{...options,signal:options.signal||ctl.signal})}catch(e){if(e&&e.name==="AbortError")throw new Error("Request timed out - tap Refresh data");throw e}finally{clearTimeout(timer)}
  let data;try{data=await res.json()}catch{data={detail:await res.text()}}
  if(!res.ok)throw new Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail||data));
  return data;
}
// Relative volume of the selected option: average of the last 3 COMPLETED bars vs the prior 20.
// The still-forming candle is excluded (its partial volume dragged the ratio down and made liquid contracts look "dry"),
// today's session is preferred as the baseline, and block-trade bars are capped at 3x the median so one spike cannot
// inflate the baseline.
function optionRelVolume(cs,intervalMin){
  let bars=(cs||[]).slice();const nowSec=Date.now()/1000,ivSec=(intervalMin||5)*60;
  if(bars.length&&bars[bars.length-1].time+ivSec>nowSec)bars=bars.slice(0,-1);
  const sess=bars.filter(c=>istDayKey(c.time)===istDayKey(nowSec)),use=sess.length>=23?sess:bars;
  if(use.length<23)return null;
  const v=use.map(c=>c.volume),last3=v.slice(-3).reduce((a,b)=>a+b,0)/3,priorRaw=v.slice(-23,-3);
  const sorted=[...priorRaw].sort((a,b)=>a-b),median=(sorted[9]+sorted[10])/2,cap=median>0?median*3:Infinity;
  const prior=priorRaw.map(x=>Math.min(x,cap)).reduce((a,b)=>a+b,0)/20;
  return prior>0?{rel:last3/prior,last3,prior,total:v.reduce((a,b)=>a+b,0),bars:use.length,sessionOnly:sess.length>=23}:null;
}
let optVolAt=0,optVolKey="";
async function loadOptionVolume(){
  if(!paperSelected){lastOptionVol={none:true};renderSignal();return}
  const sel=paperSelected,name=`${sel.type} ${sel.strike}`,vk=`${sel.instrument_key}|${getInterval()}`;
  if(vk===optVolKey&&Date.now()-optVolAt<5000)return;      // v1.19.6: analysis now arrives every second; volume needs no more than 5 s
  optVolKey=vk;optVolAt=Date.now();
  try{
    const d=await api(`/api/candles?symbol=${encodeURIComponent(sel.symbol)}&instrument_key=${encodeURIComponent(sel.instrument_key)}&interval=${getInterval()}&days=5`);
    const r=optionRelVolume(prepCandles(d.candles),getInterval());
    if(paperSelected!==sel)return;
    lastOptionVol=r?{...r,type:sel.type,name}:{error:true,name};
  }catch{lastOptionVol={error:true,name}}
  renderSignal();
}
// Index analysis + index S/R + option volume. Runs in BOTH chart views so the signal never goes stale
// or borrows the option-premium chart's support/resistance.
async function refreshSignalContext(){
  const sym=getSymbol(),interval=getInterval();
  const sel=paperSelected&&$("chartView").value==="option"?paperSelected:null;
  const optPromise=sel?api(`/api/option-analysis?symbol=${encodeURIComponent(sel.symbol)}&instrument_key=${encodeURIComponent(sel.instrument_key)}&interval=${interval}&mode=${getMode()}&score_min=${getScoreMin()}`).catch(e=>({status:"unavailable",message:e.message})):null;
  const candlePromise=api(`/api/candles?symbol=${sym}&interval=${interval}&days=10`);
  const analysisPromise=api(`/api/analysis?symbol=${sym}&interval=${interval}&mode=${getMode()}&score_min=${getScoreMin()}&reconfirm=${getReconfirm()}`);
  const c=await candlePromise;
  lastIndexSR=computeSR(prepCandles(c.candles),interval,sym,true);
  let a;try{a=await analysisPromise}catch(e){a={status:"unavailable",decision:"WAIT",message:`Analysis unavailable: ${e.message}`,score:0,bull_score:0,bear_score:0};}
  lastAnalysis=a;lastAnalysisAt=Date.now();
  if(optPromise){const o=await optPromise;if(sel===paperSelected)lastOptionAnalysis={...o,_sel:sel}}
  $("score").textContent=a.status==="ok"?`${a.score}/100`:"—";$("regime").textContent=a.status==="ok"?(a.regime_label||a.regime||"—"):"—";
  $("regime").style.color=a.regime==="BULLISH"?"var(--green)":a.regime==="BEARISH"?"var(--red)":"var(--yellow)";if($("regimeSub"))$("regimeSub").textContent=a.status==="ok"?(a.regime_note||"Trend / sideways filter"):"Trend / sideways filter";
  $("bullScore").textContent=a.bull_score??"—";$("bearScore").textContent=a.bear_score??"—";
  renderSignal();
}
async function refresh(){
  sendLiveSub();lastFullRefresh=Date.now();
  if($("chartView").value==="option"){await refreshChartSource();try{await refreshSignalContext()}catch(e){$("analysisNote").textContent=e.message}loadOptionVolume();$("lastUpdated").textContent=`Updated ${new Date().toLocaleTimeString()} · option premium view${paperSelected?"":" · select an option contract"}`;return}
  const manual=!refreshBusy;if(manual){$("refreshBtn").disabled=true;$("refreshBtn").textContent="Loading…"}
  try{
    const sym=getSymbol(),interval=getInterval();
    // Candles + price render immediately. The heavier analysis runs on its own loop so it can never hold the chart back.
    const c=await api(`/api/candles?symbol=${sym}&interval=${interval}&days=10`);
    lastVolumeSource=c.volume_source||null;
    renderCandles(c.candles);
    if(lastVolumeSource)$("chartNote").textContent+=` Volume and VWAP weights come from ${lastVolumeSource} (the index itself has no volume).`;
    const f=c.feed;
    if(!marketOpen)setUpdated(`Market closed · last session data (nothing changes until 09:15) · checked ${new Date().toLocaleTimeString()}`);
    else if(Date.now()-liveLabelAt>3000)$("lastUpdated").textContent=`Updated ${new Date().toLocaleTimeString()}`+(f&&f.price_source&&f.price_source!=="CLOSED"?` · price: ${f.price_source==="WEBSOCKET"?"live tick":f.price_source==="REST_QUOTE"?"REST quote":"candles only"}${f.bars_behind>0?` · candle feed ${f.bars_behind} bar${f.bars_behind>1?"s":""} behind`:""}`:"");
    runAnalysis(manual);
  }catch(e){toast("Data load failed: "+e.message);$("analysisNote").textContent=e.message}
  finally{$("refreshBtn").disabled=false;$("refreshBtn").textContent="Refresh data"}
}
let analysisBusy=false,analysisBusySince=0;
async function runAnalysis(force){
  if(analysisBusy&&Date.now()-analysisBusySince>30000)analysisBusy=false;     // watchdog: a stuck run must not block every later one
  if(analysisBusy||(!force&&Date.now()-lastAnalysisAt<4000))return;   // analysis is heavy: at most every ~4 s, never overlapping
  analysisBusy=true;analysisBusySince=Date.now();
  try{
    const sym=getSymbol(),interval=getInterval();
    let a;try{a=await api(`/api/analysis?symbol=${sym}&interval=${interval}&mode=${getMode()}&score_min=${getScoreMin()}&reconfirm=${getReconfirm()}`)}catch(e){a={status:"unavailable",decision:"WAIT",message:`Analysis unavailable: ${e.message}`,score:0,bull_score:0,bear_score:0};}
    if(sym!==getSymbol()||interval!==getInterval())return;   // the user switched symbol/timeframe while this was running
    applyAnalysis(a);
  }catch(e){$("analysisNote").textContent=e.message}
  finally{analysisBusy=false}
}
// v1.19.6: one renderer for the analysis, used by the REST poll AND by the result the live socket pushes the moment it is computed.
function applyAnalysis(a){
  {
    try{const cp=a.chart_pattern,el=$("chartPattern");if(el){if(cp&&cp.name&&cp.name!=="None"){const col=cp.stage==="breakout"?(cp.side==="CE"?"var(--green)":"var(--red)"):"";
      el.innerHTML=`<span style="color:${col};font-weight:700">${escapeHtml(cp.name)}</span> · ${escapeHtml(cp.detail||"")}`+(cp.trigger!=null?` · trigger ${fmt(cp.trigger)}`:"")+(cp.target!=null?` · target ${fmt(cp.target)}`:"")+(cp.invalidation!=null?` · invalid ${fmt(cp.invalidation)}`:"");}
      else el.textContent=cp?cp.detail||"none":"—";}}catch{}
    $("score").textContent=a.status==="ok"?`${a.score}/100`:"—";
    $("regime").textContent=a.status==="ok"?(a.regime_label||a.regime||"—"):"—";
    $("decision").textContent=a.decision||"WAIT";
    $("bigDecision").textContent=a.decision||"WAIT";$("decisionSub").textContent=marketOpen?"Bias only; confirm retest":"Last session data (market closed) — bias only";
    $("bullScore").textContent=a.bull_score??"—";$("bearScore").textContent=a.bear_score??"—";
    $("ema9").textContent=fmt(a.ema9);$("ema21").textContent=fmt(a.ema21);$("vwap").textContent=fmt(a.vwap_approx);
    $("rsi").textContent=fmt(a.rsi);$("atr").textContent=fmt(a.atr);$("relVol").textContent=a.relative_volume==null?"—":`${fmt(a.relative_volume)}x`;if($("vwapLabel"))$("vwapLabel").textContent=String(a.vwap_source||"").startsWith("VOLUME_VWAP")?"VWAP (session)":"Session avg price (no volume)";
    $("breakHigh").textContent=fmt(a.breakout_high);$("breakLow").textContent=fmt(a.breakdown_low);
    $("analysisNote").textContent=[a.note||a.message||"Analysis loaded.",a.score_basis].filter(Boolean).join(" · ");
    $("signalExplanation").textContent=a.note||a.message||"Confirm breakout and retest before any trade.";
    $("regime").style.color=a.regime==="BULLISH"?"var(--green)":a.regime==="BEARISH"?"var(--red)":"var(--yellow)";if($("regimeSub"))$("regimeSub").textContent=a.status==="ok"?(a.regime_note||"Trend / sideways filter"):"Trend / sideways filter";
    lastAnalysis=a;lastAnalysisAt=Date.now();renderSignal();loadOptionVolume();try{aiLiveWatch(a)}catch{}
  }
}
function renderMarketPill(m){
  const el=$("marketStatus");if(!el||!m)return;
  el.textContent=m.open?"Market open (IST)":"Market closed (IST)";
  el.className=m.open?"pill live":"pill muted";
  el.title=(m.ist_time||"")+" IST · "+(m.note||"");
}
async function hardReloadApp(){
  try{const keys=await caches.keys();await Promise.all(keys.map(k=>caches.delete(k)))}catch{}
  try{const regs=await navigator.serviceWorker.getRegistrations();await Promise.all(regs.map(r=>r.update().catch(()=>{})))}catch{}
  location.reload();
}
async function checkStatus(){
  try{
    const s=await api("/api/status"),el=$("apiStatus");
    $("buildInfo").textContent=`page v${FRONTEND_VERSION} · server v${s.version||"?"}`;
    if(s.version&&s.version!==FRONTEND_VERSION){
      // v1.18.11: update by itself (a phone has no Ctrl+F5). Clear the app's saved files once per new server version and reload;
      // if that did not fix it, show a button instead of looping.
      const b=$("staleBanner"),tried=(()=>{try{return sessionStorage.getItem("oi-updated-for")===s.version}catch{return false}})();
      if(!tried){try{sessionStorage.setItem("oi-updated-for",s.version)}catch{};b.textContent="Updating to v"+s.version+"…";b.hidden=false;await hardReloadApp();return}
      b.textContent="⚠ A newer version (v"+s.version+") is available. ";const btn=document.createElement("button");btn.type="button";btn.textContent="Update now";btn.onclick=hardReloadApp;b.appendChild(btn);b.hidden=false
    }
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
    const call=r.call_options||{},put=r.put_options||{},cm0=call.market_data||{},pm0=put.market_data||{};
    const lc=liveTickMap[call.instrument_key],lp=liveTickMap[put.instrument_key];
    const newer=t=>t&&t.ltp!=null&&(t.fresh||(t._rx||0)>optionChainAt);   // v1.20.2: newest price wins - no flipping back to an older REST snapshot
    const cm=newer(lc)?{...cm0,ltp:lc.ltp,oi:lc.oi??cm0.oi}:cm0,pm=newer(lp)?{...pm0,ltp:lp.ltp,oi:lp.oi??pm0.oi}:pm0;
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
  $("chainStatus").textContent=`Showing ${filtered.length} of ${rows.length} strikes${chainLotSize?` · lot size ${chainLotSize}`:""}${optionChainSpot?` · spot ${fmt(optionChainSpot)} · ATM ${fmt(atmStrike,0)}`:" · spot unavailable"}. ATM row highlighted. Select Use CE/Use PE. Near-ATM prices update from live WebSocket ticks; other rows are REST snapshots (refreshed every 10 s while the market is open).`;
}
function renderChain(data){
  optionChainRows=data.data||data.chain||[];optionChainAt=Date.now();chainLotSize=data.lot_size||null;
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
    optionChainRows=data.data||[];optionChainAt=Date.now();chainLotSize=data.lot_size||chainLotSize;renderFilteredChain({scroll:false});
  }catch{}
}
$("refreshBtn").addEventListener("click",refresh);
$("symbol").addEventListener("change",()=>{lastAutoSide=null;lastAutoOptionKey=null;
  optionChainRows=[];chainLotSize=null;chartFitKey=null;lastDerivatives=null;
  $("chainRows").innerHTML='<tr><td colspan="8" class="empty">Loading expiries for this underlying…</td></tr>';
  renderLiveTicks([]);connectLiveSocket();refresh();
  loadContracts(true); // expiry list -> chain -> derivatives for that real expiry
});
$("interval").addEventListener("change",refresh);
(function(){const m=$("scoreMin");if(!m)return;try{const v=localStorage.getItem("oi-score-min");if(v&&["60","65","70"].includes(v))m.value=v}catch{}m.addEventListener("change",()=>{try{localStorage.setItem("oi-score-min",m.value)}catch{}refresh()})})();
(function(){const m=$("reconfirmSec");if(!m)return;try{const v=localStorage.getItem("oi-reconfirm");if(v&&[...m.options].some(o=>o.value===v))m.value=v}catch{}m.addEventListener("change",()=>{try{localStorage.setItem("oi-reconfirm",m.value)}catch{}runAnalysis(true)})})();
(function(){const m=$("signalMode");if(!m)return;try{const v=localStorage.getItem("oi-signal-mode");if(v&&[...m.options].some(o=>o.value===v))m.value=v}catch{}m.addEventListener("change",()=>{try{localStorage.setItem("oi-signal-mode",m.value)}catch{}refresh()})})();
$("loadContracts").addEventListener("click",()=>loadContracts(true));
$("loadChain").addEventListener("click",()=>loadChain());
$("chartView").addEventListener("change",refreshChartSource);
["showEma","showVWAP","showSR","showTL"].forEach(id=>$(id)&&$(id).addEventListener("change",()=>renderCandles(currentCandles)));
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
let autoTimer=null,refreshBusy=false,refreshBusySince=0;
initChart();checkStatus();
// Auto refresh: user-selectable (default 2 s). It also runs after market close so REST quote/status
// and stale-data messaging can update. It never overlaps a prior refresh; a slow request delays the
// next cycle rather than causing a backlog. WebSocket ticks update the forming candle between cycles.
function startAutoRefresh(){
  clearInterval(autoTimer);
  const sec=Math.max(2,Number($("refreshEvery").value)||2);
  try{localStorage.setItem("oi-refresh-v2",String($("refreshEvery").value))}catch{}
  sendLiveSub();     // v1.19.8: the selected rate (0.1 s default) drives the live socket; REST reload below stays >= 2 s
  autoTimer=setInterval(async()=>{
    if(document.visibilityState!=="visible")return;
    if(refreshBusy&&Date.now()-refreshBusySince>30000)refreshBusy=false;        // watchdog
    if(refreshBusy)return;
    sendLiveSub();
    // v1.19.6: while the live socket is pushing candles + analysis, the full REST reload only runs every 30 s as a safety net.
    if(livePushFresh()&&Date.now()-lastFullRefresh<30000)return;
    if(!marketOpen&&Date.now()-lastFullRefresh<30000)return;     // v1.19.9: market closed - prices cannot change, reload every 30 s only
    refreshBusy=true;refreshBusySince=Date.now();
    try{await refresh()}catch(e){console.warn("Auto-refresh failed:",e)}finally{refreshBusy=false}
  },sec*1000);
}
try{const saved=localStorage.getItem("oi-refresh-v2");if(saved&&[...$("refreshEvery").options].some(o=>o.value===saved))$("refreshEvery").value=saved}catch{}
$("refreshEvery").addEventListener("change",startAutoRefresh);startAutoRefresh();refresh();
// Coming back to the app (unlock the phone, switch back from another app, reconnect to the network): throw away anything that
// was in flight while the tab was frozen, then reload everything at once, instead of waiting for a stuck request.
let lastResumeAt=0;
function resumeNow(){
  if(document.visibilityState!=="visible"||Date.now()-lastResumeAt<1500)return;
  lastResumeAt=Date.now();
  refreshBusy=false;analysisBusy=false;restPriceBusy=false;try{paperRefreshBusy=false}catch{}
  chartRequestId++;
  refresh();checkStatus();
  try{refreshDerivatives()}catch{}
  try{if(marketOpen)refreshChainQuiet()}catch{}
  try{if(!liveSocket||liveSocket.readyState!==1)connectLiveSocket()}catch{}
  startAutoRefresh();
}
document.addEventListener("visibilitychange",resumeNow);
window.addEventListener("pageshow",resumeNow);
window.addEventListener("focus",resumeNow);
window.addEventListener("online",resumeNow);
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
// ---------------- Real-time index price (V3 WebSocket tick -> forming candle) ----------------
let optionChainAt=0,liveTickMap={},lastChainPaint=0,lastIdxTick=null,restPriceBusy=false;
function setLivePrice(ltp,closePrice,ageText,srcText,netChange=null,netChangePercent=null){
  if($("chartView").value!=="index")return;
  $("lastPrice").textContent=fmt(ltp);
  const cp=Number(closePrice), supplied=Number(netChange);
  let ch=Number.isFinite(supplied)&&netChange!==null&&netChange!==""?supplied:
    (Number.isFinite(cp)&&cp>0?Number(ltp)-cp:null);
  let pc=Number.isFinite(Number(netChangePercent))&&netChangePercent!==null&&netChangePercent!==""?Number(netChangePercent):
    (ch!==null&&Number.isFinite(cp)&&cp>0?ch/cp*100:null);
  if(ch!==null){$("priceChange").textContent=`${ch>=0?"+":""}${fmt(ch)}${pc===null?"":` (${pc>=0?"+":""}${fmt(pc)}%)`} · ${srcText}${ageText?` ${ageText}`:""}`;$("priceChange").style.color=ch>=0?"var(--green)":"var(--red)"}
  else $("priceChange").textContent=`Daily change unavailable · ${srcText}${ageText?` ${ageText}`:""}`;
}
function applyIndexTick(t){
  if(!t||!Number.isFinite(Number(t.ltp))||!t.fresh)return;
  const ltp=Number(t.ltp);if(!t.rest)lastIdxTick=Date.now();lastIdxLtp=ltp;
  if(!t.rest)setLivePrice(ltp,Number(t.close_price),`${fmt(t.age_seconds,1)}s ago`,"● LIVE tick");
  if(chartSource!=="index"||$("chartView").value!=="index"||!currentCandles.length||!marketOpen)return;
  const step=getInterval()*60,last=currentCandles[currentCandles.length-1],lt=toChartTime(last.time);
  if(!Number.isFinite(lt))return;
  const nowSec=Math.floor(Date.now()/1000),k=Math.floor((nowSec-lt)/step);
  let c;
  if(k<=0){ // still inside the latest candle: stretch it
    c={time:lt,open:+last.open,high:Math.max(+last.high,ltp),low:Math.min(+last.low,ltp),close:ltp};
    last.high=c.high;last.low=c.low;last.close=c.close;
  }else if(istDayKey(nowSec)===istDayKey(lt)){ // new bar of the same session: open it from the tick
    const nt=lt+k*step;c={time:nt,open:ltp,high:ltp,low:ltp,close:ltp};
    currentCandles.push({time:nt,open:ltp,high:ltp,low:ltp,close:ltp,volume:0});
  }else return; // first bar of a new session: the next REST refresh supplies it
  try{candleSeries.update(c)}catch{}
}
// Selected option on the chart: every WebSocket tick of that contract moves the last premium candle (no REST wait).
// v1.20.2: in the option view the REST candles can lag the live tick by up to a minute; the header price must not jump back to them.
function liveOptLtp(){
  if(!(chartSource==="option"&&$("chartView").value==="option"&&paperSelected))return null;
  const t=liveTickMap[paperSelected.instrument_key];
  return t&&t.fresh&&Number(t.ltp)>0&&Date.now()-(t._rx||0)<5000?Number(t.ltp):null;
}
function applyOptionTick(t){
  if(!t||!t.fresh||!Number.isFinite(Number(t.ltp))||!currentCandles.length||!marketOpen)return;
  if(chartSource!=="option"||$("chartView").value!=="option"||!paperSelected||t.instrument_key!==paperSelected.instrument_key)return;
  const ltp=Number(t.ltp),step=getInterval()*60,last=currentCandles[currentCandles.length-1],lt=toChartTime(last.time);
  if(!Number.isFinite(lt))return;
  const nowSec=Math.floor(Date.now()/1000),k=Math.floor((nowSec-lt)/step);let c;
  if(k<=0){c={time:lt,open:+last.open,high:Math.max(+last.high,ltp),low:Math.min(+last.low,ltp),close:ltp};last.high=c.high;last.low=c.low;last.close=c.close}
  else if(istDayKey(nowSec)===istDayKey(lt)){const nt=lt+k*step;c={time:nt,open:ltp,high:ltp,low:ltp,close:ltp};currentCandles.push({time:nt,open:ltp,high:ltp,low:ltp,close:ltp,volume:0})}
  else return;
  paperSelected.ltp=ltp;
  $("lastPrice").textContent=fmt(ltp);
  const ch=Number(t.close_price)>0?ltp-Number(t.close_price):null;
  $("priceChange").textContent=`● LIVE option tick ${fmt(t.age_seconds,1)}s ago`+(ch===null?"":` · ${ch>=0?"+":""}${fmt(ch)} vs prev close`);
  try{candleSeries.update(c)}catch{}
}
// REST fallback: if the WebSocket is not delivering index ticks, poll the quote.
// The label says WHY the REST quote is shown, instead of always claiming the WebSocket is idle:
//  - market closed  -> no ticks exist, so a REST "last close" is the correct data (poll slowly)
//  - market open    -> the feed really is stale: say so, and reconnect the browser socket if it has dropped
let lastRestPoll=0,idleReconnectAt=0;
function restPriceLabel(status){
  if(status==="LIVE_TICK")return"● LIVE tick";
  if(!marketOpen)return"Last close · market closed";
  const open=liveSocket&&liveSocket.readyState===WebSocket.OPEN;
  return open?"REST quote · waiting for live tick":"REST quote · reconnecting live feed";
}
setInterval(async()=>{
  if(document.visibilityState!=="visible"||restPriceBusy)return;
  if(lastIdxTick&&Date.now()-lastIdxTick<5000)return;
  if(!marketOpen&&lastRestPoll&&Date.now()-lastRestPoll<30000)return;   // closed: ticks cannot arrive, poll every 30 s
  if(marketOpen&&(!liveSocket||liveSocket.readyState>WebSocket.OPEN)&&Date.now()-idleReconnectAt>10000){idleReconnectAt=Date.now();connectLiveSocket()}
  restPriceBusy=true;lastRestPoll=Date.now();
  try{const q=await api(`/api/index-ltp?symbol=${getSymbol()}`);
    if(q.ltp!=null&&Number.isFinite(Number(q.ltp))){setLivePrice(Number(q.ltp),q.close_price,"",restPriceLabel(q.status),q.net_change,q.net_change_percent);
      if(q.status!=="LIVE_TICK"&&marketOpen)applyIndexTick({ltp:q.ltp,close_price:q.close_price,age_seconds:0,fresh:true,rest:true})}}
  catch{}finally{restPriceBusy=false}
},2000);
// ---- v1.19.6 real-time push ------------------------------------------------------------------------------------------------
// v1.19.9: the "Updated" line shows the time of the last LIVE update (to 0.1 s), so the 0.1 s refresh is visible.
let liveLabelAt=0,liveLabelPaint=0,liveAnAge=null,liveAnAt=0;
function setUpdated(t){const el=$("lastUpdated");if(el)el.textContent=t}
function liveStamp(){
  if(!marketOpen)return;
  const now=Date.now();liveLabelAt=now;if(now-liveLabelPaint<100)return;liveLabelPaint=now;
  const d=new Date(),ts=`${d.toLocaleTimeString("en-GB",{hour12:false})}.${Math.floor(d.getMilliseconds()/100)}`;
  const an=liveAnAt?` · analysis ${fmt((liveAnAge||0)+(now-liveAnAt)/1000,1)} s old`:"";
  setUpdated(`● Live ${ts} · every ${liveRate()} s${an}`);
}
function liveRate(){const v=Number($("refreshEvery")?.value);return Number.isFinite(v)&&v>0?Math.min(2,Math.max(0.1,v)):0.1}
function liveSubObj(){return{type:"sub",rate:liveRate(),interval:getInterval(),mode:getMode(),score_min:getScoreMin(),reconfirm:getReconfirm(),view:$("chartView").value==="option"?"option":"index"}}
function liveSocketOpen(){try{return !!liveSocket&&liveSocket.readyState===WebSocket.OPEN}catch{return false}}   // TDZ-safe: may run before the socket code below is initialised
function sendLiveSub(){
  if(!liveSocketOpen())return;
  const o=liveSubObj(),k=JSON.stringify(o);if(k===liveSubSent)return;
  try{liveSocket.send(k);liveSubSent=k;wsBarsAt=wsAnalysisAt=0}catch{}
}
function livePushFresh(){
  if(!liveSocketOpen()||!marketOpen)return false;
  const now=Date.now(),idx=$("chartView").value!=="option";
  return now-wsAnalysisAt<8000&&(!idx||now-wsBarsAt<8000);
}
function onLiveAnalysis(m){
  const o=liveSubObj();
  if(m.symbol!==getSymbol()||m.interval!==o.interval||m.mode!==o.mode||m.score_min!==o.score_min||m.reconfirm!==o.reconfirm||!m.data)return;
  wsAnalysisAt=Date.now();
  const a=m.data;
  if(o.view==="option"){
    lastAnalysis=a;lastAnalysisAt=Date.now();
    $("score").textContent=a.status==="ok"?`${a.score}/100`:"—";$("regime").textContent=a.status==="ok"?(a.regime_label||a.regime||"—"):"—";
    $("bullScore").textContent=a.bull_score??"—";$("bearScore").textContent=a.bear_score??"—";renderSignal();
  }else applyAnalysis(a);
  liveAnAge=a.cache_age_s!=null?Number(a.cache_age_s):0;liveAnAt=Date.now();liveStamp();
}
// The server sends the last 3 candles of the chart interval whenever they change: replace / append them in place, so the chart
// (incl. volume, EMA, VWAP) moves without re-downloading 10 days of candles.
function mergeLiveBars(m){
  if(chartSource!=="index"||$("chartView").value!=="index"||!currentCandles.length||!(m.candles||[]).length)return;
  let appended=false,changed=false;
  for(const b of m.candles){
    const t=toChartTime(b.time);if(!Number.isFinite(t)||![b.open,b.high,b.low,b.close].every(v=>Number.isFinite(+v)))continue;
    let j=-1;for(let i=currentCandles.length-1;i>=Math.max(0,currentCandles.length-6);i--){if(toChartTime(currentCandles[i].time)===t){j=i;break}}
    const lastT=toChartTime(currentCandles[currentCandles.length-1].time);
    if(j>=0){const c=currentCandles[j];if(+c.open!==+b.open||+c.high!==+b.high||+c.low!==+b.low||+c.close!==+b.close||+(c.volume||0)!==+(b.volume||0)){currentCandles[j]={...b};changed=true}}
    else if(t>lastT){currentCandles.push({...b});appended=changed=true}
  }
  if(!changed)return;
  const now=Date.now();
  if(appended||now-lastBarsRender>1000){lastBarsRender=now;renderCandles(currentCandles)}
  else{const b=currentCandles[currentCandles.length-1];try{candleSeries.update({time:toChartTime(b.time),open:+b.open,high:+b.high,low:+b.low,close:+b.close})}catch{}}
}
function connectLiveSocket(){
  // One socket at a time. Old handlers are detached first so closing it cannot schedule a second reconnect.
  clearTimeout(liveReconnectTimer);
  const gen=++liveSocketGen;
  if(liveSocket){liveSocket.onopen=liveSocket.onmessage=liveSocket.onerror=liveSocket.onclose=null;try{liveSocket.close()}catch{}}
  const proto=location.protocol==="https:"?"wss:":"ws:";
  const ws=new WebSocket(`${proto}//${location.host}/ws/live?symbol=${encodeURIComponent(getSymbol())}`);
  liveSocket=ws;
  ws.onopen=()=>{if(gen!==liveSocketGen)return;liveSubSent="";wsBarsAt=wsAnalysisAt=0;sendLiveSub();$("streamStatus").textContent="CONNECTED";$("streamStatus").className="pill ok"};
  ws.onmessage=ev=>{
    if(gen!==liveSocketGen)return;
    try{
      const m=JSON.parse(ev.data);
      if(m.type==="index"){applyIndexTick(m.index);liveStamp();return}
      if(m.type==="opt"){(m.ticks||[]).forEach(t=>{t={...t,fresh:!!t.fresh,_rx:Date.now()};if(t.instrument_key)liveTickMap[t.instrument_key]=t;if(paperSelected&&t.instrument_key===paperSelected.instrument_key)applyOptionTick(t)});liveStamp();return}
      if(m.type==="bars"){if(m.interval===getInterval()){wsBarsAt=Date.now();mergeLiveBars(m);liveStamp()}return}
      if(m.type==="analysis"){onLiveAnalysis(m);return}
      const ticks=(m.ticks||[]).map(t=>({...t,fresh:!!t.fresh}));
      renderLiveTicks(ticks);
      applyIndexTick(m.index);
      if(paperSelected)ticks.forEach(t=>{if(t.instrument_key===paperSelected.instrument_key)applyOptionTick(t)});
      ticks.forEach(t=>{if(t.instrument_key){t._rx=Date.now();liveTickMap[t.instrument_key]=t}});
      const nowMs=Date.now();if(nowMs-lastChainPaint>2000&&optionChainRows.length){lastChainPaint=nowMs;renderFilteredChain({scroll:false})}
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
setInterval(()=>{if(document.visibilityState==="visible")refreshDerivatives()},6000);
setInterval(()=>{if(document.visibilityState==="visible"&&marketOpen)refreshChainQuiet()},10000);
setInterval(checkStatus,60000);
document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible"&&(!liveSocket||liveSocket.readyState>1))connectLiveSocket()});

// ---------------- Local-browser paper trading ----------------
// These are simulated positions only. They never place broker orders.
const PAPER_STORAGE_KEY = "option-intelligence-paper-trades-v1";
const PAPER_CAPITAL_KEY = "option-intelligence-paper-capital-v1";
const PAPER_DEFAULT_CAPITAL = 100000;

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
  try{const c=paperSelected;window.OIPrefs&&window.OIPrefs.set("terminal.contract",{symbol:c.symbol,type:c.type,strike:c.strike,instrument_key:c.instrument_key,expiry:c.expiry,lot:c.lot})}catch{}   // remembered so "Selected option premium" survives a reload
  {const lot=paperSelected.lot||1;$("paperQty").value=String(lot);$("paperQty").step=String(lot);$("paperLotHint").textContent=paperSelected.lot?` · 1 lot = ${paperSelected.lot}`:""}
  paperAtr=null;$("paperAtrValue").textContent="UNAVAILABLE";
  api(`/api/pin-option?symbol=${encodeURIComponent(paperSelected.symbol)}&instrument_key=${encodeURIComponent(instrumentKey)}`).catch(()=>{}); // put it on the live WebSocket now
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
function paperCalcAtrFromCandles(candles,intervalMin){
  let cs=(candles||[]).map(c=>({t:Date.parse(c.time),high:Number(c.high),low:Number(c.low),close:Number(c.close)}))
    .filter(c=>Number.isFinite(c.t)&&[c.high,c.low,c.close].every(Number.isFinite)).sort((a,b)=>a.t-b.t);
  const nowSec=Date.now()/1000;
  if(cs.length&&cs[cs.length-1].t/1000+(intervalMin||5)*60>nowSec)cs=cs.slice(0,-1); // drop the forming bar
  // Use today's bars when there are enough: overnight gaps in a premium series inflate ATR and therefore the stop distance.
  const today=cs.filter(c=>istDayKey(c.t/1000)===istDayKey(nowSec)),use=today.length>=15?today:cs;
  if(use.length<15)return null;
  return atrValue(use); // Wilder ATR(14)
}
async function paperCalculateAtr(){
  if(!paperSelected){toast("पहले option chain से CE या PE select करें.");return}
  $("paperCalcAtr").disabled=true;$("paperCalcAtr").textContent="Calculating…";
  try{
    const interval=Number($("paperAtrInterval").value);
    const data=await api(`/api/candles?symbol=${encodeURIComponent(paperSelected.symbol)}&instrument_key=${encodeURIComponent(paperSelected.instrument_key)}&interval=${interval}&days=3`);
    paperAtr=paperCalcAtrFromCandles(data.candles||[],interval);
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
    // Cap the stop at a % of premium (option premiums can move 30% on an ATR); targets keep the same R multiples.
    const g=guardRead(),rawRisk=paperAtr*sm,cap=g.maxSlPct>0?entry*g.maxSlPct/100:Infinity,risk=Math.min(rawRisk,cap),k=risk/sm;
    return {entry,sl:entry-risk,t1:entry+k*m1,t2:entry+k*m2,t3:entry+k*m3,atr:paperAtr,mode:"ATR",clamped:rawRisk>cap,rawRisk};
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
    if(p.clamped)note+=` Stop tightened from ${paperMoney(p.rawRisk)} to the ${guardRead().maxSlPct}%-of-premium cap; targets scaled to keep the same R multiples.`;
    if(rr!==null&&rr<1.5)note+=` ⚠ Target 1 pays only ${rr.toFixed(2)}R: with "Auto-exit at Target 1" you need a win rate above ${(100/(1+rr)).toFixed(0)}% just to break even (before costs and slippage).`;
    if(lot&&qty%lot!==0)note+=` ⚠ Quantity is not a multiple of the lot size (${lot}); real exchange orders need whole lots.`;
    $("paperRiskNote").textContent=note;
  }
  renderGuardStatus();
}
async function paperFetchQuote(force){
  if(!paperSelected)return null;
  try{
    const q=await api(`/api/option-quote?instrument_key=${encodeURIComponent(paperSelected.instrument_key)}`);
    if(q.ltp!==null&&q.ltp!==undefined&&Number(q.ltp)>0){
      paperSelected.ltp=Number(q.ltp);paperSelected.quoteStatus=q.status||"UNAVAILABLE";paperSelected.quoteTimestamp=q.timestamp;
      if(!$("paperEntry").dataset.userEdited||force)$("paperEntry").value=paperSelected.ltp.toFixed(2);
      $("paperSelectedContract").textContent=`Selected: ${paperSelectedLabel()} · LTP ${paperMoney(paperSelected.ltp)} · ${paperSelected.quoteStatus} · ${paperTime(q.timestamp)}`;
      return {price:paperSelected.ltp,status:q.status,timestamp:q.timestamp,bid:Number(q.bid)>0?Number(q.bid):null,ask:Number(q.ask)>0?Number(q.ask):null};
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
  trade.last_price=trade.exit_price;trade.ctx_index_exit=lastIdxLtp;
}
// ---------------- Trade guard: entry rules that actually BLOCK paper entries ----------------
const PAPER_GUARD_KEY="option-intelligence-paper-guard-v1";
const GUARD_FIELDS={guardEnforce:["enforce","checkbox",true],guardMaxTrades:["maxTrades","num",3],guardDailyLoss:["dailyLoss","num",3000],guardMaxLosses:["maxLosses","num",2],guardCooldown:["cooldown","num",15],guardMaxSlPct:["maxSlPct","num",20],guardMaxSpread:["maxSpread","num",3],guardSlip:["slip","num",0.5],guardMinRR:["minRR","num",1.2],guardTimeStop:["timeStop","num",30],guardTrail:["trail","checkbox",true],guardStart:["start","time","09:30"],guardCutoff:["cutoff","time","15:00"],guardExpiryCutoff:["expiryCutoff","time","13:30"]};
function guardTimeMin(v,d){const m=/^(\d{1,2}):(\d{2})/.exec(v||"")||/^(\d{1,2}):(\d{2})/.exec(d);return Number(m[1])*60+Number(m[2])}
function hhmm(m){return String(Math.floor(m/60)).padStart(2,"0")+":"+String(m%60).padStart(2,"0")}
function guardRead(){
  const g={};
  for(const [id,[k,type,def]] of Object.entries(GUARD_FIELDS)){
    const el=$(id);
    if(type==="checkbox")g[k]=el?el.checked:def;
    else if(type==="time")g[k]=guardTimeMin(el&&el.value,def);
    else{const v=el&&el.value!==""?Number(el.value):NaN;g[k]=Number.isFinite(v)&&v>=0?v:def}
  }
  return g;
}
function guardSave(){const s={};for(const [id,[,type]] of Object.entries(GUARD_FIELDS)){const el=$(id);if(el)s[id]=type==="checkbox"?el.checked:el.value}try{localStorage.setItem(PAPER_GUARD_KEY,JSON.stringify(s))}catch{}}
function guardInit(){
  let saved={};try{saved=JSON.parse(localStorage.getItem(PAPER_GUARD_KEY)||"{}")}catch{}
  for(const [id,[,type,def]] of Object.entries(GUARD_FIELDS)){
    const el=$(id);if(!el)continue;const v=saved[id]!==undefined?saved[id]:def;
    if(type==="checkbox")el.checked=!!v;else el.value=v;
    el.addEventListener("change",()=>{guardSave();paperUpdatePreview()});
  }
}
function istNowParts(){const d=new Date(Date.now()+IST_OFFSET_SECONDS*1000);return {min:d.getUTCHours()*60+d.getUTCMinutes(),date:d.toISOString().slice(0,10)}}
function tradeDay(iso){const ms=Date.parse(iso);return Number.isFinite(ms)?istDayKey(ms/1000):null}
function paperGuardCheck(){
  const g=guardRead(),blocks=[],warns=[],sel=paperSelected,st=lastGateState,now=istNowParts();
  if(!sel)return {ok:false,blocks:["Select a CE/PE contract first"],warns,g};
  if(!marketOpen)blocks.push("Market is closed");
  // 1. the confirmed signal, on the SAME side as the contract
  if(!st||!st.side)blocks.push("No directional signal (bull/bear tied or not enough data)");
  else{
    if(st.status!=="CONFIRMED")blocks.push(`Signal is ${st.text}, not CONFIRMED — failing: ${st.failed.join("; ")||"directional score"}`);
    if(st.timing&&st.timing.generated_at_text&&st.timing.state&&st.timing.state!=="FRESH"&&st.timing.state!=="FORMING")blocks.push(`Signal generated ${st.timing.generated_at_text} is ${st.timing.state} (${fmt(st.timing.age_minutes,0)} min old) — entering now is the wrong time`);
    if(sel.type!==st.side)blocks.push(`Selected contract is a ${sel.type} but the signal side is ${st.side}`);
  }
  // 2. liquidity of the contract being bought
  const ov=lastOptionVol;
  if(!ov||ov.none||ov.error)blocks.push("Option volume unavailable — liquidity cannot be verified");
  else if(ov.rel<0.6&&!(st&&st.failed.some(f=>/Option volume/.test(f))))blocks.push(`Option volume dry (${fmt(ov.rel,2)}× < 0.6×)`);
  const tick=liveTickMap[sel.instrument_key];
  if(tick&&tick.fresh&&tick.bid>0&&tick.ask>0){
    const sp=(tick.ask-tick.bid)/((tick.ask+tick.bid)/2)*100;
    if(g.maxSpread>0&&sp>g.maxSpread)blocks.push(`Bid-ask spread ${fmt(sp,1)}% is wider than ${g.maxSpread}%`);
  }else warns.push("Bid/ask not available from the feed — spread not verified");
  // 3. data freshness
  if(Date.now()-lastAnalysisAt>90000)blocks.push("Signal analysis is older than 90 s — refresh data");
  if(!lastIdxTick||Date.now()-lastIdxTick>30000)blocks.push("No live index tick in the last 30 s");
  if(sel.quoteStatus&&sel.quoteStatus!=="LIVE_TICK")warns.push("Option quote is a REST snapshot, not a live tick");
  // 4. time windows (expiry-day options lose premium fast after midday)
  const expiryDay=sel.expiry===now.date,cut=expiryDay?Math.min(g.cutoff,g.expiryCutoff):g.cutoff;
  if(now.min<g.start)blocks.push(`Before ${hhmm(g.start)} IST — opening volatility`);
  if(now.min>=cut)blocks.push(`After ${hhmm(cut)} IST${expiryDay?" on expiry day (theta decay)":""} — no new entries`);
  // 5. account discipline
  const trades=paperRead(),today=istDayKey(Date.now()/1000);
  if(trades.some(t=>t.status==="OPEN"))blocks.push("A paper trade is already open (one at a time)");
  if(g.maxTrades>0&&trades.filter(t=>tradeDay(t.opened_at)===today).length>=g.maxTrades)blocks.push(`Max ${g.maxTrades} trades per day reached`);
  const closedToday=trades.filter(t=>t.status==="CLOSED"&&tradeDay(t.exit_time)===today).sort((a,b)=>Date.parse(b.exit_time)-Date.parse(a.exit_time));
  const pnlToday=closedToday.reduce((s,t)=>s+(Number(t.realized_pnl)||0),0);
  if(g.dailyLoss>0&&pnlToday<=-g.dailyLoss)blocks.push(`Daily loss cap reached (${paperMoney(pnlToday)})`);
  let streak=0;for(const t of closedToday){if(Number(t.realized_pnl)<0)streak++;else break}
  if(g.maxLosses>0&&streak>=g.maxLosses)blocks.push(`${streak} consecutive losses — stop for the day`);
  if(closedToday[0]&&g.cooldown>0){const mins=(Date.now()-Date.parse(closedToday[0].exit_time))/60000;if(mins<g.cooldown)blocks.push(`Cooldown: ${Math.ceil(g.cooldown-mins)} min left after the last exit`)}
  // 6. reward:risk of the chosen exit
  const p=paperRiskPlan();
  if(p){const tg=({t1:p.t1,t2:p.t2,t3:p.t3})[$("paperExitTarget").value]||p.t1,risk=p.entry-p.sl,rr=risk>0?(tg-p.entry)/risk:0;if(rr<g.minRR)blocks.push(`Reward:risk ${rr.toFixed(2)}R is below the ${g.minRR}R minimum`)}
  else blocks.push("Risk plan incomplete (calculate ATR / enter SL and target)");
  return {ok:!blocks.length,blocks,warns,g};
}
function renderGuardStatus(){
  const ul=$("guardStatus");if(!ul)return;
  const c=paperGuardCheck();ul.innerHTML="";
  const add=(cls,mark,text)=>{const li=document.createElement("li");li.className=cls;const b=document.createElement("b");b.textContent=mark;const s=document.createElement("span");s.textContent=" "+text;li.appendChild(b);li.appendChild(s);ul.appendChild(li)};
  if(c.ok)add("gate-pass","✓","All entry rules satisfied");
  c.blocks.forEach(b=>add("gate-fail","✗",b));
  c.warns.forEach(w=>add("gate-warn","!",w));
  const btn=$("paperBuyBtn");if(btn){const blocked=!c.ok&&c.g.enforce;btn.textContent=blocked?"ENTRY BLOCKED — see Trade guard":"BUY SELECTED OPTION (PAPER)";btn.classList.toggle("blocked",blocked)}
}
async function paperOpenTrade(){
  if(!paperSelected){toast("Option chain से CE/PE select करें.");return}
  const btn=$("paperBuyBtn");btn.disabled=true;
  try{
    // The price in the form can be minutes old. Re-quote at click time and fill like a real order: at the ask, or last price + slippage.
    const q=await paperFetchQuote(true);
    if(!q){toast("Fresh option quote unavailable — trade not opened.");return}
    const g0=guardRead(),fill=q.ask>0?q.ask:q.price*(1+g0.slip/100);
    $("paperEntry").value=(Math.round(fill/0.05)*0.05).toFixed(2);
    paperUpdatePreview();
    const check=paperGuardCheck();let override=false;
    if(!check.ok){
      if(check.g.enforce){toast("Entry blocked: "+check.blocks[0]+(check.blocks.length>1?` (+${check.blocks.length-1} more)`:""));renderGuardStatus();return}
      if(!confirm("Trade guard is OFF, but these rules fail:\n\n• "+check.blocks.join("\n• ")+"\n\nOpen the paper trade anyway?"))return;
      override=true;
    }
    const p=paperRiskPlan(),qty=paperNum("paperQty");
    if(!p||!qty||!Number.isInteger(qty)||qty<1){toast("Entry/SL/target/quantity ठीक करें; ATR mode में पहले ATR calculate करें.");return}
    if(p.sl>=p.entry||p.t1<=p.entry||(p.t2&&p.t2<=p.entry)||(p.t3&&p.t3<=p.entry)){toast("BUY trade में SL entry से नीचे और targets entry से ऊपर होने चाहिए.");return}
    const tick=liveTickMap[paperSelected.instrument_key],spread=tick&&tick.fresh&&tick.bid>0&&tick.ask>0?(tick.ask-tick.bid)/((tick.ask+tick.bid)/2)*100:null;
    const st=lastGateState,ov=lastOptionVol&&!lastOptionVol.none&&!lastOptionVol.error?lastOptionVol:null,a=lastAnalysis;
    const trade={
      id:(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random()),symbol:paperSelected.symbol,type:paperSelected.type,
      strike:paperSelected.strike,expiry:paperSelected.expiry,instrument_key:paperSelected.instrument_key,
      qty,entry:p.entry,sl:p.sl,sl_initial:p.sl,t1:p.t1,t2:p.t2,t3:p.t3,atr:p.atr,risk_mode:p.mode,slip:g0.slip,override,mfe:0,mae:0,
      exit_target:$("paperExitTarget").value,status:"OPEN",opened_at:new Date().toISOString(),
      last_price:paperSelected.ltp||p.entry,last_quote_status:paperSelected.quoteStatus||"UNAVAILABLE",last_quote_time:paperSelected.quoteTimestamp||null,
      exit_price:null,exit_time:null,exit_reason:null,realized_pnl:null,
      // what the dashboard said at entry, so every loss can be traced to the rule it should have respected
      ctx_signal:st?st.text:"—",ctx_score:st?st.dom:null,ctx_failed:st?st.failed.join("; "):"",ctx_opt_vol:ov?Number(ov.rel.toFixed(2)):null,
      ctx_spread_pct:spread==null?null:Number(spread.toFixed(2)),ctx_index_entry:lastIdxLtp,ctx_index_exit:null,ctx_regime:a?(a.regime_label||a.regime):null,ctx_mode:a?a.mode||null:null,ctx_signal_at:a&&a.timing?a.timing.generated_at_text||null:null,ctx_signal_age_min:a&&a.timing?a.timing.age_minutes??null:null,ctx_dash:a&&a.dashboard&&a.dashboard.available?`${a.dashboard.side||"-"} ${a.dashboard.level}`:"unavailable",ctx_htf:a&&a.htf?a.htf.state:null
    };
    const trades=paperRead(),capital=paperGetCapital();
    const realized=trades.filter(t=>t.status==="CLOSED").reduce((sum,t)=>sum+(Number(t.realized_pnl)||0),0);
    const committed=trades.filter(t=>t.status==="OPEN").reduce((sum,t)=>sum+(Number(t.entry)||0)*(Number(t.qty)||0),0);
    const available=capital+realized-committed,required=p.entry*qty;
    if(required>available){toast(`Insufficient paper cash. Required ${paperMoney(required)}, available ${paperMoney(Math.max(0,available))}.`);return}
    trades.unshift(trade);paperSave(trades);paperRender();
    toast(`Paper BUY opened: ${trade.symbol} ${trade.strike} ${trade.type} @ ${paperMoney(trade.entry)}${override?" (guard OVERRIDDEN)":""}`);
  }finally{btn.disabled=false;paperUpdatePreview()}
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
  if(!closed.length)hist.innerHTML='<tr><td colspan="8" class="empty">No closed trades yet.</td></tr>';
  closed.slice(0,100).forEach(t=>{
    const tr=document.createElement("tr"),values=[`${paperTime(t.opened_at)} / ${paperTime(t.exit_time)}`,`${t.symbol} ${t.strike} ${t.type} · ${t.expiry}`,t.qty,paperMoney(t.entry),paperMoney(t.exit_price),t.exit_reason||"Manual",paperMoney(t.realized_pnl),t.ctx_signal?`${t.ctx_signal} · score ${t.ctx_score??"—"} · opt vol ${t.ctx_opt_vol??"—"}×${t.override?" · OVERRIDE":""} · idx ${fmt(t.ctx_index_entry,0)}→${fmt(t.ctx_index_exit,0)}`:"—"];
    values.forEach((v,i)=>{const td=document.createElement("td");td.textContent=v;if(i===6)td.className=Number(t.realized_pnl)>=0?"pnl-positive":"pnl-negative";if(i===7){td.className="paper-muted";td.style.textAlign="left";td.style.whiteSpace="normal";td.style.minWidth="220px"}tr.appendChild(td)});hist.appendChild(tr);
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
    const trades=paperRead(),open=trades.filter(t=>t.status==="OPEN"),g=guardRead(),now=istNowParts();
    for(const t of open){
      try{
        const q=await api(`/api/option-quote?instrument_key=${encodeURIComponent(t.instrument_key)}`);
        if(!(Number(q.ltp)>0)||q.status==="UNAVAILABLE") {t.last_quote_status="UNAVAILABLE";continue}
        const price=Number(q.ltp);t.last_price=price;t.last_quote_status=q.status;t.last_quote_time=q.timestamp;
        // excursion tracking: shows whether stops are too tight / targets too far
        t.mfe=Math.max(t.mfe||0,price-t.entry);t.mae=Math.min(t.mae||0,price-t.entry);
        // market-type exits (stop / time / square-off) are filled slightly worse than the observed quote
        const slipExit=p=>Math.max(0.05,Number((p*(1-(t.slip??0.5)/100)).toFixed(2)));
        const target=t.exit_target==="t1"?t.t1:t.exit_target==="t2"?t.t2:t.exit_target==="t3"?t.t3:null;
        const slInit=t.sl_initial??t.sl,risk=t.entry-slInit;
        if(price<=t.sl)paperCloseTrade(t,slipExit(price),t.sl>slInit?"Trailing stop hit (observed quote)":"Stop-loss hit (filled at observed quote)");
        else if(target&&price>=target)paperCloseTrade(t,target,`Target ${t.exit_target.toUpperCase()} touched (observed quote)`);
        else{
          // trailing: breakeven after +1R; lock half of the T1 gain once T1 is touched (matters when exiting at T2/T3)
          if(g.trail&&risk>0){
            if(price>=t.t1)t.sl=Math.max(t.sl,t.entry+(t.t1-t.entry)*0.5);
            else if(price>=t.entry+risk)t.sl=Math.max(t.sl,t.entry);
          }
          const elapsedMin=(Date.now()-Date.parse(t.opened_at))/60000,progress=risk>0?(price-t.entry)/risk:0;
          if(g.timeStop>0&&elapsedMin>=g.timeStop&&progress<0.3)paperCloseTrade(t,slipExit(price),`Time stop (${g.timeStop} min, no progress)`);
          else if(now.min>=15*60+20&&tradeDay(t.opened_at)===istDayKey(Date.now()/1000))paperCloseTrade(t,slipExit(price),"End-of-day square-off (15:20)");
        }
      }catch{t.last_quote_status="UNAVAILABLE"}
    }
    paperSave(trades);paperRender();
  }finally{paperRefreshBusy=false}
}
function paperExportCsv(){
  const trades=paperRead();if(!trades.length){toast("No paper trades to export.");return}
  const cols=["symbol","type","strike","expiry","qty","entry","sl_initial","sl","t1","t2","t3","risk_mode","slip","opened_at","status","last_price","exit_price","exit_time","exit_reason","realized_pnl","mfe","mae","override","ctx_signal","ctx_score","ctx_failed","ctx_opt_vol","ctx_spread_pct","ctx_index_entry","ctx_index_exit","ctx_regime","ctx_mode","ctx_signal_at","ctx_signal_age_min","ctx_dash","ctx_htf"];
  const esc=v=>`"${String(v??"").replace(/"/g,'""')}"`;
  const csv=[cols.join(","),...trades.map(t=>cols.map(k=>esc(t[k])).join(","))].join("\r\n");
  if(window.oiSave){window.oiSave("option-intelligence-paper-trades.csv",csv,"text/csv");return}const blob=new Blob([csv],{type:"text/csv;charset=utf-8;"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="option-intelligence-paper-trades.csv";a.click();URL.revokeObjectURL(url);
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
$("paperClearBtn").addEventListener("click",()=>{
   const trades=paperRead(),closed=trades.filter(t=>t.status==="CLOSED");
   if(!closed.length){toast("No closed paper-trade history to clear.");return}
   if(!confirm("Clear all CLOSED paper-trade history from your account on every synced device? Open trades will be kept."))return;
   let deleted=[];try{deleted=JSON.parse(localStorage.getItem("option-intelligence-paper-deleted-v1")||"[]")}catch{}
   const ids=new Set(Array.isArray(deleted)?deleted.map(String):[]);
   closed.forEach(t=>ids.add(String(t.id)));
   // Save tombstones first; account sync uses these to prevent old server copies reappearing.
   localStorage.setItem("option-intelligence-paper-deleted-v1",JSON.stringify([...ids]));
   paperSave(trades.filter(t=>t.status==="OPEN"));paperRender();toast("Closed paper-trade history cleared and syncing across devices.");
 });
$("paperInitialCapital").value=String(paperGetCapital());
guardInit();paperUpdateMode();paperRender();setInterval(()=>{if(document.visibilityState==="visible")paperRefreshQuotes()},4000);setInterval(()=>{if(document.visibilityState==="visible")renderGuardStatus()},3000);
// Restore the selected option contract saved to the account: at startup, and whenever another device changes it, so the
// "Selected option premium" chart view shows the same contract everywhere.
function restoreSavedContract(){try{
  const c=window.OIPrefs&&window.OIPrefs.get("terminal.contract",null);
  if(!c||!c.instrument_key||c.symbol!==getSymbol())return;
  if(/^\d{4}-\d{2}-\d{2}$/.test(String(c.expiry))&&c.expiry<new Date().toISOString().slice(0,10))return;   // expired contract: do not revive
  if(paperSelected&&paperSelected.instrument_key===c.instrument_key)return;                               // already selected
  selectPaperContract(c.type,c.strike,c.instrument_key,null,c.expiry,null);
  if(c.lot&&paperSelected){paperSelected.lot=c.lot;$("paperQty").value=String(c.lot);$("paperQty").step=String(c.lot);$("paperLotHint").textContent=` · 1 lot = ${c.lot}`}
}catch(e){console.warn("contract restore failed",e)}}
restoreSavedContract();
window.addEventListener("oi-sync-pulled",ev=>{if((ev.detail?.changed||[]).includes("oi-settings-v1"))restoreSavedContract()});

// v1.18.12: push alerts switch themselves on and stay on. The button used to read "Enable push alerts" after every reopen because
// (a) the check gave up after 4 s while the phone was still waking its service worker, and (b) a missing registration was only
// repaired when that check passed. Now: the last known state is shown at once, a granted permission is always re-registered with the
// server in the background (on open, on returning to the app, every 10 min), and the first tap anywhere asks for permission once.
(async function initPushControls(){
  const btn=$("enablePush"); if(!btn)return;
  const b64=(s)=>Uint8Array.from(atob(s.replace(/-/g,"+").replace(/_/g,"/")),c=>c.charCodeAt(0));
  const LS_ON="oi-push-on", LS_ASKED="oi-push-asked";
  const lsGet=k=>{try{return localStorage.getItem(k)}catch{return null}}, lsSet=(k,v)=>{try{localStorage.setItem(k,v)}catch{}};
  const webSupported=()=>"serviceWorker" in navigator&&"PushManager" in window&&"Notification" in window;
  const nativePlugin=()=>window.Capacitor?.isNativePlatform?.()&&window.Capacitor?.Plugins?.PushNotifications;
  const setLabel=on=>{btn.dataset.on=on?"1":"";btn.textContent=on?"Push alerts: ON":"Enable push alerts"};
  const swReady=ms=>Promise.race([navigator.serviceWorker.ready,new Promise(r=>setTimeout(()=>r(null),ms))]);
  async function localPushState(){
    try{
      if(nativePlugin())return lsGet(LS_ON)==="1";
      if(!webSupported()||Notification.permission!=="granted")return false;
      const reg=await swReady(10000);
      if(!reg)return lsGet(LS_ON)==="1";            // slow service worker is not the same as "switched off"
      return !!(await reg.pushManager.getSubscription());
    }catch{return lsGet(LS_ON)==="1"}
  }
  function showPushStatus(s){
    const el=$("pushStatus");if(!el)return;
    if(!s){el.textContent="";return}
    const m=s.monitor||{},ago=x=>x==null?"—":x<90?x+"s ago":Math.round(x/60)+" min ago";
    const sy=Object.entries(m.symbols||{}).map(([k,v])=>`${k} ${v.action||"—"}${v.score!=null?" "+v.score:""}`).join(" · ");
    const dev=s.devices||{};
    el.innerHTML="";
    const line=document.createElement("div");
    line.textContent=`Server monitor: ${m.running?"running":s.market_open?"NOT running":"idle (market closed)"} · last check ${ago(m.last_check_ago_s)} · phones registered: ${(dev.web||0)+(dev.native||0)}`+(sy?` · ${sy}`:"");
    el.appendChild(line);
    (s.problems||[]).forEach(p=>{const d=document.createElement("div");d.style.color="var(--yellow)";d.textContent="⚠ "+p;el.appendChild(d)});
    if(s.last_alert){const d=document.createElement("div");d.textContent=`Last alert: ${s.last_alert.event} ${s.last_alert.symbol} ${s.last_alert.side||""} · ${new Date(s.last_alert.at*1000).toLocaleTimeString()}`;el.appendChild(d)}
  }
  async function loadPushStatus(){try{const s=await api("/api/push/status");showPushStatus(s);return s}catch{return null}}
  window.oiLoadPushStatus=loadPushStatus;

  // Native Capacitor Android/iOS push (FCM). ask=false never shows a permission prompt.
  async function registerNative(ask){
    const native=nativePlugin(); if(!native)return false;
    let perm=null; try{perm=await native.checkPermissions()}catch{}
    if(!perm||perm.receive!=="granted"){
      if(!ask)return false;
      perm=await native.requestPermissions();
      if(perm.receive!=="granted")throw new Error("Push permission not granted");
    }
        // Wait for the FCM token and for the server to confirm it was saved.
        // Do not report success merely because the native register() call started.
        await new Promise((resolve,reject)=>{
          let settled=false, registrationHandle=null, errorHandle=null;
          const finish=(fn,value)=>{
            if(settled)return; settled=true; clearTimeout(timer);
            try{registrationHandle?.remove()}catch{}
            try{errorHandle?.remove()}catch{}
            fn(value);
          };
          const timer=setTimeout(()=>finish(reject,new Error("Timed out waiting for Firebase device token. Check Google Services configuration and internet access.")),25000);
          (async()=>{
            try {
              // Capacitor versions/plugins may return either a Promise or a
              // listener handle directly. await safely supports both forms.
              registrationHandle = await native.addListener("registration", async t => {
                try {
                  if(!t?.value) throw new Error("Firebase returned an empty device token");
                  await api("/api/push/native/register", {
                    method:"POST",
                    headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({token:t.value})
                  });
                  finish(resolve,true);
                } catch(e) { finish(reject,e); }
              });
              errorHandle = await native.addListener("registrationError", e => {
                finish(reject,new Error(e?.error||"Firebase device registration failed"));
              });
              await native.register();
            } catch(e) {
              finish(reject,e);
            }
          })();
        });
    return true;
  }
  // Browser/PWA Web Push. Idempotent: replaces a subscription made with an older/different server key and re-saves it on the server.
  async function registerWeb(ask){
    if(!webSupported())return false;
    const cfg=await api("/api/push/config"); if(!(cfg.web_push&&cfg.vapid_public_key))return false;
    if(Notification.permission==="default"){
      if(!ask)return false;
      lsSet(LS_ASKED,String((Number(lsGet(LS_ASKED))||0)+1));
      const p=await Notification.requestPermission(); if(p!=="granted")throw new Error("Browser notification permission not granted");
    }
    if(Notification.permission!=="granted"){
      if(ask)throw new Error("Notifications are blocked for this app. Open the browser's site settings for this address, set Notifications to Allow, then reopen the app.");
      return false;
    }
    const reg=await swReady(15000); if(!reg)return false;
    let sub=await reg.pushManager.getSubscription();
    if(sub){
      const cur=sub.options&&sub.options.applicationServerKey?btoa(String.fromCharCode(...new Uint8Array(sub.options.applicationServerKey))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,""):"";
      if(cur&&cur!==cfg.vapid_public_key){await sub.unsubscribe();sub=null}
    }
    if(!sub)sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:b64(cfg.vapid_public_key)});
    await api("/api/push/web/subscribe",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({subscription:sub.toJSON()})});
    return true;
  }
  let busy=false,lastEnsure=0;
  async function ensure(ask){
    if(busy)return [];
    busy=true; lastEnsure=Date.now();
    const enabled=[];
    try{
      try{if(await registerNative(ask))enabled.push("native push")}catch(e){if(ask)throw e;console.warn("native push",e)}
      try{if(await registerWeb(ask))enabled.push("Web Push")}catch(e){if(ask)throw e;console.warn("web push",e)}
      if(enabled.length){lsSet(LS_ON,"1");setLabel(true);loadPushStatus()}
      return enabled;
    }finally{busy=false}
  }

  setLabel(lsGet(LS_ON)==="1"&&(nativePlugin()||(webSupported()&&Notification.permission==="granted")));   // instant, no network
  loadPushStatus();
  ensure(false).then(async got=>{
    if(!got.length&&nativePlugin()){try{got=await ensure(true)}catch(e){toast("Push setup: "+e.message)}}      // Android app: ask at launch, no tap needed
    if(!got.length)setLabel(await localPushState());
    banner();
  });                                                                                                         // silent repair on every open
  // Browsers only allow the permission prompt after a tap. So: the first tap on every page load asks (up to 3 times in total - Chrome
  // blocks a site after 3 dismissals anyway), and a bar at the top stays visible with a one-tap "Turn on" until push is on.
  function banner(){
    let el=document.getElementById("pushBar");
    const on=btn.dataset.on==="1";
    const perm=webSupported()?Notification.permission:"unsupported";
    const ios=/iphone|ipad|ipod/i.test(navigator.userAgent);
    let msg="",act="";
    if(!on&&!nativePlugin()){
      if(perm==="denied")msg="Push alerts are blocked. Open your browser's site settings for this app, set Notifications to Allow, then reopen it.";
      else if(perm==="unsupported"&&ios)msg="To get push alerts on iPhone: tap Share, Add to Home Screen, then open the app from the home screen.";
      else if(perm==="default"||perm==="granted"){msg="Push alerts are off on this phone.";act="Turn on"}
    }
    if(!msg){if(el)el.remove();return}
    if(!el){el=document.createElement("div");el.id="pushBar";el.style.cssText="position:sticky;top:0;z-index:50;display:flex;gap:10px;align-items:center;justify-content:space-between;padding:8px 12px;background:#7a4b00;color:#fff;font-size:14px";document.body.prepend(el)}
    el.innerHTML="";const t=document.createElement("span");t.textContent=msg;el.appendChild(t);
    if(act){const b=document.createElement("button");b.type="button";b.textContent=act;b.style.cssText="flex:none;padding:6px 12px;border-radius:8px;border:0;font-weight:600";b.onclick=()=>btn.click();el.appendChild(b)}
  }
  new MutationObserver(banner).observe(btn,{attributes:true,attributeFilter:["data-on"]});
  if((nativePlugin()||(webSupported()&&Notification.permission==="default"))&&(Number(lsGet(LS_ASKED))||0)<3){
    document.addEventListener("pointerup",()=>{ensure(true).catch(e=>toast("Push setup: "+e.message)).finally(banner)},{once:true});
  }
  document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible"&&Date.now()-lastEnsure>60000)ensure(false)});
  setInterval(()=>{if(document.visibilityState==="visible")loadPushStatus()},30000);
  setInterval(()=>{if(document.visibilityState==="visible")ensure(false)},600000);
  btn.addEventListener("click",async()=>{
    btn.disabled=true; btn.textContent="Enabling…";
    try{
      const enabled=await ensure(true);
      if(!enabled.length)throw new Error("Push is not available in this browser. On iPhone, add the app to the Home Screen first (Share > Add to Home Screen) and open it from there; on Android use Chrome.");
      toast("Enabled: "+enabled.join(" + ")+". Alerts now arrive even when the app is closed.");
    }catch(e){toast("Push setup: "+e.message)}
    finally{btn.disabled=false;setLabel(lsGet(LS_ON)==="1"&&btn.dataset.on==="1")}
  });
})();

// v1.18.13: choose which signals send a push - every signal change, or only confirmed buy signals (saved on the account, used by the server).
(function(){
  const sel=$("pushType"); if(!sel)return;
  const apply=m=>{if(m&&sel.value!==m)sel.value=m};
  fetch("/api/push/prefs",{credentials:"same-origin"}).then(r=>r.ok?r.json():null).then(d=>d&&apply(d.mode)).catch(()=>{});
  sel.addEventListener("change",async()=>{
    const want=sel.value;
    try{
      const r=await fetch("/api/push/prefs",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({mode:want})});
      if(!r.ok)throw new Error("HTTP "+r.status);
      toast(want==="confirmed"?"Push only for CONFIRMED buy signals.":"Push for every signal change.");
      window.dispatchEvent(new CustomEvent("oi-notify-pref",{detail:want}));
    }catch(e){toast("Could not save notification type: "+e.message);fetch("/api/push/prefs",{credentials:"same-origin"}).then(r=>r.json()).then(d=>apply(d.mode)).catch(()=>{})}
  });
  window.addEventListener("oi-notify-pref",e=>apply(e.detail));
})();

// v1.20.0 strategy monitor pill: today's findings at a glance, links to the Signals page panel
async function loadMonitorPill(){
  const el=$("monPill");if(!el)return;
  try{const m=await api("/api/monitor/status");
    if(!m.enabled){el.textContent="Monitor off";el.className="pill muted";return}
    const n=(m.high||0)+(m.medium||0);
    el.textContent=n?`Monitor: ${n} issue${n>1?"s":""}${m.ai?" · "+m.ai.split(" ")[0]:""}`:`Monitor: OK${m.ai?" · "+m.ai.split(" ")[0]:""}`;
    el.className="pill "+(m.high?"bad":m.medium?"stale":"ok");el.title=m.latest||"No issues found today";
  }catch{el.textContent="Monitor";el.className="pill muted"}
}
loadMonitorPill();setInterval(()=>{if(document.visibilityState==="visible")loadMonitorPill()},30000);

// v1.20.14: "Download for Claude" on the terminal - the server adds settings, live analyses, the signal log / board and the replay;
// the page adds what only it knows: chart view, selected option, trendlines and the S/R map drawn on the chart.
function pageSnapshot(){
  const lv=l=>l?{price:l.price,strength:l.strength,basis:l.basis}:null, tl=t=>t?{direction:t.direction,value:Math.round(t.value*100)/100,touches:t.touches,broken:!!t.broken,bars:t.bars}:null;
  const sr=lastSR||{};
  return {symbol:getSymbol(),interval:getInterval(),mode:getMode(),score_min:getScoreMin(),reconfirm:getReconfirm(),chart_view:$("chartView").value,
    refresh:$("refreshEvery")?.value,live_price:$("lastPrice")?.textContent,price_note:$("priceChange")?.textContent,stream:$("streamStatus")?.textContent,
    updated:$("lastUpdated")?.textContent,monitor:$("monPill")?.textContent,decision:$("bigDecision")?.textContent,decision_note:$("decisionSub")?.textContent,
    selected_option:paperSelected?{name:paperSelectedLabel?paperSelectedLabel():null,instrument_key:paperSelected.instrument_key,ltp:paperSelected.ltp,type:paperSelected.type,strike:paperSelected.strike}:null,
    trendlines:lastTrendlines?{support:tl(lastTrendlines.support),resistance:tl(lastTrendlines.resistance)}:null,
    sr:{supports:(sr.supports||[]).slice(0,3).map(lv),resistances:(sr.resistances||[]).slice(0,3).map(lv)},
    chart_indicators:{ema9:$("ema9")?.textContent,ema21:$("ema21")?.textContent,vwap:$("vwap")?.textContent,rsi:$("rsi")?.textContent,atr:$("atr")?.textContent,rel_vol:$("relVol")?.textContent},
    page_version:FRONTEND_VERSION};
}
(function(){const b=$("claudeExport");if(!b)return;b.addEventListener("click",async()=>{
  b.disabled=true;const old=b.textContent;b.textContent="Preparing…";
  try{const day=new Date(Date.now()+330*60000).toISOString().slice(0,10);
    const r=await fetch(`/api/monitor/export?day=${day}`,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(pageSnapshot())});
    if(!r.ok)throw Error((await r.text()).slice(0,160));
    const text=await r.text(),name=`strategy-review-${day}.json`;
    if(window.oiSave)await window.oiSave(name,text,"application/json");else toast("Saving files is not available on this page");
    toast("Review file ready - upload it in a Claude chat and write: review this day");
  }catch(e){toast("Download for Claude failed: "+e.message)}finally{b.disabled=false;b.textContent=old}
});})();

function escapeHtml(t){return String(t??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}

// v1.20.18: Pattern window (1-2 hours) - a server setting shared by live signals, alerts and the monitor (owner can change it)
(function(){const sel=$("patternMin");if(!sel)return;
  fetch("/api/signals/config",{credentials:"same-origin"}).then(r=>r.ok?r.json():null).then(c=>{if(c&&c.pattern_minutes)sel.value=String(c.pattern_minutes)}).catch(()=>{});
  sel.addEventListener("change",async()=>{
    try{const r=await fetch("/api/pattern-window",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({minutes:Number(sel.value)})});
      if(!r.ok)throw Error(r.status===403?"only the owner account can change it":(await r.text()).slice(0,120));
      const d=await r.json();toast(`Chart patterns now read the last ${d.pattern_minutes} minutes - signals recompute now`);lastAnalysisAt=0;runAnalysis(true);
    }catch(e){toast("Pattern window not changed: "+e.message)}
  });})();


// v1.20.27: AI view on the terminal - "Ask AI now" plus automatic comments when a BUY fires or a setup is blocked by only one rule.
// The server dedupes moments across tabs/phones and caps calls per day; the AI never changes the signal.
const aiLive={busy:false,lastKey:"",combo:""};
function aiLiveCombo(){return `${getSymbol()}|${getInterval()}|${getMode()}`}
function aiLiveRender(d,note){
  const box=$("aiLiveText"),meta=$("aiLiveMeta"),list=$("aiLiveList"),older=$("aiLiveOlder");if(!box)return;
  const cs=(d&&d.comments)||[],c=cs[0];
  const why={manual:"you asked",buy:"BUY fired",near:"blocked by one rule"};
  if(c){box.textContent=c.text;meta.textContent=`${c.at} IST · ${why[c.reason]||c.reason} · signal ${c.signal||"—"} · price ${fmt(c.price)} · ${c.ai||"AI"}${c.model?" ("+c.model+")":""}`+(note?` · ${note}`:"")}
  else if(note){meta.textContent=note}
  if(d&&d.auto_left!=null)meta.textContent+=` · left today: ${d.manual_left} questions, ${d.auto_left} auto`;
  if(list&&older){const rest=cs.slice(1);older.hidden=!rest.length;
    list.innerHTML=rest.map(x=>`<div class="notice" style="margin-top:6px;white-space:pre-line;overflow-wrap:anywhere"><b>${escapeHtml(x.at)}</b> · ${escapeHtml(why[x.reason]||x.reason)} · ${escapeHtml(x.signal||"")}<br>${escapeHtml(x.text)}</div>`).join("")}
}
async function aiLiveLoad(){
  if(!$("aiLiveText"))return;aiLive.combo=aiLiveCombo();
  try{const r=await fetch(`/api/ai/live?symbol=${getSymbol()}&interval=${getInterval()}&mode=${getMode()}`,{credentials:"same-origin"});if(!r.ok)return;
    const d=await r.json();if(!d.enabled){$("aiLiveMeta").textContent="No AI connected - add GEMINI_API_KEY in Render, then press Test AI on the Signals page.";return}
    if(d.comments&&d.comments.length)aiLiveRender(d);else{$("aiLiveText").textContent='Press "Ask AI now" for a read of this chart.';$("aiLiveMeta").textContent=`${d.ai} connected`;if($("aiLiveOlder"))$("aiLiveOlder").hidden=true}
  }catch{}
}
async function aiLiveAsk(reason,event){
  if(aiLive.busy)return;aiLive.busy=true;const b=$("aiLiveAsk");
  if(reason==="manual"&&b){b.disabled=true;b.textContent="Asking…"}
  if(reason!=="manual"&&$("aiLiveMeta"))$("aiLiveMeta").textContent=(reason==="buy"?"BUY fired":"Setup blocked by one rule")+" - asking the AI…";
  try{let page=null;try{page=pageSnapshot()}catch{}
    const r=await fetch("/api/ai/live",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({symbol:getSymbol(),interval:Number(getInterval()),mode:getMode(),score_min:Number(getScoreMin()),reconfirm:Number(getReconfirm()),reason,event,page})});
    if(!r.ok)throw Error(r.status===401?"log in first":(await r.text()).slice(0,140));
    const d=await r.json();aiLiveRender(d,d.ok?"":d.skipped);
    if(reason!=="manual"&&d.ok&&d.comment)toast("AI: "+d.comment.text.slice(0,140));
  }catch(e){if($("aiLiveMeta"))$("aiLiveMeta").textContent="AI view: "+e.message}
  finally{aiLive.busy=false;if(b){b.disabled=false;b.textContent="Ask AI now"}}
}
function aiLiveWatch(a){
  if(!$("aiLiveText"))return;
  if(aiLive.combo!==aiLiveCombo()){aiLive.lastKey="";aiLiveLoad()}
  const auto=$("aiLiveAuto");if(!auto||!auto.checked||!marketOpen||!a||a.status!=="ok")return;
  const fin=a.final||{},bar=a.last_closed_bar||((a.timing||{}).generated_at)||"",side=fin.side||a.side;
  let reason=null,event=null;
  if(fin.level==="CONFIRMED"&&side){reason="buy";event=`${side}|${bar}`}
  else if(side){const failed=(a.gates||[]).filter(g=>!g.pass);
    if(failed.length===1&&!/Entry time window|One trade per side|Daily loss/i.test(failed[0].label||"")){reason="near";event=`${side}|${failed[0].label}|${bar}`}}
  if(!reason)return;const key=reason+"|"+event;if(key===aiLive.lastKey)return;aiLive.lastKey=key;aiLiveAsk(reason,event);
}
(function(){const b=$("aiLiveAsk");if(!b)return;b.addEventListener("click",()=>aiLiveAsk("manual",null));
  const au=$("aiLiveAuto");try{const v=localStorage.getItem("aiLiveAuto");if(v!=null)au.checked=v==="1"}catch{}
  au.addEventListener("change",()=>{try{localStorage.setItem("aiLiveAuto",au.checked?"1":"0")}catch{}});
  aiLiveLoad();setInterval(()=>{if(document.visibilityState==="visible"&&!aiLive.busy)aiLiveLoad()},60000);})();
