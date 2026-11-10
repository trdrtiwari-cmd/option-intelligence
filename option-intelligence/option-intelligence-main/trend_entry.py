"""v1.20.28: strong-trend entries - the pullback strategy's blind spot.

The live rules buy a PULLBACK (EMA 9 retest, RSI not overextended, not a fresh breakout, not chasing). On a strong one-way day
price never pulls back, RSI stays above 68 and every push is a "fresh breakout", so the engine sees the move all day and never
buys (5 and 6 Nov 2026). This module recognises that situation from CLOSED candles only and lists which of those pullback rules
may be relaxed. Everything else (score, trend strength, Supertrend, higher timeframe, not sideways, CE structure, entry window,
dashboard, 60 s reconfirm, profit check, daily limits) still applies.

Variants
  off           - nothing relaxed (default)
  rsi           - strong trend: RSI may go to 75 (CE) / 25 (PE)
  continuation  - strong trend AND the bar breaks the high (CE) / low (PE) of the previous 3 bars: a fresh
                  breakout bar (RSI up to 85 / down to 15), up to 2 ATR beyond EMA 9, and the pullback / candle / RSI-direction triggers are all allowed,
                  but never a blow-off bar (range > 2 ATR).
"""
import os

VARIANTS = ("off", "rsi", "continuation")
TREND_ENTRY = (os.getenv("TREND_ENTRY", "off").strip().lower() or "off")
if TREND_ENTRY not in VARIANTS:
    TREND_ENTRY = "off"

RELAX = {
    "rsi": ("Momentum not overextended",),
    "continuation": ("Momentum not overextended", "Not a fresh breakout", "Not chasing", "Entry trigger", "Pullback-and-resume trigger",
                     "Closed-candle price-action confirmation", "RSI direction supports"),
}
RULES = {"rsi": {"min_eff": 0.6, "rsi_cap": 75.0}, "continuation": {"min_eff": 0.55, "rsi_cap": 85.0}}   # 6 Nov: RSI 78-87 all afternoon
MIN_MOVE_ATR = 4.0          # the last 20 closes moved at least this many ATR in the trade's direction
MAX_STRETCH_ATR = 2.0       # continuation: at most this far beyond EMA 9
MAX_BAR_ATR = 2.0           # continuation: no blow-off bar


def set_variant(v):
    global TREND_ENTRY
    v = str(v or "off").strip().lower()
    TREND_ENTRY = v if v in VARIANTS else "off"
    return TREND_ENTRY


def efficiency(vals):
    if len(vals) < 2:
        return 0.0
    path = sum(abs(vals[k] - vals[k - 1]) for k in range(1, len(vals)))
    return abs(vals[-1] - vals[0]) / path if path else 0.0


def check(cs, side, atr, rsi, vwap, stretch_atr, failed_labels, variant=None):
    """cs: closed candles up to and including the signal bar (dicts with open/high/low/close). Returns a dict describing the strong
    trend when `variant` allows an entry on this bar, else None."""
    variant = variant or TREND_ENTRY
    if variant not in RELAX or side not in ("CE", "PE") or not atr or rsi is None or len(cs) < 21 or not failed_labels:
        return None
    relax = RELAX[variant]
    if any(not str(f).startswith(relax) for f in failed_labels):
        return None                                  # something outside the relaxable pullback rules failed
    d = 1 if side == "CE" else -1
    closes = [float(c["close"]) for c in cs[-21:]]
    move = (closes[-1] - closes[0]) * d
    eff = efficiency(closes)
    r = RULES[variant]
    if move < MIN_MOVE_ATR * atr or eff < r["min_eff"]:
        return None
    if vwap is not None and (closes[-1] - float(vwap)) * d <= 0:
        return None
    if (side == "CE" and rsi > r["rsi_cap"]) or (side == "PE" and rsi < 100 - r["rsi_cap"]):
        return None
    bar = cs[-1]
    if variant == "continuation":
        prev = cs[-4:-1]
        brk = (closes[-1] > max(float(c["high"]) for c in prev)) if d == 1 else (closes[-1] < min(float(c["low"]) for c in prev))
        if not brk:
            return None
        if stretch_atr is not None and stretch_atr > MAX_STRETCH_ATR:
            return None
        if (float(bar["high"]) - float(bar["low"])) > MAX_BAR_ATR * atr:
            return None
    return {"variant": variant, "efficiency": round(eff, 2), "move_atr": round(move / atr, 1), "rsi": round(rsi, 1),
            "relaxed": [f for f in failed_labels],
            "detail": f"strong {'up' if d == 1 else 'down'}trend: {move / atr:.1f} ATR in 20 bars, efficiency {eff:.2f}, "
                      f"{'above' if d == 1 else 'below'} VWAP, RSI {rsi:.0f}"
                      + (" · broke the last 3 bars' " + ("high" if d == 1 else "low") if variant == "continuation" else "")}
