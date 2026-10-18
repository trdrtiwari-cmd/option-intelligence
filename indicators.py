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


def trend_state(raw_candles):
    """Higher-timeframe read: BULL / BEAR / MIXED from EMA 9/21 + Supertrend."""
    cs = normalize_candles(raw_candles)
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


def analyze(raw_candles, symbol="NIFTY", interval=5, htf_raw=None, htf_interval=None):
    cs = normalize_candles(raw_candles)
    if len(cs) < 40:
        return {"status": "insufficient_data", "message": "Not enough candles to calculate indicators.",
                "score": 0, "bull_score": 0, "bear_score": 0, "decision": "WAIT", "signal": "WAIT", "regime": "NEUTRAL",
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
    htf = trend_state(htf_raw) if htf_raw else None

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
         "bull": bool(rsi and 55 < rsi < 80), "bear": bool(rsi and 20 < rsi < 45),
         "detail": f"RSI {rsi:.1f}" if rsi is not None else "n/a"},
        {"key": "adx", "label": "ADX (14) trend strength ≥ 20", "weight": 12, "ok": adx is not None,
         "bull": bool(adx and adx[0] >= 20 and adx[1] > adx[2]), "bear": bool(adx and adx[0] >= 20 and adx[2] > adx[1]),
         "detail": f"ADX {adx[0]:.1f} · +DI {adx[1]:.1f} · −DI {adx[2]:.1f}" if adx else "n/a"},
        {"key": "breakout", "label": "Breaks last 11-bar range", "weight": 10, "ok": True,
         "bull": last > recent_high, "bear": last < recent_low, "detail": f"Range {f(recent_low)} – {f(recent_high)}"},
        {"key": "volume", "label": "Volume expansion (≥ 1.2×)", "weight": 5, "ok": has_volume,
         "bull": rel_vol >= 1.2 and last > closes[-2], "bear": rel_vol >= 1.2 and last < closes[-2],
         "detail": f"{rel_vol:.2f}× avg" if has_volume else "No volume in feed (index) — excluded"},
        {"key": "htf", "label": f"Higher timeframe ({htf_interval or 15}m) alignment", "weight": 18, "ok": htf is not None,
         "bull": bool(htf and htf["state"] == "BULL"), "bear": bool(htf and htf["state"] == "BEAR"),
         "detail": f"{htf['state']} · EMA {htf['ema9']:,.0f}/{htf['ema21']:,.0f} · ST {htf['supertrend']}" if htf else "Unavailable — excluded"},
    ]
    available = sum(c["weight"] for c in checks if c["ok"])
    bull = round(sum(c["weight"] for c in checks if c["ok"] and c["bull"]) / available * 100)
    bear = round(sum(c["weight"] for c in checks if c["ok"] and c["bear"]) / available * 100)

    separation = abs(e9[-1] - e21[-1]) / last * 100 if last else 0
    # ADX decides when available; the EMA-distance fallback is only for feeds too short to compute it.
    sideways = adx[0] < 15 if adx is not None else (separation < 0.025 and abs(last - vwap) / last * 100 < 0.08)
    regime = "SIDEWAYS" if sideways else "BULLISH" if bull > bear else "BEARISH" if bear > bull else "NEUTRAL"

    squeeze = bool(bb and bb[2])
    side = "CE" if bull > bear else "PE" if bear > bull else None
    dom = bull if side == "CE" else bear
    want = "bull" if side == "CE" else "bear"
    by_key = {c["key"]: c for c in checks}
    gates = []
    if side:
        gates = [
            {"label": "Score ≥ 70", "pass": dom >= 70, "detail": f"{dom}/100"},
            {"label": "Trend strength (ADX ≥ 20, DI agrees)", "pass": by_key["adx"][want], "detail": by_key["adx"]["detail"]},
            {"label": "Supertrend agrees", "pass": by_key["supertrend"][want], "detail": by_key["supertrend"]["detail"]},
            {"label": "Higher timeframe agrees" if htf else "Higher timeframe (unavailable)", "pass": (by_key["htf"][want] if htf else True),
             "detail": by_key["htf"]["detail"]},
            {"label": "Not sideways", "pass": not sideways, "detail": regime},
            {"label": "Momentum not overextended (RSI < 80 / > 20)", "pass": rsi is None or (rsi < 80 if side == "CE" else rsi > 20),
             "detail": f"RSI {rsi:.1f}" if rsi is not None else "n/a"},
        ]
    failed = [g for g in gates if not g["pass"]]
    if side and not failed:
        signal, decision = f"CONFIRMED_{side}", f"CONFIRMED {side} SETUP — enter only on a retest that holds"
    elif side and dom >= 55 and len(failed) <= 2:
        signal, decision = f"WATCH_{side}", f"WATCH {side} — waiting for: {failed[0]['label']}"
    else:
        signal, decision = "WAIT", "WAIT"

    return {
        "status": "ok", "symbol": symbol, "interval": interval,
        "decision": decision, "signal": signal, "side": side, "score": max(bull, bear), "bull_score": bull, "bear_score": bear,
        "regime": regime, "sideways": sideways, "checks": checks, "gates": gates,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "ema9": round(e9[-1], 2), "ema21": round(e21[-1], 2),
        "vwap_approx": round(vwap, 2), "vwap_source": vwap_source,
        "atr": round(atr, 2) if atr is not None else None,
        "macd": {"line": round(mc[0], 2), "signal": round(mc[1], 2), "hist": round(mc[2], 2)} if mc else None,
        "adx": {"adx": round(adx[0], 1), "plus_di": round(adx[1], 1), "minus_di": round(adx[2], 1)} if adx else None,
        "supertrend": {"direction": "UP" if st[0] == 1 else "DOWN", "level": round(st[1], 2), "bars_since_flip": st[2]} if st else None,
        "bollinger": {"percent_b": round(bb[0], 2), "bandwidth_pct": round(bb[1], 2), "squeeze": squeeze} if bb else None,
        "htf": htf,
        "relative_volume": round(rel_vol, 2) if has_volume else None,
        "last_close": last, "breakout_high": recent_high, "breakdown_low": recent_low,
        "score_basis": f"Scored on {available} of 100 weight" + ("" if has_volume else " (no volume in feed)") + ("" if htf else " (no higher-timeframe data)"),
        "note": "Bias only, not a guarantee. A confirmed signal still needs a retest, room to the next S/R level, and your own risk limits.",
    }
