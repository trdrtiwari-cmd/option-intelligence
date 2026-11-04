/* Automatic trendlines (v1.19.3). Pure function, no chart code, so it can be tested with node.
   Support line   = connects two swing LOWS, every close since the first point stays above it (small tolerance).
   Resistance line= connects two swing HIGHS, every close since the first point stays below it.
   Among the valid lines the one with the most touches wins; ties go to the most recent second point, then the longer line.
   A line is BROKEN when one of the last three bars closed beyond it by more than the tolerance.
   cs = [{time, open, high, low, close}] ascending (chart time). Returns {support, resistance, atr} - each line or null. */
(function (root) {
  "use strict";
  function atrOf(cs, n) {
    n = n || 14;
    if (cs.length < n + 1) return null;
    const tr = [];
    for (let i = 1; i < cs.length; i++) tr.push(Math.max(cs[i].high - cs[i].low, Math.abs(cs[i].high - cs[i - 1].close), Math.abs(cs[i].low - cs[i - 1].close)));
    let a = tr.slice(0, n).reduce((x, y) => x + y, 0) / n;
    for (let i = n; i < tr.length; i++) a = (a * (n - 1) + tr[i]) / n;
    return a;
  }

  function computeTrendlines(cs, opts) {
    opts = opts || {};
    const W = opts.window || 150, L = opts.pivot || 3, out = { support: null, resistance: null, atr: null };
    const n = cs.length;
    if (n < 30) return out;
    const atr = opts.atr || atrOf(cs);
    if (!atr) return out;
    out.atr = atr;
    const tol = (opts.tolAtr || 0.25) * atr, start = Math.max(L, n - W);
    const lows = [], highs = [];
    for (let i = start; i < n - L; i++) {
      let isH = true, isL = true;
      for (let k = 1; k <= L; k++) {
        if (cs[i].high <= cs[i - k].high || cs[i].high < cs[i + k].high) isH = false;
        if (cs[i].low >= cs[i - k].low || cs[i].low > cs[i + k].low) isL = false;
      }
      if (isH) highs.push({ i, p: cs[i].high });
      if (isL) lows.push({ i, p: cs[i].low });
    }

    function best(piv, sup) {
      let pick = null;
      const recent = piv.slice(-6);
      for (const b of recent) {
        for (const a of piv) {
          if (a.i >= b.i || b.i - a.i < 5) continue;
          const m = (b.p - a.p) / (b.i - a.i), at = k => a.p + m * (k - a.i);
          let ok = true;
          for (let k = a.i; k < n - 3 && ok; k++) {                 // closes must respect the line (the last 3 bars may break it)
            const d = sup ? cs[k].close - at(k) : at(k) - cs[k].close;
            if (d < -tol) ok = false;
            if (k > a.i && k < b.i) {                                // wicks between the two anchors may not slice deep through it
              const w = sup ? cs[k].low - at(k) : at(k) - cs[k].high;
              if (w < -2 * tol) ok = false;
            }
          }
          if (!ok) continue;
          if (Math.abs(m) * (n - 1 - a.i) > 12 * atr) continue;      // absurdly steep: a spike, not a trend
          if (Math.abs(cs[n - 4].close - at(n - 4)) > (opts.maxDistAtr || 4) * atr) continue;   // too far from price to matter (checked before a fresh break)
          const touches = piv.filter(q => q.i >= a.i && Math.abs(q.p - at(q.i)) <= tol).length;
          const score = touches * 1000 + b.i + (b.i - a.i) / 1000;
          if (!pick || score > pick.score) pick = { a, b, m, touches, score };
        }
      }
      if (!pick) return null;
      const { a, m } = pick, last = n - 1, now = a.p + m * (last - a.i);
      const beyond = k => (sup ? (a.p + m * (k - a.i)) - cs[k].close : cs[k].close - (a.p + m * (k - a.i)));
      const broken = beyond(last) > tol || ([last - 1, last - 2].some(k => k > a.i && beyond(k) > tol) && beyond(last) > 0);
      const slopeAtr = m / atr;
      return {
        kind: sup ? "support" : "resistance", from: { time: cs[a.i].time, value: a.p }, to: { time: cs[last].time, value: now },
        touches: pick.touches, slopePerBar: m, direction: slopeAtr > 0.02 ? "rising" : slopeAtr < -0.02 ? "falling" : "flat",
        value: now, broken, distance: cs[last].close - now, bars: last - a.i
      };
    }
    out.support = best(lows, true);
    out.resistance = best(highs, false);
    return out;
  }

  const api = { computeTrendlines };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.OITrendlines = api;
})(typeof window !== "undefined" ? window : globalThis);
