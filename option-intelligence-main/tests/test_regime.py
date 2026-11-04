"""Regime detection tests. Run: python tests/test_regime.py
Scenario 1 mirrors the screenshot: flat morning -> spike to ~23,120 -> rounded top -> sharp fall.
The old rule (>= 3 EMA crosses => SIDEWAYS) mislabelled that fall as a range."""
import os, random, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import indicators as ind

IST = timezone(timedelta(hours=5, minutes=30))


def build(path, seed=7, wick=9.0):
    """path = list of (bars, start, end); returns raw candles (5m, IST) from 09:15."""
    rnd, t, price, rows = random.Random(seed), datetime(2026, 10, 21, 9, 15, tzinfo=IST), None, []
    for bars, a, b in path:
        for i in range(bars):
            tgt = a + (b - a) * (i + 1) / bars
            o = price if price is not None else a
            c = tgt + rnd.uniform(-6, 6)
            hi, lo = max(o, c) + rnd.uniform(1, wick), min(o, c) - rnd.uniform(1, wick)
            rows.append({"time": t.isoformat(), "open": o, "high": hi, "low": lo, "close": c, "volume": 0})
            price, t = c, t + timedelta(minutes=5)
    return rows


def run(rows):
    now = ind.parse_time(rows[-1]["time"]) + timedelta(minutes=10)
    return ind.analyze(rows, "NIFTY", 5, now=now)


def test_screenshot_selloff_is_downtrend():
    rows = build([(10, 22950, 22960), (10, 22960, 23110), (6, 23110, 23000), (6, 23000, 22990), (8, 22990, 22900), (3, 22900, 22950), (9, 22950, 22790)])
    a = run(rows)
    print("selloff   ->", a["regime"], a["regime_label"], a["regime_phase"], "|", a["regime_note"])
    assert a["regime"] == "BEARISH" and a["regime_label"] == "DOWNTREND" and not a["sideways"]


def test_screenshot_regression_crosses_no_longer_force_sideways():
    # 6.7 ATR range, efficiency 0.48, 3 EMA crosses, fresh low: v1.8.1 said SIDEWAYS, v1.8.2 must say DOWNTREND
    path = [(12, 22950, 22960), (7, 22960, 23110), (4, 23110, 23068), (4, 23068, 23143), (4, 23143, 23108), (4, 23108, 23064),
            (4, 23064, 23036), (3, 23036, 23109), (4, 23109, 23068), (3, 23068, 23013), (5, 23013, 22943)]
    a = run(build(path, seed=65, wick=12))
    print("screenshot->", a["regime_label"], a["regime_phase"], a["range"], "|", a["regime_note"])
    assert a["range"]["ema_crosses"] >= 3 and a["range"]["efficiency"] > 0.35
    assert a["regime"] == "BEARISH" and a["regime_label"] == "DOWNTREND" and not a["sideways"]


def test_uptrend_is_uptrend():
    rows = build([(14, 22800, 22810), (30, 22810, 23000)])
    a = run(rows)
    print("uptrend   ->", a["regime"], a["regime_label"], a["regime_phase"])
    assert a["regime"] == "BULLISH" and a["regime_label"] == "UPTREND"


def test_true_range_stays_sideways():
    rows = build([(10, 22900, 22900), (6, 22900, 22960), (6, 22960, 22890), (6, 22890, 22955), (6, 22955, 22895), (6, 22895, 22950)], seed=3)
    a = run(rows)
    print("range     ->", a["regime"], "|", a["regime_note"])
    assert a["regime"] == "SIDEWAYS" and a["sideways"]


def test_flat_after_sharp_move_stays_sideways():
    # v1.7.1 case: big drop earlier, then 25+ flat bars. Must NOT be called a downtrend any more.
    rows = build([(8, 23100, 23100), (6, 23100, 22900), (26, 22900, 22905)], seed=11)
    a = run(rows)
    print("post-drop ->", a["regime"], "|", a["regime_note"])
    assert a["regime"] == "SIDEWAYS"


def test_ema_cross_dead_band():
    # tiny wiggles around the same level must not count as crosses
    e9 = [100, 100.2, 99.9, 100.1, 99.95, 100.05] * 4
    e21 = [100.0] * len(e9)
    assert ind.count_ema_crosses(e9, e21, atr=30, n=20) == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all regime tests passed")
