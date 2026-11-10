"""Leading entry triggers (v1.20.20) - they turn BEFORE the moving averages do.

The engine's entry trigger is the pullback-and-resume (price dips to EMA 9 and turns). EMA / ADX / Supertrend / MACD all lag, so on
steady one-way days the trigger never fires. These three triggers react to the turn itself:

  rsi_div    RSI divergence: price made a lower low (CE) / higher high (PE) but RSI did not, and the next candle confirms the turn
  stoch      Stochastic %K crosses %D coming out of oversold (< 20, CE) / overbought (> 80, PE)
  macd_turn  MACD histogram turns: the first rising bar after a fall below zero (CE) / first falling bar above zero (PE)

fires(cs, i, side, kind) is True when that trigger fires on candle i (only candles up to i are used - no look-ahead).
LEADING_TRIGGER (env / Signals setting) = off | rsi_div | stoch | macd_turn makes the chosen one an alternative entry trigger live;
Diagnostics measures all three on the same days first.
"""
import os

KINDS = ("rsi_div", "stoch", "macd_turn")
LABELS = {"rsi_div": "RSI divergence", "stoch": "Stochastic cross from oversold / overbought", "macd_turn": "MACD histogram turn"}
LEADING_TRIGGER = os.getenv("LEADING_TRIGGER", "off").strip().lower()
LEADING_TRIGGER = LEADING_TRIGGER if LEADING_TRIGGER in KINDS else "off"


class Series:
    """Indicator series for one candle list, computed once (replays call fires() for every candle)."""

    def __init__(self, cs):
        self.cs = cs
        closes = [c["close"] for c in cs]
        self.rsi = _rsi(closes)
        self.k, self.d = _stoch(cs)
        self.hist = _macd_hist(closes)


_CACHE = {"id": None, "s": None}


def series(cs):
    if _CACHE["id"] != (id(cs), len(cs)):
        _CACHE.update(id=(id(cs), len(cs)), s=Series(cs))
    return _CACHE["s"]


def _rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100 - 100 / (1 + ag / al) if al else 100.0
    for i in range(n + 1, len(closes)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100 - 100 / (1 + ag / al) if al else 100.0
    return out


def _stoch(cs, n=14, sk=3, sd=3):
    raw = [None] * len(cs)
    for i in range(n - 1, len(cs)):
        w = cs[i - n + 1:i + 1]
        hi, lo = max(c["high"] for c in w), min(c["low"] for c in w)
        raw[i] = 100.0 * (cs[i]["close"] - lo) / (hi - lo) if hi > lo else 50.0

    def sma(xs, m):
        out = [None] * len(xs)
        for i in range(len(xs)):
            win = xs[max(0, i - m + 1):i + 1]
            if len(win) == m and all(v is not None for v in win):
                out[i] = sum(win) / m
        return out
    k = sma(raw, sk)
    return k, sma(k, sd)


def _ema(xs, n):
    out, a, e = [], 2.0 / (n + 1), None
    for x in xs:
        e = x if e is None else e + a * (x - e)
        out.append(e)
    return out


def _macd_hist(closes):
    if len(closes) < 35:
        return [None] * len(closes)
    m = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    sig = _ema(m, 9)
    return [a - b for a, b in zip(m, sig)]


def _pivot(cs, i, low, L=2):
    if i - L < 0 or i + L >= len(cs):
        return False
    y = (lambda k: cs[k]["low"]) if low else (lambda k: cs[k]["high"])
    return all((y(i) <= y(i - k) and y(i) <= y(i + k)) if low else (y(i) >= y(i - k) and y(i) >= y(i + k)) for k in range(1, L + 1))


def fires(cs, i, side, kind):
    """True when `kind` fires for `side` on candle i, using candles 0..i only."""
    if kind not in KINDS or side not in ("CE", "PE") or i < 40:
        return False
    s = series(cs)
    up = side == "CE"
    if kind == "stoch":
        k0, d0, k1, d1 = s.k[i - 1], s.d[i - 1], s.k[i], s.d[i]
        if None in (k0, d0, k1, d1):
            return False
        return (k0 <= d0 and k1 > d1 and min(k0, d0) < 20) if up else (k0 >= d0 and k1 < d1 and max(k0, d0) > 80)
    if kind == "macd_turn":
        h0, h1, h2 = s.hist[i - 2], s.hist[i - 1], s.hist[i]
        if None in (h0, h1, h2):
            return False
        return (h1 < 0 and h1 < h0 and h2 > h1) if up else (h1 > 0 and h1 > h0 and h2 < h1)
    # rsi_div: the latest pivot (confirmed 2 candles later, at i-2 .. i-6) vs the previous pivot of the same kind within 25 candles
    for p2 in range(i - 2, max(i - 7, 2), -1):
        if not _pivot(cs[:i + 1], p2, up):
            continue
        for p1 in range(p2 - 4, max(p2 - 25, 2), -1):
            if not _pivot(cs[:i + 1], p1, up):
                continue
            r1, r2 = s.rsi[p1], s.rsi[p2]
            if r1 is None or r2 is None:
                return False
            if up:
                div = cs[p2]["low"] < cs[p1]["low"] and r2 > r1 + 2 and r1 < 40
                turn = cs[i]["close"] > cs[i - 1]["close"] and cs[i]["close"] > cs[p2]["high"]
            else:
                div = cs[p2]["high"] > cs[p1]["high"] and r2 < r1 - 2 and r1 > 60
                turn = cs[i]["close"] < cs[i - 1]["close"] and cs[i]["close"] < cs[p2]["low"]
            return bool(div and turn)
        return False
    return False
