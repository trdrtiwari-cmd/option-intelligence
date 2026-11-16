"""v1.20.75: indicator test in Diagnostics (is every scoring indicator earning its place?) and two more paper-only tests."""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import diagnostics as dg
import indicators as ind
import leading
import paper_tests as pt
import signal_fusion as sf
import test_paper_tests as TP
import test_v1_20_72 as T72

IST = ind.IST
ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- indicator test
def test_score_without_matches_the_engine_and_drops_one_indicator():
    votes = {"ema": [12, 1], "supertrend": [15, -1], "vwap": [10, 1], "macd": [10, 1], "rsi": [8, 0], "adx": [12, -1],
             "breakout": [10, 0], "htf": [18, -1]}                       # 95 available: bull 32 -> 34%, bear 45 -> 47%
    assert dg._score_without(votes) == (34, 47, "PE")
    assert dg._score_without(votes, "htf") == (42, 35, "CE")            # without the higher timeframe the side flips
    assert dg._score_without({}, None) == (0, 0, None)


def test_replay_keeps_votes_that_rebuild_the_classic_score():
    raw = T72.downtrend_then_turn(30)
    cs, rep = dg.replay(raw, "NIFTY", 5, "standard", live_window=False, score_model="classic")
    assert rep and all("votes" in r for r in rep)
    for r in rep:
        bull, bear, side = dg._score_without(r["votes"])
        assert side == r["side"]
        if side:
            assert (bull if side == "CE" else bear) == r["score"]


def _fake(n=400):
    """Hand-made replay: the higher timeframe votes against every move at its start, VWAP with it, EMA and MACD always agree."""
    start = datetime(2026, 11, 2, 9, 15, tzinfo=IST)
    cs, rep, starts = [], [], []
    px = 25000.0
    for k in range(n):
        day, slot = divmod(k, 75)
        phase = (k // 25) % 2                                           # 25 bars up, 25 bars down
        d = 1 if phase == 0 else -1
        o = px
        px += d * 12.0
        cs.append({"_dt": start + timedelta(days=day, minutes=5 * slot), "open": o, "high": max(o, px) + 2, "low": min(o, px) - 2, "close": px, "volume": 0})
        first = k % 25 < 6                                              # the slow indicator is still on the old side
        votes = {"ema": [12, d], "macd": [10, d], "vwap": [10, d], "htf": [18, -d if first else d], "supertrend": [15, -d if first else d],
                 "adx": [12, 0 if first else d], "rsi": [8, 0], "breakout": [10, d if not first else 0]}
        bull, bear, side = dg._score_without(votes)
        dom = bull if side == "CE" else bear
        conf = (not first) and dom >= 60 and k % 25 == 8
        rep.append({"i": k, "signal": (f"CONFIRMED_{side}" if conf else f"WATCH_{side}" if side else "WAIT"), "side": side, "score": dom, "atr": 10.0,
                    "failed": [] if conf else (["Score ≥ 60"] if dom < 60 else ["Not chasing"]), "votes": votes})
        if k % 25 == 0 and slot < 60:
            starts.append([k, d])
    return cs, rep, starts


def test_indicator_test_finds_the_late_indicator_and_the_duplicate_pair():
    cs, rep, starts = _fake()
    it = dg.indicator_test(cs, rep, starts, 5, 12, 4.0, 30, 20, score_min=60)
    by = {d["key"]: d for d in it["indicators"]}
    assert it["moves"] == len(starts) and set(by) >= {"ema", "macd", "vwap", "htf", "supertrend", "adx"}
    assert by["htf"]["against"] == it["moves"] and by["htf"]["with"] == 0 and by["htf"]["against_pct"] == 100.0
    assert by["vwap"]["with"] == it["moves"] and by["vwap"]["verdict"].startswith("points with the move")
    assert by["htf"]["right_side_without"] >= it["right_side_now"]
    assert by["rsi"]["verdict"].startswith("rarely votes")
    assert any({p["a"], p["b"]} == {"EMA 9/21", "MACD"} and p["agree_pct"] == 100.0 for p in it["pairs"])
    assert by["ema"]["trades_without"]["n"] >= 0 and "total_change" in by["ema"]
    lines = dg.indicator_test_lines(it)
    assert len(lines) in (3, 4) and all(l["text"].startswith(f"Indicator test {k}/4") for k, l in enumerate(lines, 1))
    assert "EMA 9/21 and MACD 100%" in lines[2]["text"] and "nothing is changed automatically" in lines[2]["text"]


def test_indicator_test_score_rule_only():
    """A bar blocked only by the score passes when the score without the indicator reaches the minimum; a bar blocked by another
    rule never does; a confirmed bar is lost when its score falls under the minimum."""
    cs, rep, starts = _fake(200)
    k = 30                                                              # second leg, slow indicators still on the old side
    rep[k] = dict(rep[k], side="PE", signal="WATCH_PE", failed=["Score ≥ 60"],
                  votes={"ema": [12, -1], "macd": [10, -1], "vwap": [10, -1], "htf": [18, 1], "breakout": [10, -1], "rsi": [8, 0]})
    it = dg.indicator_test(cs, rep, starts, 5, 12, 4.0, 30, 20, score_min=60)
    by = {d["key"]: d for d in it["indicators"]}
    base_n = it["signals_now"].get("n", 0)
    assert by["htf"]["added"].get("n", 0) >= 1 and by["htf"]["trades_without"]["n"] >= base_n     # 42/50 = 84 without the higher timeframe
    rep[k] = dict(rep[k], failed=["Score ≥ 60", "Not chasing"])
    it2 = dg.indicator_test(cs, rep, starts, 5, 12, 4.0, 30, 20, score_min=60)
    assert {d["key"]: d for d in it2["indicators"]}["htf"]["added"].get("n", 0) == by["htf"]["added"]["n"] - 1


def test_run_adds_the_indicator_test_and_missed_moves_starts():
    src = read("diagnostics.py")
    assert 'rep["indicator_test"] = it' in src and '"starts": [[m["i"], m["d"]] for m in moves]' in src
    assert '"indicator_test": r.get("indicator_test")' in read("main.py")
    raw = T72.downtrend_then_turn(40)
    rep = dg.run(raw, "NIFTY", 5, "standard", live_window=False)
    assert "indicator_test" in rep and "error" not in rep["indicator_test"]
    assert "starts" in rep["missed_moves"]
    assert any(v["text"].startswith("Indicator test") for v in rep["verdict"])


# ---------------------------------------------------------------- paper tests
def test_paper_modes():
    assert pt.MODES["off"] == () and pt.MODES["on"] == ("macd_loose",) and set(pt.MODES["all"]) == set(pt.STRATEGIES)
    assert set(pt.SHORT) == set(pt.STRATEGIES)


def test_stochastic_loose_like_macd_loose():
    cs = ind.normalize_candles(TP.rows())
    i = next(i for i in range(41, len(cs)) if leading.fires(cs, i, "CE", "stoch")
             and sf.in_entry_window(cs[i]["_dt"].astimezone(IST).hour * 60 + cs[i]["_dt"].astimezone(IST).minute + 5))
    sub = cs[:i + 1]
    a = {"side": "CE", "atr": 12.0, "score": 66, "gates": [{"label": "Score ≥ 60", "pass": True}, {"label": "Not chasing", "pass": False}]}
    e = pt.check_leading_loose(a, sub, 5, sf.in_entry_window, "stoch_loose")
    assert e and e["strategy"] == "stoch_loose" and e["side"] == "CE"
    assert pt.check_leading_loose(dict(a, gates=[{"label": "Score ≥ 60", "pass": False}]), sub, 5, sf.in_entry_window, "stoch_loose") is None
    assert pt.check_leading_loose(a, sub, 5, lambda m: False, "stoch_loose") is None
    m = pt.check_macd_loose(a, sub, 5, sf.in_entry_window)             # the old entry point still means MACD
    assert m is None or m["strategy"] == "macd_loose"


def test_early_trend_paper_uses_the_live_rule_and_its_limits(monkeypatch):
    import trend_entry as te
    cs = ind.normalize_candles(TP.rows())
    sub = [c for c in cs if c["_dt"].astimezone(IST).date() == cs[0]["_dt"].astimezone(IST).date()][:20]   # last bar 10:50
    pa = {"side": "CE", "atr": 12.0, "score": 70, "signal": "WATCH_CE", "rsi": 64.0, "last_close": sub[-1]["close"], "ema9": sub[-1]["close"] - 6,
          "vwap": sub[-1]["close"] - 20, "gates": [{"label": "Score ≥ 60", "pass": True}, {"label": "Not chasing (≤ 1 ATR beyond EMA 9)", "pass": False}]}
    seen = {}

    def fake(cs_, side, atr, rsi, vwap, stretch, failed, variant):
        seen.update(side=side, variant=variant, failed=failed, stretch=stretch)
        return {"variant": "early"}
    monkeypatch.setattr(te, "check", fake)
    e = pt.check_early_trend(pa, sub, 5, sf.in_entry_window)
    assert e and e["strategy"] == "early_trend" and e["bar_time"] == "10:50"
    assert seen["variant"] == "early" and seen["failed"] == ["Not chasing (≤ 1 ATR beyond EMA 9)"] and seen["stretch"] == 0.5
    day = e["day"]
    assert pt.check_early_trend(dict(pa, signal="CONFIRMED_CE"), sub, 5, sf.in_entry_window) is None      # already a live signal
    assert pt.check_early_trend(pa, sub, 5, lambda m: False) is None                                     # outside the entry window
    assert pt.check_early_trend(pa, sub, 5, sf.in_entry_window, [{"day": day, "bar_time": "10:20"}]) is None   # within 12 candles of the last one
    assert pt.check_early_trend(pa, sub, 5, sf.in_entry_window, [{"day": day, "bar_time": "09:45"}]) is not None
    three = [{"day": day, "bar_time": t} for t in ("09:20", "09:25", "09:30")]
    assert pt.check_early_trend(pa, sub, 5, sf.in_entry_window, three) is None                            # at most 3 a day
    monkeypatch.setattr(te, "check", lambda *a, **k: None)
    assert pt.check_early_trend(pa, sub, 5, sf.in_entry_window) is None


def test_paper_wiring_and_page():
    m = read("main.py")
    assert "def paper_mode():" in m and 'payload.opt_paper_macd not in ptest.MODES' in m and '"paper_macd": paper_mode()' in m
    assert 'score_model="priority"' in m and '_PAPER_BAR[(sym, st_)] = bar' in m and 'ctx["paper_tests"]' in m
    h = read("static/signals.html")
    assert '<option value="all">' in h and '<option value="on">MACD turn (loose) - paper only</option><option value="off">Off</option>' in h
    js = read("static/signals.js")
    assert "d.tests && d.tests.length" in js and "t.test" in js
