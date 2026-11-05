"""Chart patterns from closed candles (v1.20.16) - pure Python, no I/O.

The engine used to confirm an entry with the LAST CANDLE only (bullish close above the previous close). This module reads the
SHAPE of the last ~80 candles instead:

  * Range (consolidation) and its breakout / breakdown
  * Bull / bear flag: a strong impulse, a shallow counter-move, then a break of the flag
  * Double bottom / double top: two tests of the same level, then a break of the neckline
  * Ascending / descending triangle: a flat side plus rising lows (falling highs), then a break of the flat side

detect() returns the most relevant pattern, newest first: a breakout in the last 3 bars beats a pattern still forming, which beats a
plain range. Every pattern carries its trigger level, invalidation level and a measured-move target, so it can be checked on a chart.
Levels use the same ATR as the rest of the app.
"""

PIVOT = 3


def _pivots(cs, kind, start, end):
    out = []
    for i in range(max(start, PIVOT), min(end, len(cs) - PIVOT)):
        if kind == "low":
            if all(cs[i]["low"] <= cs[i - k]["low"] and cs[i]["low"] <= cs[i + k]["low"] for k in range(1, PIVOT + 1)):
                out.append(i)
        else:
            if all(cs[i]["high"] >= cs[i - k]["high"] and cs[i]["high"] >= cs[i + k]["high"] for k in range(1, PIVOT + 1)):
                out.append(i)
    return out


def _mk(name, side, stage, trigger, invalid, target, detail, bars_ago=0):
    r = lambda v: None if v is None else round(float(v), 2)
    return {"name": name, "side": side, "stage": stage, "trigger": r(trigger), "invalidation": r(invalid), "target": r(target),
            "detail": detail, "bars_ago": bars_ago}


def _range(cs, atr, look=30, max_atr=2.5, min_bars=8):
    """Largest box of >= min_bars bars, ending at the bar before the last one, whose height is <= max_atr ATR."""
    n = len(cs)
    best = None
    for k in range(min_bars, min(look, n - 2) + 1):
        box = cs[n - 1 - k:n - 1]
        hi, lo = max(c["high"] for c in box), min(c["low"] for c in box)
        if hi - lo <= max_atr * atr:
            best = (k, hi, lo)
        elif best:
            break
    return best


def _range_pattern(cs, atr):
    b = _range(cs, atr)
    if not b:
        return None
    k, hi, lo = b
    c = cs[-1]["close"]
    h = hi - lo
    if c > hi + 0.1 * atr:
        return _mk("Range breakout", "CE", "breakout", hi, lo + h / 2, hi + h, f"{k}-bar range {lo:.0f}-{hi:.0f} ({h / atr:.1f} ATR) broken upward")
    if c < lo - 0.1 * atr:
        return _mk("Range breakdown", "PE", "breakout", lo, hi - h / 2, lo - h, f"{k}-bar range {lo:.0f}-{hi:.0f} ({h / atr:.1f} ATR) broken downward")
    return _mk("Range (consolidation)", None, "range", None, None, None,
               f"{k}-bar range {lo:.0f}-{hi:.0f} ({h / atr:.1f} ATR) - wait for a close outside it")


def _flag(cs, atr, side):
    """Impulse >= 3 ATR in <= 12 bars, then a 3-15 bar counter-move retracing 15-60 %, then a close beyond the flag."""
    n = len(cs)
    up = side == "CE"
    for end in range(n - 4, max(n - 20, 12), -1):            # impulse top (bull) / bottom (bear), newest first
        top = cs[end]["high"] if up else cs[end]["low"]
        seg = cs[max(0, end - 12):end + 1]
        base = min(c["low"] for c in seg) if up else max(c["high"] for c in seg)
        imp = (top - base) if up else (base - top)
        if imp < 3 * atr:
            continue
        ext = max(c["high"] for c in cs[end:n - 1]) if up else min(c["low"] for c in cs[end:n - 1])
        if (up and ext > top + 0.05 * atr) or (not up and ext < top - 0.05 * atr):
            continue                                          # not the impulse extreme
        flag = cs[end + 1:n - 1]
        if not 3 <= len(flag) <= 15:
            continue
        retr = (top - min(c["low"] for c in flag)) / imp if up else (max(c["high"] for c in flag) - top) / imp
        if not 0.15 <= retr <= 0.6:
            continue
        last3 = flag[-3:]
        trig = max(c["high"] for c in last3) if up else min(c["low"] for c in last3)
        inval = min(c["low"] for c in flag) if up else max(c["high"] for c in flag)
        c = cs[-1]["close"]
        name = "Bull flag" if up else "Bear flag"
        if (up and c > trig) or (not up and c < trig):
            return _mk(name + " breakout", side, "breakout", trig, inval, c + imp if up else c - imp,
                       f"impulse {imp / atr:.1f} ATR, flag {len(flag)} bars retracing {retr:.0%}, broken")
        return _mk(name + " (forming)", side, "forming", trig, inval, trig + imp if up else trig - imp,
                   f"impulse {imp / atr:.1f} ATR, flag {len(flag)} bars retracing {retr:.0%} - trigger {trig:.0f}")
    return None


def _double(cs, atr, side):
    """Double bottom (CE) / top (PE): two pivots within 0.35 ATR, >= 8 bars apart, a 2 ATR swing between, neckline break."""
    n = len(cs)
    lows = side == "CE"
    piv = _pivots(cs, "low" if lows else "high", n - 60, n - 1)
    y = (lambda i: cs[i]["low"]) if lows else (lambda i: cs[i]["high"])
    for j in range(len(piv) - 1, 0, -1):
        b = piv[j]
        if n - 1 - b > 20:
            break
        for a in reversed(piv[:j]):
            if b - a < 8:
                continue
            if abs(y(a) - y(b)) > 0.35 * atr:
                continue
            mid = cs[a:b + 1]
            neck = max(c["high"] for c in mid) if lows else min(c["low"] for c in mid)
            depth = (neck - min(y(a), y(b))) if lows else (max(y(a), y(b)) - neck)
            if depth < 2.0 * atr:
                continue
            c = cs[-1]["close"]
            level = min(y(a), y(b)) if lows else max(y(a), y(b))
            name = "Double bottom" if lows else "Double top"
            if (lows and c > neck) or (not lows and c < neck):
                return _mk(name + " breakout", side, "breakout", neck, level, neck + depth if lows else neck - depth,
                           f"lows {y(a):.0f} / {y(b):.0f} ({b - a} bars apart), neckline {neck:.0f} broken" if lows else
                           f"highs {y(a):.0f} / {y(b):.0f} ({b - a} bars apart), neckline {neck:.0f} broken")
            return _mk(name + " (forming)", side, "forming", neck, level, neck + depth if lows else neck - depth,
                       f"two tests of {level:.0f}; needs a close {'above' if lows else 'below'} {neck:.0f}")
    return None


def _triangle(cs, atr, side):
    """Ascending (CE): >= 2 pivot highs within 0.3 ATR (flat top) and rising pivot lows; descending (PE) mirrored."""
    n = len(cs)
    asc = side == "CE"
    flat = _pivots(cs, "high" if asc else "low", n - 40, n - 1)
    slope = _pivots(cs, "low" if asc else "high", n - 40, n - 1)
    if len(flat) < 2 or len(slope) < 2:
        return None
    fv = [cs[i]["high"] if asc else cs[i]["low"] for i in flat[-3:]]
    if max(fv) - min(fv) > 0.3 * atr:
        return None
    sv = [cs[i]["low"] if asc else cs[i]["high"] for i in slope[-3:]]
    rising = all(b > a for a, b in zip(sv, sv[1:])) if asc else all(b < a for a, b in zip(sv, sv[1:]))
    if not rising or abs(sv[-1] - sv[0]) < 0.5 * atr:
        return None
    top = max(fv) if asc else min(fv)
    height = abs(top - sv[0])
    c = cs[-1]["close"]
    name = "Ascending triangle" if asc else "Descending triangle"
    if (asc and c > top + 0.05 * atr) or (not asc and c < top - 0.05 * atr):
        return _mk(name + " breakout", side, "breakout", top, sv[-1], top + height if asc else top - height,
                   f"flat {'top' if asc else 'bottom'} {top:.0f}, {'higher lows' if asc else 'lower highs'}, broken")
    return _mk(name + " (forming)", side, "forming", top, sv[-1], top + height if asc else top - height,
               f"flat {'top' if asc else 'bottom'} {top:.0f} with {'higher lows' if asc else 'lower highs'} - trigger {top:.0f}")


def detect(cs, atr, look=80):
    """Most relevant chart pattern on closed candles, or {"name": "None", ...}. cs: dicts with open/high/low/close, oldest first."""
    if not atr or len(cs) < 30:
        return _mk("None", None, "none", None, None, None, "not enough candles")
    cs = cs[-look:]
    found = []
    for side in ("CE", "PE"):
        for fn in (_flag, _double, _triangle):
            try:
                p = fn(cs, atr, side)
            except (ValueError, ZeroDivisionError, IndexError):
                p = None
            if p:
                found.append(p)
    rp = _range_pattern(cs, atr)
    order = {"breakout": 0, "forming": 1, "range": 2}
    if rp and rp["stage"] == "breakout":
        found.append(rp)
    if found:
        found.sort(key=lambda p: order.get(p["stage"], 3))
        best = found[0]
        if best["stage"] == "forming" and rp and rp["stage"] == "range":
            best = dict(best, detail=best["detail"] + f" · inside a {rp['detail'].split(' - ')[0]}")
        return best
    return rp or _mk("None", None, "none", None, None, None, "no clear chart pattern")


def supports(pattern, side):
    """True when the pattern is a breakout in the direction of `side` (the entry confirmation the owner asked for)."""
    return bool(pattern and pattern.get("side") == side and pattern.get("stage") == "breakout")


def against(pattern, side):
    """True when the latest pattern is a breakout AGAINST `side` (e.g. a double top while the engine leans CE)."""
    return bool(pattern and pattern.get("side") and pattern.get("side") != side and pattern.get("stage") == "breakout")
