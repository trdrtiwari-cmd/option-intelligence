/* Automatic trendlines (v1.19.4). Pure function, no chart code, so it can be tested with node.

   v1.19.3 joined any two swing points and allowed wicks to cut through the line, so it could draw a support line through
   candles and a "resistance" across a 200-point V (from the morning top to the afternoon highs). v1.19.4 uses the classic
   tangent construction:
     support    = an edge of the LOWER convex hull of the lows  -> no low between (and after) its two anchors is below it
     resistance = an edge of the UPPER convex hull of the highs -> no high is above it
   The hull is built from each swing point in the window, so the line can belong to the latest leg (e.g. the afternoon rally),
   not only to the day's extreme. An edge is a usable trendline only when
     - its anchors are >= 6 bars apart,
     - price never travelled more than 5 ATR away from it between the first anchor and now (otherwise it is a range / V, not a trend),
     - it is not steeper than 1 ATR per bar, is within 3 ATR of price before the latest bars, and was touched recently.
   Among usable lines: most touches (bars whose low / high come within 0.15 ATR of the line, counted once per swing), then the most
   recent second anchor, then the longer line. The last 3 bars may close through the line: that is reported as a BREAK.
   cs = [{time, open, high, low, close}] ascending. Returns {support, resistance, atr}; each line or null. */
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

  // Lower hull (sup=true) of points (i, low) or upper hull (sup=false) of points (i, high), i from `from` to `to`.
  function hull(cs, from, to, sup) {
    const h = [];
    const y = i => (sup ? cs[i].low : cs[i].high);
    const cross = (o, a, b) => (a - o) * (y(b) - y(o)) - (y(a) - y(o)) * (b - o);
    for (let i = from; i <= to; i++) {
      while (h.length >= 2 && (sup ? cross(h[h.length - 2], h[h.length - 1], i) <= 0 : cross(h[h.length - 2], h[h.length - 1], i) >= 0)) h.pop();
      h.push(i);
    }
    return h;
  }

  function computeTrendlines(cs, opts) {
    opts = opts || {};
    const W = opts.window || 150, L = opts.pivot || 3, out = { support: null, resistance: null, atr: null };
    const n = cs.length;
    if (n < 30) return out;
    const atr = opts.atr || atrOf(cs);
    if (!atr) return out;
    out.atr = atr;
    const start = Math.max(L, n - W), last = n - 1, settled = n - 4;     // the last 3 bars may break the line
    const touchTol = (opts.touchAtr || 0.15) * atr, breakTol = (opts.breakAtr || 0.25) * atr;
    const maxDev = (opts.maxDevAtr || 5) * atr, maxDist = (opts.maxDistAtr || 3) * atr, minSpan = opts.minSpan || 6;

    function pivots(sup) {
      const p = [];
      for (let i = start; i <= settled - L; i++) {
        let ok = true;
        for (let k = 1; k <= L && ok; k++) {
          if (sup ? (cs[i].low > cs[i - k].low || cs[i].low > cs[i + k].low) : (cs[i].high < cs[i - k].high || cs[i].high < cs[i + k].high)) ok = false;
        }
        if (ok) p.push(i);
      }
      return p;
    }

    function best(sup) {
      const y = i => (sup ? cs[i].low : cs[i].high);
      let pick = null;
      const seen = new Set();
      for (const s of pivots(sup)) {
        const h = hull(cs, s, settled, sup);
        for (let e = 0; e + 1 < h.length; e++) {
          const a = h[e], b = h[e + 1];
          if (b - a < minSpan || seen.has(a + ":" + b)) continue;
          seen.add(a + ":" + b);
          const m = (y(b) - y(a)) / (b - a), at = k => y(a) + m * (k - a);
          if (Math.abs(m) > atr) continue;                                            // > 1 ATR per bar: a spike, not a trend
          if (settled - b > Math.max(20, 1.5 * (b - a))) continue;                    // its last touch is too old for the line to still matter
          let dev = 0, valid = true;
          for (let k = a; k <= settled && valid; k++) {
            const g = sup ? cs[k].close - at(k) : at(k) - cs[k].close;
            if (g < -breakTol) valid = false;                                         // a close through it before the last bars
            dev = Math.max(dev, g);
          }
          if (!valid || dev > maxDev) continue;                                       // price left the line: range / V, not a trendline
          let devAB = 0;
          for (let k = a; k <= b; k++) devAB = Math.max(devAB, sup ? cs[k].close - at(k) : at(k) - cs[k].close);
          // too far from price to matter now - unless price is just at the other side of the channel it already swung through
          if (Math.abs(cs[settled].close - at(settled)) > Math.max(maxDist, devAB * 1.1)) continue;
          let touches = 0, lastTouch = -99;
          for (let k = a; k <= settled; k++) {
            if (Math.abs(y(k) - at(k)) <= touchTol && k - lastTouch >= 3) { touches++; lastTouch = k; }
          }
          const score = touches * 100000 + b * 100 + (b - a);
          if (!pick || score > pick.score) pick = { a, b, m, touches, score };
        }
      }
      if (!pick) return null;
      const { a, m } = pick, at = k => y(a) + m * (k - a), now = at(last);
      const beyond = k => (sup ? at(k) - cs[k].close : cs[k].close - at(k));
      const broken = beyond(last) > breakTol || ([last - 1, last - 2].some(k => k > a && beyond(k) > breakTol) && beyond(last) > 0);
      const slopeAtr = m / atr;
      return {
        kind: sup ? "support" : "resistance", from: { time: cs[a].time, value: y(a) }, to: { time: cs[last].time, value: now },
        anchor2: { time: cs[pick.b].time, value: y(pick.b) }, touches: pick.touches, slopePerBar: m,
        direction: slopeAtr > 0.02 ? "rising" : slopeAtr < -0.02 ? "falling" : "flat",
        value: now, broken, distance: cs[last].close - now, bars: last - a
      };
    }
    out.support = best(true);
    out.resistance = best(false);
    return out;
  }

  const api = { computeTrendlines };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.OITrendlines = api;
})(typeof window !== "undefined" ? window : globalThis);
