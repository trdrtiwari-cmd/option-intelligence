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
