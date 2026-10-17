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


def analyze(raw_candles, symbol="NIFTY", interval=5):
    cs = normalize_candles(raw_candles)
    if len(cs) < 30:
        return {"status": "insufficient_data", "message": "Not enough candles to calculate indicators.",
                "score": 0, "bull_score": 0, "bear_score": 0, "decision": "WAIT", "regime": "NEUTRAL",
                "symbol": symbol, "interval": interval}
    closes = [c["close"] for c in cs]
    highs = [c["high"] for c in cs]
    lows = [c["low"] for c in cs]
    volumes = [c["volume"] for c in cs]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    rsi = rsi_wilder(closes)
    atr = atr_wilder(cs)

    plus_dm, minus_dm = [], []
    for i in range(1, len(cs)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)

    has_volume = sum(volumes[-21:]) > 0
    prior = volumes[-21:-1]
    avg_vol = sum(prior) / len(prior) if prior else 0
    rel_vol = volumes[-1] / avg_vol if avg_vol else 0
    vwap, vwap_source = session_vwap(cs)
    last = closes[-1]
    recent_high, recent_low = max(highs[-12:-1]), min(lows[-12:-1])

    # (weight, bull?, bear?, available?) - score is normalised by the weight actually available,
    # so a feed without volume is not capped below the decision threshold.
    parts = [
        (20, e9[-1] > e21[-1], e9[-1] < e21[-1], True),
        (15, last > vwap, last < vwap, True),
        (10, rsi is not None and rsi > 55, rsi is not None and rsi < 45, rsi is not None),
        (10, sum(plus_dm[-14:]) > sum(minus_dm[-14:]), sum(minus_dm[-14:]) > sum(plus_dm[-14:]), True),
        (10, rel_vol >= 1.2 and last > closes[-2], rel_vol >= 1.2 and last < closes[-2], has_volume),
        (20, last > recent_high, last < recent_low, True),
    ]
    available = sum(w for w, _, _, ok in parts if ok)
    bull_pts = sum(w for w, b, _, ok in parts if ok and b)
    bear_pts = sum(w for w, _, s, ok in parts if ok and s)
    bull = round(bull_pts / available * 100)
    bear = round(bear_pts / available * 100)

    separation = abs(e9[-1] - e21[-1]) / last * 100 if last else 0
    sideways = separation < 0.025 and abs(last - vwap) / last * 100 < 0.08
    if sideways:
        regime = "SIDEWAYS"
    elif bull > bear:
        regime = "BULLISH"
    elif bear > bull:
        regime = "BEARISH"
    else:
        regime = "NEUTRAL"

    threshold = 70
    decision = "WAIT"
    if not sideways and bull >= threshold and bull > bear:
        decision = "BULLISH BIAS — CE SETUP ONLY AFTER RETEST"
    elif not sideways and bear >= threshold and bear > bull:
        decision = "BEARISH BIAS — PE SETUP ONLY AFTER RETEST"

    return {
        "status": "ok", "symbol": symbol, "interval": interval,
        "decision": decision, "score": max(bull, bear), "bull_score": bull, "bear_score": bear,
        "regime": regime, "sideways": sideways,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "ema9": round(e9[-1], 2), "ema21": round(e21[-1], 2),
        "vwap_approx": round(vwap, 2), "vwap_source": vwap_source,
        "atr": round(atr, 2) if atr is not None else None,
        "relative_volume": round(rel_vol, 2) if has_volume else None,
        "last_close": last, "breakout_high": recent_high, "breakdown_low": recent_low,
        "score_basis": f"Scored on {available} of 85 available points" + ("" if has_volume else " (volume not provided for this instrument)"),
        "note": "Bias only, not a confirmed entry. Breakout + retest and option-premium risk checks are required.",
    }
