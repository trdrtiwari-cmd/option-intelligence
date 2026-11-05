"""v1.20.16 chart patterns on closed candles."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chart_patterns as cp


def candles(path, wick=1.0):
    out, prev = [], path[0]
    for p in path:
        o, c = prev, p
        out.append({"open": o, "close": c, "high": max(o, c) + wick, "low": min(o, c) - wick})
        prev = p
    return out


def test_bull_flag_breakout_and_forming():
    base = [100.0] * 40
    imp = [100 + 4 * k for k in range(1, 11)]                 # +40 in 10 bars (ATR ~5 -> 8 ATR)
    flag = [139, 137, 136, 134, 133, 132]                     # shallow drift down
    cs = candles(base + imp + flag + [142])
    p = cp.detect(cs, 5.0)
    assert p["name"] == "Bull flag breakout" and p["side"] == "CE" and p["stage"] == "breakout", p
    assert cp.supports(p, "CE") and cp.against(p, "PE") and not cp.supports(p, "PE")
    p2 = cp.detect(candles(base + imp + flag + [131.5]), 5.0)
    assert p2["name"] == "Bull flag (forming)" and p2["stage"] == "forming" and not cp.supports(p2, "CE")


def test_range_and_breakdown():
    rng = [100 + (2 if k % 2 else -2) for k in range(30)]
    cs = candles([100.0] * 15 + rng + [100])
    p = cp.detect(cs, 4.0)
    assert p["stage"] in ("range", "forming") and not cp.supports(p, "CE")
    cs2 = candles([100.0] * 15 + rng + [90])
    p2 = cp.detect(cs2, 4.0)
    assert p2["name"] == "Range breakdown" and p2["side"] == "PE" and cp.against(p2, "CE")


def test_double_bottom_breakout():
    down = [120 - 2 * k for k in range(10)]                    # to ~102
    path = [120.0] * 25 + down + [100, 99, 100, 104, 108, 112, 113, 110, 106, 102, 100, 99.2, 100, 103, 107, 111, 115]
    p = cp.detect(candles(path, 0.5), 3.0)
    assert p["side"] == "CE" and p["stage"] == "breakout" and p["name"] in ("Double bottom breakout", "Range breakout"), p


def test_not_enough_data():
    assert cp.detect(candles([100.0] * 10), 2.0)["name"] == "None"
    assert cp.detect(candles([100.0] * 50), None)["name"] == "None"


def test_window_is_selectable_1_to_2_hours():
    assert cp.window_bars(5, 60) == 12 and cp.window_bars(5, 90) == 18 and cp.window_bars(5, 120) == 24
    assert cp.window_bars(1, 120) == 80 and cp.window_bars(3, 90) == 30 and cp.window_bars(15, 120) == 12     # min 12 candles
    assert cp._clean(100) == 105 and cp._clean("x") == 90 and cp._clean(45) == 60
    old = cp.PATTERN_MINUTES
    try:
        assert cp.set_minutes(120) == 120 and cp.window_bars(5) == 24
    finally:
        cp.set_minutes(old)
    # a double bottom whose first low is 3+ hours old is not reported with a 2-hour window on a 5-minute chart
    old_path = [120.0] * 5 + [100, 99, 100, 106, 112, 114, 112] + [113 + (k % 3) for k in range(40)] + [100.2, 101, 105, 110, 116]
    p = cp.detect(candles(old_path, 0.5), 3.0, 5, minutes=120)
    assert not p["name"].startswith("Double bottom")
