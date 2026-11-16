"""v1.20.76: indicator test - several scoring indicators removed together (RSI band + ADX, the pattern pair, all four)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import diagnostics as dg
import test_v1_20_75 as T75


def test_score_without_several():
    votes = {"ema": [12, 1], "supertrend": [15, -1], "vwap": [10, 1], "macd": [10, 1], "rsi": [8, -1], "adx": [12, -1], "htf": [18, -1]}
    assert dg._score_without(votes, "rsi") == dg._score_without(votes, ("rsi",))
    assert dg._score_without(votes) == (38, 62, "PE")                       # 32 of 85 bullish, 53 bearish
    assert dg._score_without(votes, ("rsi", "adx")) == (49, 51, "PE")       # 32 of 65, 33 of 65
    assert dg._score_without(votes, {"rsi", "adx", "htf"}) == (68, 32, "CE")


def _with_patterns():
    cs, rep, starts = T75._fake()
    for r in rep:                                                           # a pattern vote on every bar, always on the engine's side
        d = 1 if r["side"] == "CE" else -1 if r["side"] == "PE" else 0
        r["votes"] = dict(r["votes"], pattern_break=[6, d], pattern_retest=[6, 0])
    return cs, rep, starts


def test_combined_sets_are_measured_like_single_indicators():
    cs, rep, starts = _with_patterns()
    it = dg.indicator_test(cs, rep, starts, 5, 12, 4.0, 30, 20, score_min=60)
    comb = {tuple(c["keys"]): c for c in it["combined"]}
    assert set(comb) == {("rsi", "adx"), ("pattern_break", "pattern_retest"), ("rsi", "adx", "pattern_break", "pattern_retest")}
    for c in comb.values():
        assert {"right_side_without", "right_side_change", "trades_without", "total_change", "added", "removed", "side_changes", "verdict", "weight"} <= set(c)
    assert comb[("rsi", "adx")]["weight"] == 20 and comb[("rsi", "adx", "pattern_break", "pattern_retest")]["weight"] == 32
    # removing one key through the combined path equals the single-indicator result
    one = next(d for d in it["indicators"] if d["key"] == "rsi")
    dg_sets = dg.COMBINED_SETS
    try:
        dg.COMBINED_SETS = [("x", ("rsi", "rsi_missing")), ("y", ("rsi", "adx"))]
        it2 = dg.indicator_test(cs, rep, starts, 5, 12, 4.0, 30, 20, score_min=60)
    finally:
        dg.COMBINED_SETS = dg_sets
    assert [c["label"] for c in it2["combined"]] == ["y"]                   # a set with fewer than 2 known indicators is skipped
    assert one["right_side_without"] == sum(1 for i, d in starts if dg._score_without(rep[i]["votes"], "rsi")[2] == ("CE" if d > 0 else "PE"))


def test_combined_verdicts_and_line():
    cs, rep, starts = _with_patterns()
    it = dg.indicator_test(cs, rep, starts, 5, 12, 4.0, 30, 20, score_min=60)
    for c in it["combined"]:
        same = not c["added"].get("n") and not c["removed"].get("n") and not c["side_changes"]
        if same and c["right_side_change"] >= 0:
            assert c["verdict"].startswith("no signal changes and")
        elif c["total_change"] < 0:
            assert c["verdict"].startswith(("changes the signals for the worse", "no signal changes, but"))
    lines = dg.indicator_test_lines(it)
    assert len(lines) == 4 and lines[3]["text"].startswith("Indicator test 4/4") and "TOGETHER" in lines[3]["text"]
    assert "RSI band + ADX + both pattern checks [32 of the weight]" in lines[3]["text"]
    it["combined"] = []
    assert len(dg.indicator_test_lines(it)) == 3
