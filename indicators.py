"""Pure-Python indicator and scoring helpers (no web framework imports).

Formulas follow TradingView conventions so numbers on the dashboard line up with
your TradingView charts:
  * RSI  - Wilder smoothing (ta.rsi)
  * ATR  - Wilder / RMA smoothing (ta.atr)
  * VWAP - anchored to each IST trading session. Index feeds carry no volume, so
           a typical-price average is used and clearly labelled instead.
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def parse_time(value):
    """Return an aware datetime for an Upstox candle timestamp (ISO string / epoch)."""
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=IST)


def normalize_candles(rows):
    """Sort ascending by time, drop duplicates / malformed rows. Upstox returns newest-first."""
    seen, out = set(), []
    for c in rows:
        try:
            dt = parse_time(c["time"])
            item = {
                "time": c["time"],
                "_dt": dt,
                "open": float(c["open"]), "high": float(c["high"]),
                "low": float(c["low"]), "close": float(c["close"]),
                "volume": float(c.get("volume") or 0),
                "oi": c.get("oi"),
            }
        except (KeyError, TypeError, ValueError):
            continue
        key = dt.timestamp()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    out.sort(key=lambda x: x["_dt"])
    return out


def drop_forming(cs, interval, now=None):
    """Ignore the candle that is still being formed. Its partial range / volume makes every indicator
    (and every gate) flicker intra-bar and produces signals that vanish by the bar close ("repainting").
    Returns (candles, dropped?)."""
    if not cs or not interval:
        return cs, False
    now = now or datetime.now(timezone.utc)
    if cs[-1]["_dt"] + timedelta(minutes=interval) > now:
        return cs[:-1], True
    return cs, False


def pullback_resume(cs, e9, atr, side, lookback=6, tol=0.25):
    """Entry trigger: price recently pulled back to (within `tol` ATR of) EMA 9 and the last CLOSED bar
    resumes the trend (closes in the trade direction and beyond EMA 9). Returns (ok, detail)."""
    n = len(cs)
    if n < lookback + 2 or not atr:
        return False, "n/a"
    last = cs[-1]
    window = range(n - 1 - lookback, n - 1)
    if side == "PE":
        touched = any(cs[i]["high"] >= e9[i] - tol * atr for i in window)
        resumed = last["close"] < last["open"] and last["close"] < e9[-1]
    else:
        touched = any(cs[i]["low"] <= e9[i] + tol * atr for i in window)
        resumed = last["close"] > last["open"] and last["close"] > e9[-1]
    detail = (f"{'pullback to EMA 9 seen' if touched else f'no pullback to EMA 9 in last {lookback} bars (extended move)'}"
              f" · last closed bar {'resumes' if resumed else 'does NOT resume'} the trend")
    return touched and resumed, detail


# Signal modes. "standard" is the original, strict rule set (unchanged behaviour). "scalp" is tuned for a small option
# buyer who is happy with a 20-40 point index move: it trades price structure instead of lagging indicators, accepts a
# continuation break instead of demanding an EMA 9 retest, and needs less room to the next S/R level.
# v1.11.2 corrections from the 10-session replay (see README). Both can be switched off from the environment.
#  * RSI momentum floor: every entry with RSI "extremity" >= 40 (PE: RSI >= 40, CE: RSI <= 60) lost - 5 of 5 in both modes.
#  * CE needs price-structure proof: the 9 CE entries that rested on lagging indicators alone (generic BULLISH label) won 2 of 9.
import os as _os
CE_NEEDS_STRUCTURE = _os.getenv("CE_NEEDS_STRUCTURE", "1") != "0"
RSI_MOMENTUM = float(_os.getenv("RSI_MOMENTUM", "40"))

MODES = {
    "standard": {"label": "Standard", "rsi_lo": 30, "rsi_hi": 70, "rsi_score_lo": 20, "rsi_score_hi": 80, "chase_atr": 1.0,
                 "room_atr": 1.0, "relax_lagging": False, "micro_trigger": False,
                 "fresh_bars": 3, "expire_bars": 12, "max_chase_atr": 1.0, "rsi_momentum": 40, "ce_needs_structure": CE_NEEDS_STRUCTURE},
    "scalp": {"label": "Scalp (20–40 pt moves)", "rsi_lo": 30, "rsi_hi": 70, "rsi_score_lo": 20, "rsi_score_hi": 80, "chase_atr": 3.5,
              "room_atr": 0.5, "relax_lagging": True, "micro_trigger": True,
              "fresh_bars": 2, "expire_bars": 6, "max_chase_atr": 0.6, "rsi_momentum": 40, "ce_needs_structure": CE_NEEDS_STRUCTURE},
}


def mode_rules(mode):
    return MODES.get(mode) or MODES["standard"]


def micro_continuation(cs, atr, side, lookback=3, min_body=0.25):
    """Scalp entry trigger: a short pause (1-3 counter-trend candles) happened within the last `lookback` closed bars
    BEFORE the latest bar, and the latest closed bar is a real trend-direction candle that closes beyond the pause's
    extreme. Straight runs with no pause are not entries (that is chasing); the trigger lapses `lookback` bars after
    the pause so every signal run is short and a fresh signal is always close to the break. Returns (ok, detail)."""
    n = len(cs)
    if n < lookback + 3 or not atr:
        return False, "n/a"
    last = cs[-1]
    pe = side == "PE"
    is_counter = (lambda c: c["close"] >= c["open"]) if pe else (lambda c: c["close"] <= c["open"])
    trend_bar = last["close"] < last["open"] if pe else last["close"] > last["open"]
    recent = list(range(n - 1 - lookback, n - 1))
    counters = [i for i in recent if is_counter(cs[i])]
    if not counters:
        return False, f"no pause in the last {lookback} bars (straight run — wait for the first pause, don't chase)"
    j = counters[-1]
    group = [j]
    while group[0] - 1 >= 0 and is_counter(cs[group[0] - 1]) and group[0] - 1 >= n - 1 - lookback - 2:
        group.insert(0, group[0] - 1)
    level = min(cs[i]["low"] for i in group) if pe else max(cs[i]["high"] for i in group)
    broke = last["close"] < level if pe else last["close"] > level
    body = abs(last["close"] - last["open"])
    big = body >= min_body * atr
    ok = bool(trend_bar and broke and big)
    word = "below" if pe else "above"
    detail = (f"pause of {len(group)} bar(s), pause {'low' if pe else 'high'} {level:,.2f}; last closed bar "
              f"{'breaks ' + word + ' it' if (trend_bar and broke) else 'has NOT broken ' + word + ' it'}"
              + ("" if big or not (trend_bar and broke) else f" but body is only {body / atr:.2f} ATR (needs ≥ {min_body})"))
    return ok, detail


def ema(values, n):
    if not values:
        return []
    alpha = 2 / (n + 1)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1 - alpha) * out[-1])
    return out


def rsi_wilder(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def true_ranges(cs):
    return [
        max(cs[i]["high"] - cs[i]["low"],
            abs(cs[i]["high"] - cs[i - 1]["close"]),
            abs(cs[i]["low"] - cs[i - 1]["close"]))
        for i in range(1, len(cs))
    ]


def atr_wilder(cs, n=14):
    trs = true_ranges(cs)
    if len(trs) < n:
        return None
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    return atr


def session_vwap(cs):
    """VWAP of the latest IST session. Returns (value, source)."""
    if not cs:
        return None, "UNAVAILABLE"
    last_day = cs[-1]["_dt"].astimezone(IST).date()
    session = [c for c in cs if c["_dt"].astimezone(IST).date() == last_day]
    pv = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in session)
    vol = sum(c["volume"] for c in session)
    if vol > 0:
        return pv / vol, "VOLUME_VWAP"
    typical = [(c["high"] + c["low"] + c["close"]) / 3 for c in session]
    return sum(typical) / len(typical), "TYPICAL_PRICE_AVG (no volume in feed)"


def atr_series(cs, n=14):
    """Wilder ATR for every bar (None until n true ranges exist)."""
    out = [None] * len(cs)
    trs = true_ranges(cs)
    if len(trs) < n:
        return out
    atr = sum(trs[:n]) / n
    out[n] = atr
    for i in range(n, len(trs)):
        atr = (atr * (n - 1) + trs[i]) / n
        out[i + 1] = atr
    return out


def macd(closes, fast=12, slow=26, signal=9):
    """Returns (macd, signal, histogram, previous_histogram) for the latest bar."""
    if len(closes) < slow + signal:
        return None
    line = [a - b for a, b in zip(ema(closes, fast), ema(closes, slow))]
    sig = ema(line, signal)
    hist = [m - s for m, s in zip(line, sig)]
    return line[-1], sig[-1], hist[-1], hist[-2]


def adx_wilder(cs, n=14):
    """Returns (adx, +di, -di) using Wilder smoothing (matches ta.dmi)."""
    if len(cs) < n * 2 + 1:
        return None
    trs = true_ranges(cs)
    pdm, mdm = [], []
    for i in range(1, len(cs)):
        up, down = cs[i]["high"] - cs[i - 1]["high"], cs[i - 1]["low"] - cs[i]["low"]
        pdm.append(up if up > down and up > 0 else 0.0)
        mdm.append(down if down > up and down > 0 else 0.0)
    str_, spdm, smdm = sum(trs[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs, pdi, mdi = [], 0.0, 0.0
    for i in range(n, len(trs) + 1):
        if i > n:
            str_ = str_ - str_ / n + trs[i - 1]
            spdm = spdm - spdm / n + pdm[i - 1]
            smdm = smdm - smdm / n + mdm[i - 1]
        pdi = 100 * spdm / str_ if str_ else 0.0
        mdi = 100 * smdm / str_ if str_ else 0.0
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else 0.0)
    if len(dxs) < n:
        return None
    adx = sum(dxs[:n]) / n
    for dx in dxs[n:]:
        adx = (adx * (n - 1) + dx) / n
    return adx, pdi, mdi


def supertrend(cs, period=10, mult=3.0):
    """Returns (direction, level, bars_since_flip); direction 1 = bullish, -1 = bearish."""
    atrs = atr_series(cs, period)
    fu = fl = None
    direction, flip_at = 1, 0
    level = None
    for i, c in enumerate(cs):
        if atrs[i] is None:
            continue
        hl2 = (c["high"] + c["low"]) / 2
        up, lo = hl2 + mult * atrs[i], hl2 - mult * atrs[i]
        prev_close = cs[i - 1]["close"]
        fu = up if fu is None or up < fu or prev_close > fu else fu
        fl = lo if fl is None or lo > fl or prev_close < fl else fl
        if direction == 1 and c["close"] < fl:
            direction, flip_at = -1, i
        elif direction == -1 and c["close"] > fu:
            direction, flip_at = 1, i
        level = fl if direction == 1 else fu
    if level is None:
        return None
    return direction, level, len(cs) - 1 - flip_at


def bollinger(closes, n=20, k=2.0):
    """Returns (%B, bandwidth %, squeeze?) - squeeze = bandwidth in the lowest 20% of the last 100 bars."""
    if len(closes) < n + 5:
        return None
    def band(end):
        w = closes[end - n:end]
        m = sum(w) / n
        sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
        return m, m + k * sd, m - k * sd
    m, up, lo = band(len(closes))
    pct_b = (closes[-1] - lo) / (up - lo) if up != lo else 0.5
    widths = []
    for end in range(max(n, len(closes) - 100), len(closes) + 1):
        mm, uu, ll = band(end)
        widths.append((uu - ll) / mm * 100 if mm else 0)
    bw = widths[-1]
    rank = sum(1 for w in widths if w <= bw) / len(widths)
    return pct_b, bw, rank <= 0.2


def trend_state(raw_candles, interval=None, now=None):
    """Higher-timeframe read: BULL / BEAR / MIXED from EMA 9/21 + Supertrend (closed candles only)."""
    cs, _ = drop_forming(normalize_candles(raw_candles), interval, now)
    if len(cs) < 40:
        return None
    closes = [c["close"] for c in cs]
    e9, e21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
    st = supertrend(cs)
    if st is None:
        return None
    bull = e9 > e21 and st[0] == 1 and closes[-1] > e21
    bear = e9 < e21 and st[0] == -1 and closes[-1] < e21
    return {"state": "BULL" if bull else "BEAR" if bear else "MIXED", "ema9": round(e9, 2), "ema21": round(e21, 2),
            "supertrend": "UP" if st[0] == 1 else "DOWN", "close": closes[-1]}


def merge_volume(index_raw, fut_raw, min_match=0.5):
    """Attach futures volume to index candles (matched by timestamp). Returns the merged list, or None when
    the futures data is empty or lines up with too few index candles (better no volume than wrong volume)."""
    idx, fut = normalize_candles(index_raw), normalize_candles(fut_raw)
    if not idx or not fut or sum(c["volume"] for c in fut) <= 0:
        return None
    vol = {c["_dt"].timestamp(): c["volume"] for c in fut}
    matched = sum(1 for c in idx if c["_dt"].timestamp() in vol)
    if matched / len(idx) < min_match:
        return None
    out = []
    for c in idx:
        row = {k: v for k, v in c.items() if k != "_dt"}
        row["volume"] = vol.get(c["_dt"].timestamp(), 0.0)
        out.append(row)
    return out


def efficiency_ratio(vals):
    """Kaufman efficiency ratio: 1 = straight line, 0 = pure chop."""
    path = sum(abs(vals[i] - vals[i - 1]) for i in range(1, len(vals)))
    return abs(vals[-1] - vals[0]) / path if path else 0.0


def count_ema_crosses(e9, e21, atr, n=20, dead_band=0.1):
    """EMA 9/21 crossovers in the last n bars. Wiggles inside +/- dead_band*ATR of the other EMA are
    ignored, so a single clean trend change is not counted as several crosses."""
    band = dead_band * (atr or 0)
    state, crosses = 0, 0
    for a, b in zip(e9[-n:], e21[-n:]):
        d = a - b
        cur = 1 if d > band else -1 if d < -band else 0
        if cur == 0:
            continue
        if state and cur != state:
            crosses += 1
        state = cur
    return crosses


def directional_structure(cs, e9, e21, atr, vwap, n=20):
    """Is price actually TRENDING right now (even if ADX / Supertrend / score still lag)?
    Returns {direction: +1 / -1 / 0, evidence: [...], broke_out: +1 / -1 / 0, ...}.
    Core requirement: EMA 9 vs 21 and the close are on the same side (EMA 21).
    Then it needs ONE hard proof (a still-running directional move, or a close beyond the prior range)
    plus at least one more confirmation (swing structure, or VWAP + EMA 21 slope)."""
    out = {"direction": 0, "evidence": [], "broke_out": 0, "eff_short": None, "net_atr": None}
    if len(cs) < n + 4 or not atr:
        return out
    closes = [c["close"] for c in cs]
    highs = [c["high"] for c in cs]
    lows = [c["low"] for c in cs]
    last = closes[-1]
    eff20, eff8 = efficiency_ratio(closes[-(n + 1):]), efficiency_ratio(closes[-9:])
    net20, net8 = closes[-1] - closes[-(n + 1)], closes[-1] - closes[-9]
    ref = cs[-(n + 3):-3]                       # the range as it stood before the last 3 bars
    ref_hi, ref_lo = max(c["high"] for c in ref), min(c["low"] for c in ref)
    recent = closes[-3:]
    broke = 1 if (max(recent) > ref_hi and last > e21[-1]) else -1 if (min(recent) < ref_lo and last < e21[-1]) else 0
    out.update(broke_out=broke, eff_short=round(eff8, 2), net_atr=round(net20 / atr, 1))
    for d, word in ((1, "up"), (-1, "down")):
        if not (d * (e9[-1] - e21[-1]) > 0 and d * (last - e21[-1]) > 0):
            continue
        ev, hard = [], False
        slow = eff20 >= 0.35 and d * net20 >= 2.0 * atr and d * net8 >= 1.0 * atr     # sustained move that is still running
        fast = eff8 >= 0.55 and d * net8 >= 2.5 * atr                                 # sharp burst (bigger than a range leg)
        if slow or fast:
            span, bars = (net20, n) if slow else (net8, 8)
            ev.append(f"{abs(span) / atr:.1f} ATR move {word} in {bars} bars (efficiency {eff20 if slow else eff8:.2f})")
            hard = True
        if broke == d:
            ev.append(f"closed {'above' if d == 1 else 'below'} the prior {n}-bar range")
            hard = True
        if d == 1:
            struct = min(lows[-5:]) > min(lows[-10:-5]) and max(highs[-5:]) > max(highs[-10:-5])
        else:
            struct = max(highs[-5:]) < max(highs[-10:-5]) and min(lows[-5:]) < min(lows[-10:-5])
        if struct:
            ev.append("higher highs / higher lows" if d == 1 else "lower highs / lower lows")
        if d * (last - vwap) > 0 and d * (e21[-1] - e21[-4]) > 0.1 * atr:
            ev.append(f"{'above' if d == 1 else 'below'} VWAP with EMA 21 sloping {word}")
        if hard and len(ev) >= 2:
            out["direction"], out["evidence"] = d, ev
            break
    return out


def analyze(raw_candles, symbol="NIFTY", interval=5, htf_raw=None, htf_interval=None, volume_source=None, now=None, mode="standard"):
    rules = mode_rules(mode)
    mode = mode if mode in MODES else "standard"
    cs, forming_dropped = drop_forming(normalize_candles(raw_candles), interval, now)
    if len(cs) < 40:
        return {"status": "insufficient_data", "message": "Not enough candles to calculate indicators.",
                "score": 0, "bull_score": 0, "bear_score": 0, "decision": "WAIT", "signal": "WAIT", "regime": "NEUTRAL", "regime_label": "NEUTRAL", "regime_phase": None,
                "checks": [], "gates": [], "symbol": symbol, "interval": interval}
    closes = [c["close"] for c in cs]
    highs = [c["high"] for c in cs]
    lows = [c["low"] for c in cs]
    volumes = [c["volume"] for c in cs]
    last = closes[-1]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    rsi = rsi_wilder(closes)
    atr = atr_wilder(cs)
    vwap, vwap_source = session_vwap(cs)
    mc = macd(closes)
    adx = adx_wilder(cs)
    st = supertrend(cs)
    bb = bollinger(closes)
    htf = trend_state(htf_raw, htf_interval, now) if htf_raw else None

    has_volume = sum(volumes[-21:]) > 0
    prior = volumes[-21:-1]
    avg_vol = sum(prior) / len(prior) if prior else 0
    rel_vol = volumes[-1] / avg_vol if avg_vol else 0
    recent_high, recent_low = max(highs[-12:-1]), min(lows[-12:-1])
    f = lambda v, d=2: f"{v:,.{d}f}"

    # Each check: weight, bullish?, bearish?, available?, detail. Score is normalised by available weight.
    checks = [
        {"key": "ema", "label": "EMA 9 / 21 trend", "weight": 12, "ok": True,
         "bull": e9[-1] > e21[-1], "bear": e9[-1] < e21[-1], "detail": f"EMA9 {f(e9[-1])} vs EMA21 {f(e21[-1])}"},
        {"key": "supertrend", "label": "Supertrend (10, 3)", "weight": 15, "ok": st is not None,
         "bull": bool(st and st[0] == 1), "bear": bool(st and st[0] == -1),
         "detail": f"{'UP' if st[0] == 1 else 'DOWN'} · level {f(st[1])} · flipped {st[2]} bars ago" if st else "n/a"},
        {"key": "vwap", "label": "Price vs session VWAP / avg", "weight": 10, "ok": True,
         "bull": last > vwap, "bear": last < vwap, "detail": f"Close {f(last)} vs {f(vwap)}"},
        {"key": "macd", "label": "MACD (12, 26, 9) momentum", "weight": 10, "ok": mc is not None,
         "bull": bool(mc and mc[2] > 0 and mc[2] >= mc[3]), "bear": bool(mc and mc[2] < 0 and mc[2] <= mc[3]),
         "detail": f"Hist {mc[2]:+.2f} ({'rising' if mc[2] >= mc[3] else 'falling'})" if mc else "n/a"},
        {"key": "rsi", "label": "RSI (14) — strong but not stretched", "weight": 8, "ok": rsi is not None,
         "bull": bool(rsi and 55 < rsi < rules["rsi_score_hi"]), "bear": bool(rsi and rules["rsi_score_lo"] < rsi < 45),
         "detail": f"RSI {rsi:.1f}" if rsi is not None else "n/a"},
        {"key": "adx", "label": "ADX (14) trend strength ≥ 20", "weight": 12, "ok": adx is not None,
         "bull": bool(adx and adx[0] >= 20 and adx[1] > adx[2]), "bear": bool(adx and adx[0] >= 20 and adx[2] > adx[1]),
         "detail": f"ADX {adx[0]:.1f} · +DI {adx[1]:.1f} · −DI {adx[2]:.1f}" if adx else "n/a"},
        {"key": "breakout", "label": "Breaks last 11-bar range", "weight": 10, "ok": True,
         "bull": last > recent_high, "bear": last < recent_low, "detail": f"Range {f(recent_low)} – {f(recent_high)}"},
        {"key": "volume", "label": "Volume expansion (≥ 1.2×)" + (" — futures" if volume_source else ""), "weight": 5, "ok": has_volume,
         "bull": rel_vol >= 1.2 and last > closes[-2], "bear": rel_vol >= 1.2 and last < closes[-2],
         "detail": (f"{rel_vol:.2f}× avg ({volume_source})" if volume_source else f"{rel_vol:.2f}× avg") if has_volume else "Index has no volume and futures volume is unavailable (excluded) — see option volume row below"},
        {"key": "htf", "label": f"Higher timeframe ({htf_interval or 15}m) alignment", "weight": 18, "ok": htf is not None,
         "bull": bool(htf and htf["state"] == "BULL"), "bear": bool(htf and htf["state"] == "BEAR"),
         "detail": f"{htf['state']} · EMA {htf['ema9']:,.0f}/{htf['ema21']:,.0f} · ST {htf['supertrend']}" if htf else "Unavailable — excluded"},
    ]
    available = sum(c["weight"] for c in checks if c["ok"])
    bull = round(sum(c["weight"] for c in checks if c["ok"] and c["bull"]) / available * 100)
    bear = round(sum(c["weight"] for c in checks if c["ok"] and c["bear"]) / available * 100)

    separation = abs(e9[-1] - e21[-1]) / last * 100 if last else 0
    # ---- regime: trend vs range. ADX / Supertrend / score all LAG, so price structure decides first ----
    N = 20
    chop = {"efficiency": None, "range_atr": None, "crosses": 0, "range_low": None, "range_high": None}
    if len(cs) > N + 1 and atr:
        w = closes[-(N + 1):]
        chop["efficiency"] = efficiency_ratio(w)                                 # Kaufman efficiency ratio
        chop["range_low"], chop["range_high"] = min(lows[-N:]), max(highs[-N:])
        chop["range_atr"] = (chop["range_high"] - chop["range_low"]) / atr
        chop["crosses"] = count_ema_crosses(e9, e21, atr, N)                     # with a dead band, no noise crosses
    ds = directional_structure(cs, e9, e21, atr, vwap, N)
    trend_dir = ds["direction"]
    eff = chop["efficiency"]
    # crossing EMAs alone no longer means "range": the market must also lack directional progress
    in_range = trend_dir == 0 and eff is not None and ((eff < 0.30 and chop["range_atr"] <= 6.0) or (chop["crosses"] >= 3 and eff < 0.40))
    adx_flat = adx[0] < 15 if adx is not None else (separation < 0.025 and abs(last - vwap) / last * 100 < 0.08)
    sideways = bool(trend_dir == 0 and (adx_flat or in_range))
    prior_bias = "BULLISH" if bull > bear else "BEARISH" if bear > bull else "NEUTRAL"
    regime_phase = None
    if trend_dir:
        regime = "BULLISH" if trend_dir == 1 else "BEARISH"
        regime_label = "UPTREND" if trend_dir == 1 else "DOWNTREND"
        regime_phase = ("BREAKOUT" if trend_dir == 1 else "BREAKDOWN") if ds["broke_out"] == trend_dir else "TREND"
        regime_note = f"{regime_label} ({regime_phase.lower()}): " + " · ".join(ds["evidence"])
        if adx is not None and adx[0] < 20:
            regime_note += (f" · ADX {adx[0]:.0f} still lagging — scalp mode trades on price structure" if rules["relax_lagging"]
                            else f" · ADX {adx[0]:.0f} still lagging, so entry gates may stay closed")
    elif sideways:
        regime, regime_label = "SIDEWAYS", "SIDEWAYS"
        if in_range:
            regime_note = (f"Range {chop['range_low']:,.0f}–{chop['range_high']:,.0f} ({chop['range_atr']:.1f} ATR) over last {N} bars · "
                           f"efficiency {chop['efficiency']:.2f} · {chop['crosses']} EMA crosses · no directional break yet. "
                           f"Wait for a close outside the range.")
        else:
            regime_note = f"ADX below 15 and no directional break — no trend strength · bias {prior_bias.lower()}"
    else:
        regime, regime_label = prior_bias, prior_bias
        regime_note = "Trending: price is making directional progress"

    squeeze = bool(bb and bb[2])
    side = "CE" if bull > bear else "PE" if bear > bull else None
    dom = bull if side == "CE" else bear
    want = "bull" if side == "CE" else "bear"
    by_key = {c["key"]: c for c in checks}
    gates = []
    if side:
        relax = rules["relax_lagging"]
        trend_agree = trend_dir == (1 if side == "CE" else -1)       # price structure itself proves the trend
        adx_ok = by_key["adx"][want] or (relax and trend_agree)
        st_ok = by_key["supertrend"][want] or (relax and trend_agree)
        lag_note = " · passes on price-structure trend (scalp)"
        if htf:
            against = "BEAR" if side == "CE" else "BULL"
            htf_ok = by_key["htf"][want] or (relax and htf["state"] != against)
            htf_label = "Higher timeframe not against" if relax else "Higher timeframe agrees"
        else:
            htf_ok, htf_label = True, "Higher timeframe (unavailable)"
        rsi_lo, rsi_hi = (rules["rsi_lo"], rules["rsi_hi"]) if (relax and trend_agree) else (30, 70)
        gates = [
            {"label": "Score ≥ 70", "pass": dom >= 70, "detail": f"{dom}/100"},
            {"label": "Trend strength (ADX ≥ 20, DI agrees)" + (" or price-structure trend" if relax else ""), "pass": bool(adx_ok),
             "detail": by_key["adx"]["detail"] + (lag_note if adx_ok and not by_key["adx"][want] else "")},
            {"label": "Supertrend agrees" + (" or price-structure trend" if relax else ""), "pass": bool(st_ok),
             "detail": by_key["supertrend"]["detail"] + (lag_note if st_ok and not by_key["supertrend"][want] else "")},
            {"label": htf_label, "pass": bool(htf_ok), "detail": by_key["htf"]["detail"]},
            {"label": "Not sideways / not in a range", "pass": not sideways, "detail": regime_note},
            {"label": f"Momentum not overextended (RSI < {rsi_hi} / > {rsi_lo})", "pass": rsi is None or (rsi < rsi_hi if side == "CE" else rsi > rsi_lo),
             "detail": (f"RSI {rsi:.1f}" if rsi is not None else "n/a") + (" · wider limit because the trend is confirmed" if (relax and trend_agree and (rsi_lo, rsi_hi) != (30, 70)) else "")},
        ]
        mom = RSI_MOMENTUM
        if mom and rsi is not None:
            ext = rsi if side == "PE" else 100 - rsi
            gates.append({"label": f"Momentum present (RSI {'≤ %g' % mom if side == 'PE' else '≥ %g' % (100 - mom)})", "pass": ext < mom,
                          "detail": f"RSI {rsi:.1f} — entries with weak momentum (RSI still near 50) lost 5 of 5 in the replay"})
        if side == "CE" and rules.get("ce_needs_structure"):
            gates.append({"label": "CE needs price-structure uptrend (not indicators alone)", "pass": trend_dir == 1,
                          "detail": regime_note if trend_dir == 1 else "No higher-high / EMA / range-break proof of an uptrend yet — CE entries on lagging indicators alone won 2 of 9 in the replay"})
        if atr:
            # Anti-chase gates: most of the day's losses came from entering after the move had already happened.
            stretch = (last - e9[-1]) if side == "CE" else (e9[-1] - last)
            bar_range = (cs[-1]["high"] - cs[-1]["low"]) / atr
            chase_ok = stretch <= rules["chase_atr"] * atr and (not relax or bar_range <= 2.5)
            gates.append({"label": (f"Not chasing (≤ {rules['chase_atr']:g} ATR beyond EMA 9, no blow-off bar)" if relax else "Not chasing (≤ 1 ATR beyond EMA 9)"),
                          "pass": chase_ok,
                          "detail": f"{stretch:+.1f} pts beyond EMA 9 ({stretch / atr:+.2f} ATR)" + (f" · last bar range {bar_range:.1f} ATR" if relax else "")})
            ok_pb, pb_detail = pullback_resume(cs, e9, atr, side)
            if rules["micro_trigger"]:
                ok_mc, mc_detail = micro_continuation(cs, atr, side)
                gates.append({"label": "Entry trigger (EMA 9 retest resumes, or break of the last pause)", "pass": ok_pb or ok_mc,
                              "detail": ("Retest: " + pb_detail) if ok_pb else ("Pause break: " + mc_detail) if ok_mc else f"{mc_detail} · {pb_detail}"})
            else:
                gates.append({"label": "Pullback-and-resume trigger (retest holds)", "pass": ok_pb, "detail": pb_detail})
    failed = [g for g in gates if not g["pass"]]
    if side and not failed:
        signal = f"CONFIRMED_{side}"
        decision = f"CONFIRMED {side} SETUP — " + ("enter on this continuation break; aim for 20–40 pts" if rules["micro_trigger"] else "enter only on a retest that holds")
    elif side and dom >= 55 and len(failed) <= 2:
        signal, decision = f"WATCH_{side}", f"WATCH {side} — waiting for: {failed[0]['label']}"
    else:
        signal, decision = "WAIT", "WAIT"

    return {
        "status": "ok", "symbol": symbol, "interval": interval, "mode": mode, "mode_label": rules["label"],
        "mode_rules": {k: rules[k] for k in ("room_atr", "fresh_bars", "expire_bars", "max_chase_atr")},
        "decision": decision, "signal": signal, "side": side, "score": max(bull, bear), "bull_score": bull, "bear_score": bear,
        "regime": regime, "regime_label": regime_label, "regime_phase": regime_phase, "regime_note": regime_note, "sideways": sideways, "prior_bias": prior_bias,
        "trend_evidence": ds["evidence"],
        "range": {"low": chop["range_low"], "high": chop["range_high"], "atr_multiple": round(chop["range_atr"], 2) if chop["range_atr"] is not None else None,
                  "efficiency": round(chop["efficiency"], 2) if chop["efficiency"] is not None else None, "ema_crosses": chop["crosses"], "broke_out": ds["broke_out"]}, "checks": checks, "gates": gates,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "ema9": round(e9[-1], 2), "ema21": round(e21[-1], 2),
        "vwap_approx": round(vwap, 2), "vwap_source": vwap_source + (f" ({volume_source})" if volume_source and vwap_source == "VOLUME_VWAP" else ""),
        "volume_source": volume_source,
        "atr": round(atr, 2) if atr is not None else None,
        "macd": {"line": round(mc[0], 2), "signal": round(mc[1], 2), "hist": round(mc[2], 2)} if mc else None,
        "adx": {"adx": round(adx[0], 1), "plus_di": round(adx[1], 1), "minus_di": round(adx[2], 1)} if adx else None,
        "supertrend": {"direction": "UP" if st[0] == 1 else "DOWN", "level": round(st[1], 2), "bars_since_flip": st[2]} if st else None,
        "bollinger": {"percent_b": round(bb[0], 2), "bandwidth_pct": round(bb[1], 2), "squeeze": squeeze} if bb else None,
        "htf": htf,
        "relative_volume": round(rel_vol, 2) if has_volume else None,
        "last_close": last, "breakout_high": recent_high, "breakdown_low": recent_low,
        "forming_candle_ignored": forming_dropped, "last_closed_bar": cs[-1]["time"],
        "score_basis": f"Scored on {available} of 100 weight" + ("" if has_volume else " (no volume in feed)") + (f" · volume from {volume_source}" if volume_source else "") + ("" if htf else " (no higher-timeframe data)"),
        "note": "Bias only, not a guarantee. Calculated on CLOSED candles only. A confirmed signal still needs room to the next S/R level and your own risk limits.",
    }
