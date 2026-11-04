const {computeTrendlines}=require(process.argv[2]);
function mk(fn,n=120,seed=3){let s=seed;const r=()=>{s=(s*9301+49297)%233280;return s/233280};const out=[];let prev=fn(0);
 for(let i=0;i<n;i++){const c=fn(i)+(r()-.5)*6;const o=prev;out.push({time:1e9+i*300,open:o,close:c,high:Math.max(o,c)+r()*4,low:Math.min(o,c)-r()*4});prev=c}return out}
const up=mk(i=>23800+i*1.2+12*Math.sin(i/4));
const t=computeTrendlines(up);console.log('UP support',t.support&&{dir:t.support.direction,touches:t.support.touches,broken:t.support.broken,val:t.support.value.toFixed(1),last:up.at(-1).close.toFixed(1)});
console.log('UP resistance',t.resistance&&{dir:t.resistance.direction,touches:t.resistance.touches,broken:t.resistance.broken});
const dn=mk(i=>24000-i*1.2+12*Math.sin(i/4));const d=computeTrendlines(dn);
console.log('DOWN resistance',d.resistance&&{dir:d.resistance.direction,touches:d.resistance.touches,broken:d.resistance.broken});
// break: uptrend then a hard drop in the last 3 bars
const br=up.slice();for(let k=1;k<=3;k++){const c=br[br.length-k];br[br.length-k]={...c,open:c.open-60,close:c.close-70,low:c.low-75,high:c.high-55}}
const b=computeTrendlines(br);console.log('BREAK support',b.support&&{dir:b.support.direction,broken:b.support.broken});
console.log('short',JSON.stringify(computeTrendlines(up.slice(0,10))));
// 4 Nov 2026 shape: gap-up top, V down to the afternoon low, rally, final breakout bar.
// The resistance must NOT join the morning top to the afternoon highs across the V, and the support must start at the V low.
{
  let s = 11; const r = () => { s = (s * 9301 + 49297) % 233280; return s / 233280; };
  const path = []; const seg = (n, a, b, nz) => { for (let i = 0; i < n; i++) path.push([a + (b - a) * (i + 1) / n, nz]); };
  seg(30,23690,23720,14); seg(3,23720,23940,20); seg(12,23940,23945,18); seg(10,23945,23850,18); seg(14,23850,23790,16); seg(6,23790,23745,14);
  seg(6,23745,23820,14); seg(3,23820,23800,10); seg(6,23800,23870,14); seg(3,23870,23855,10); seg(8,23855,23915,12); seg(9,23915,23928,9); seg(1,23928,23960,4);
  const cs = []; let p = 23690; path.forEach(([t, nz], i) => { const o = p, c = t + (r() - .5) * nz * 1.4; cs.push({time: i, open: o, close: c, high: Math.max(o, c) + r() * nz * .7, low: Math.min(o, c) - r() * nz * .7}); p = c; });
  const t = computeTrendlines(cs);
  console.log('DAY', JSON.stringify({sFrom: t.support && t.support.from.time, sDir: t.support && t.support.direction, rFrom: t.resistance && t.resistance.from.time, rDir: t.resistance && t.resistance.direction}));
}
