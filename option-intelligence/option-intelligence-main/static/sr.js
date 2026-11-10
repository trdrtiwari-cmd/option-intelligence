/* Automatic support / resistance zones (v1.20.47) - the Terminal's computeSR, shared with the chart window.
   Zones from 7-bar swing pivots over the last ~5 sessions plus previous-day high / low / close and today's high / low, merged
   within ~0.6 ATR and scored by touches, key-level status, recency and round numbers (index only). Nearest 3 per side.
   cs = [{time (unix s), open, high, low, close}] ascending. */
(function (root) {
  "use strict";
  const SR_PER_SIDE = 3, IST_OFFSET_SECONDS = 19800;
  const istDayKey = t => Math.floor((t + IST_OFFSET_SECONDS) / 86400);
  function atrValue(cs, n = 14) {
    if (cs.length < n + 1) return null;
    const tr = []; for (let i = 1; i < cs.length; i++) tr.push(Math.max(cs[i].high - cs[i].low, Math.abs(cs[i].high - cs[i - 1].close), Math.abs(cs[i].low - cs[i - 1].close)));
    let atr = tr.slice(0, n).reduce((a, b) => a + b, 0) / n;
    for (let i = n; i < tr.length; i++) atr = (atr * (n - 1) + tr[i]) / n;
    return atr;
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
  const api = { computeSR };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.OISR = api;
})(typeof window !== "undefined" ? window : globalThis);
