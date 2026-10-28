"""Scalp-mode tests. Run: python tests/test_scalp.py
Scenario mirrors the 2026-10-23 screenshot: a morning range, then a steady ~220 pt fall in 5-minute candles
(ATR ~40, RSI in the 20s, price 3 ATR below EMA 9). Standard mode must keep its strict behaviour; scalp mode must
catch the continuation entries but must NOT chase a straight run and must stay out of a range."""
import os, sys
from datetime import timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import indicators as ind
import test_regime as T


def candle(t, o, c, hi_extra=4.0, lo_extra=4.0):
    return {"time": t.isoformat(), "open": o, "high": max(o, c) + hi_extra, "low": min(o, c) - lo_extra, "close": c, "volume": 0}


def selloff(ending, bounce=22):
    """Flat morning, then a staircase fall: two red bars of -20 then a bounce, eight times, then `ending` = list of further
    point moves, one candle each. bounce=22 keeps RSI in the mid 30s; bounce=14 gives the exhausted RSI-in-the-20s case."""
    rows = T.build([(34, 22705, 22715), (6, 22715, 22700)], seed=3, wick=7.0)
    t = ind.parse_time(rows[-1]["time"]) + timedelta(minutes=5)
    price = rows[-1]["close"]
    for d in [-20, -20, bounce] * 8 + list(ending):
        rows.append(candle(t, price, price + d)); price, t = price + d, t + timedelta(minutes=5)
    return rows


def run(rows, mode):
    now = ind.parse_time(rows[-1]["time"]) + timedelta(minutes=10)
    return ind.analyze(rows, "NIFTY", 5, now=now, mode=mode)


def gate(a, text):
    return next(g for g in a["gates"] if text in g["label"])


def test_standard_mode_unchanged_blocks_vertical_fall():
    a = run(selloff([-20, -20, -20, -20], bounce=14), "standard")
    assert a["mode"] == "standard" and a["regime_label"] == "DOWNTREND", a["regime_note"]
    assert a["signal"] != "CONFIRMED_PE"
    assert not gate(a, "Not chasing")["pass"] and not gate(a, "Pullback-and-resume")["pass"]


def test_exhausted_rsi_entries_stay_blocked_in_both_modes():
    # diagnostics on 14 real days: entries with RSI already < 30 lost (-14.7 pts/trade in the 14 setups only that gate blocked)
    for mode in ("scalp", "standard"):
        a = run(selloff([-20], bounce=14), mode)
        g = gate(a, "Momentum not overextended")
        assert a["rsi"] < 30 and not g["pass"] and a["signal"] != "CONFIRMED_PE", (mode, a["rsi"], g)


def test_scalp_straight_run_is_not_chased():
    a = run(selloff([-20, -20, -20, -20], bounce=14), "scalp")     # four red bars after the last bounce: no pause -> no entry even in scalp mode
    trig = gate(a, "Entry trigger")
    assert not trig["pass"] and "straight run" in trig["detail"], trig
    assert a["signal"] != "CONFIRMED_PE"


def test_scalp_catches_pause_break_continuation():
    # the last bounce candle is followed by a red candle that closes below the bounce candle's low
    a = run(selloff([-20]), "scalp")
    std = run(selloff([-20]), "standard")
    print("scalp  :", a["signal"], "|", [(g["label"][:34], g["pass"]) for g in a["gates"] if not g["pass"]])
    print("standard:", std["signal"], "|", [(g["label"][:34], g["pass"]) for g in std["gates"] if not g["pass"]])
    assert gate(a, "Entry trigger")["pass"], gate(a, "Entry trigger")
    assert a["signal"] == "CONFIRMED_PE", a["gates"]
    # (real-data note: the pause-break trigger fired on only 3 of 117 signals in 14 days; EMA 9 retests do most of the work)


def test_trigger_lapses_after_three_bars():
    assert gate(run(selloff([-20, -20, -20], bounce=14), "scalp"), "Entry trigger")["pass"]      # still within 3 bars of the pause
    assert not gate(run(selloff([-20] * 4, bounce=14), "scalp"), "Entry trigger")["pass"]        # 4 bars on: lapsed


def test_scalp_does_not_trade_a_range():
    rows = T.build([(12, 22950, 22960), (12, 22960, 22900), (12, 22900, 22955), (10, 22955, 22905), (8, 22905, 22930)], seed=11, wick=9.0)
    a = run(rows, "scalp")
    print("range  :", a["regime_label"], a["signal"])
    assert a["signal"] not in ("CONFIRMED_PE", "CONFIRMED_CE")


def test_rules_exposed_and_fusion_uses_them():
    import signal_fusion as sf
    a = run(selloff([-20]), "scalp")
    assert a["mode_rules"]["room_atr"] == 0.5 and a["mode_rules"]["fresh_bars"] == 2
    # a 4-bar-old scalp signal is LATE (fresh window is 2 bars = 10 min) but would still be FRESH in standard mode
    now = sf.datetime(2026, 10, 23, 11, 0, tzinfo=sf.IST)
    bar = {"index": 0, "bar_open": "x", "generated_at": (now - timedelta(minutes=20)).isoformat(), "text": "10:40 IST", "price": 22500.0}
    replay = {"key": bar, "lean": bar, "bars": 4, "capped": False}
    tech = {"status": "ok", "signal": "CONFIRMED_PE", "side": "PE", "bull_score": 10, "bear_score": 90, "last_close": 22495.0, "atr": 40.0, "regime": "BEARISH", "gates": []}
    dash = {"action": "PE BIAS", "level": "BIAS", "side": "PE", "score": -55.0, "agree": 4, "oppose": 0, "available": 5, "headline": "x", "cautions": [], "contributions": []}
    assert sf.fuse(tech, dash, replay, None, now, True, 5, mode="scalp")["timing"]["state"] == "LATE"
    assert sf.fuse(tech, dash, replay, None, now, True, 5, mode="standard")["timing"]["state"] == "LATE"   # 20 min > 15 as well
    bar["generated_at"] = (now - timedelta(minutes=12)).isoformat()
    assert sf.fuse(tech, dash, replay, None, now, True, 5, mode="scalp")["timing"]["state"] == "LATE"
    assert sf.fuse(tech, dash, replay, None, now, True, 5, mode="standard")["timing"]["state"] == "FRESH"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
    print("all scalp tests passed")


def test_score_threshold_is_configurable_without_changing_other_gates():
    rows = selloff([-20])
    now = ind.parse_time(rows[-1]["time"]) + timedelta(minutes=10)
    strict = ind.analyze(rows, "NIFTY", 5, now=now, mode="scalp", score_min=70)
    relaxed = ind.analyze(rows, "NIFTY", 5, now=now, mode="scalp", score_min=60)
    assert next(g for g in strict["gates"] if g["label"].startswith("Score"))["label"] == "Score ≥ 70"
    assert next(g for g in relaxed["gates"] if g["label"].startswith("Score"))["label"] == "Score ≥ 60"
    assert len(strict["gates"]) == len(relaxed["gates"])
