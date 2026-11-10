import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import trend_entry as te  # noqa: E402

UP = [{"open": 100 + 3 * i, "high": 102 + 3 * i, "low": 99 + 3 * i, "close": 101 + 3 * i} for i in range(30)]
RSI = "Momentum not overextended (RSI < 68 for CE)"
FRESH = "Not a fresh breakout / breakdown bar (wait for the retest)"


def test_rsi_variant():
    assert te.check(UP, "CE", 3.0, 72, 120, 1.0, [RSI], "rsi")["efficiency"] == 1.0
    assert te.check(UP, "CE", 3.0, 76, 120, 1.0, [RSI], "rsi") is None                 # above 75
    assert te.check(UP, "CE", 3.0, 72, 120, 1.0, [RSI, FRESH], "rsi") is None          # fresh breakout not relaxed in this variant
    assert te.check(UP, "CE", 3.0, 72, 120, 1.0, [RSI, "Score ≥ 70"], "continuation") is None   # score never relaxed
    assert te.check(UP, "CE", 3.0, 72, 120, 1.0, [RSI], "off") is None


def test_continuation_variant():
    assert te.check(UP, "CE", 3.0, 84, 120, 1.5, [RSI, FRESH, "Not chasing (≤ 1 ATR beyond EMA 9)"], "continuation")
    assert te.check(UP, "CE", 3.0, 79, 120, 2.5, [RSI, FRESH], "continuation") is None     # too far beyond EMA 9
    assert te.check(UP, "CE", 3.0, 79, 999, 1.5, [RSI, FRESH], "continuation") is None     # below VWAP
    flat = UP[:-1] + [dict(UP[-1], close=UP[-2]["high"] - 1)]                                # no break of the last 3 highs
    assert te.check(flat, "CE", 3.0, 79, 120, 1.5, [RSI, FRESH], "continuation") is None
    down = [{"open": 200 - 3 * i, "high": 202 - 3 * i, "low": 199 - 3 * i, "close": 200 - 3 * i} for i in range(30)]
    assert te.check(down, "PE", 3.0, 22, 999, 1.5, ["Momentum not overextended (RSI > 32 for PE)", FRESH], "continuation")
    assert te.check(UP, "PE", 3.0, 22, 999, 1.5, ["Momentum not overextended (RSI > 32 for PE)"], "continuation") is None


def test_set_variant():
    assert te.set_variant("CONTINUATION") == "continuation" and te.set_variant("bad") == "off"
