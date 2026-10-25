"""Diagnostics tests. Run: python tests/test_diagnostics.py"""
import os, random, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import diagnostics as dg, indicators as ind
import test_scalp as S

IST = ind.IST


def c(o, h, l, cl, t=0):
    return {"open": o, "high": h, "low": l, "close": cl}


def test_first_touch_rules():
    entry = 100.0
    # PE (d=-1): price falls to 70 -> target 30 hit
    assert dg.first_touch([c(100, 101, 69, 70)], entry, -1, 30, 20) == (30.0, "target")
    # CE: both stop and target inside one candle -> stop counts first (conservative)
    assert dg.first_touch([c(100, 135, 79, 100)], entry, 1, 30, 20) == (-20.0, "stop")
    # neither hit -> time exit at the last close, signed by direction
    assert dg.first_touch([c(100, 110, 95, 108), c(108, 112, 104, 110)], entry, 1, 30, 20) == (10.0, "time")
    assert dg.first_touch([c(100, 110, 95, 108)], entry, -1, 30, 20) == (-8.0, "time")
    # stop hit on the second candle after a partial move
    assert dg.first_touch([c(100, 110, 95, 108), c(108, 109, 79, 85)], entry, 1, 30, 20) == (-20.0, "stop")
    mfe, mae = dg.excursions([c(100, 112, 96, 110), c(110, 125, 90, 95)], entry, 1)
    assert (mfe, mae) == (25.0, 10.0)


def test_stats():
    s = dg.stats([30, -20, 30, -20, 30])
    assert s["n"] == 5 and s["win_rate"] == 60.0 and s["avg"] == 10.0 and s["pf"] == 2.25 and s["total"] == 50.0
    assert dg.stats([]) == {"n": 0}


def test_run_starts():
    mk = lambda i, sig, failed=(): {"i": i, "signal": sig, "failed": list(failed), "side": "PE"}
    rp = [mk(1, "WAIT"), mk(2, "WATCH_PE", ["A"]), mk(3, "WATCH_PE", ["A"]), mk(4, "CONFIRMED_PE"), mk(5, "CONFIRMED_PE"),
          mk(6, "WATCH_PE", ["B"]), mk(7, "WATCH_PE", ["B", "C"]), mk(9, "CONFIRMED_PE")]
    conf, near = dg.events_from(rp)
    assert [r["i"] for r in conf] == [4, 9]                                   # run of 4-5 counts once; gap makes 9 a new run
    assert [(r["i"], r["gate"]) for r in near] == [(2, "A"), (6, "B")]       # only one-failed-gate setups; 7 has two


def staircase_days(n_days, seed):
    rows = []
    for d in range(n_days):
        day = S.selloff([-20])
        base = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(days=d)
        for k, r in enumerate(day[:75]):
            r = dict(r); r["time"] = (base + timedelta(minutes=5 * k)).isoformat(); r["volume"] = 0; rows.append(r)
    return rows


def test_end_to_end_on_trending_data_finds_signals():
    rows = staircase_days(4, 1)
    rep = dg.run(rows, mode="scalp", cost=2.0)
    assert rep["confirmed"]["count"] >= 4, rep["confirmed"]
    assert rep["confirmed"]["stats"]["avg"] > 0, rep["confirmed"]["stats"]     # a staircase fall pays after a PE signal
    assert rep["random_entries"]["n"] > 50
    assert rep["grid"] and rep["verdict"] and all("level" in v and "text" in v for v in rep["verdict"])
    std = dg.run(rows, mode="standard", cost=2.0)
    assert std["confirmed"]["count"] < rep["confirmed"]["count"]               # standard mode takes far fewer entries on this data


def walk(seed, days=14):
    rnd, price, rows = random.Random(seed), 22800.0, []
    for d in range(days):
        t = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(days=d)
        for k in range(75):
            o = price; price += rnd.gauss(0, 9); rows.append({"time": (t + timedelta(minutes=5 * k)).isoformat(), "open": o, "high": max(o, price) + rnd.uniform(0, 5),
                                                              "low": min(o, price) - rnd.uniform(0, 5), "close": price, "volume": 0})
    return rows


def test_wide_exit_makes_no_phantom_edge_on_noise():
    """The 60/20 exit must not look profitable on a random walk once you average over several different walks."""
    avgs = [dg.run(walk(s), mode="scalp", cost=4.0, target=60, stop=20)["confirmed"]["stats"]["avg"] for s in (5, 6, 7, 8, 9, 10)]
    print("random-walk mean of avg pts/trade at 60/20:", round(sum(avgs) / len(avgs), 2))
    assert sum(avgs) / len(avgs) < 1.0


def test_breakeven_trail_and_new_report_fields():
    entry = 100.0
    # +15 reached, then price returns to entry -> breakeven trail exits at 0 where no-trail would later hit the stop
    path = [c(100, 116, 99, 112), c(112, 113, 100, 101), c(101, 102, 70, 75)]
    assert dg.first_touch_be(path, entry, 1, 60, 20, 15) == (0.0, "stop") or dg.first_touch_be(path, entry, 1, 60, 20, 15)[0] == -0.0
    assert dg.first_touch(path, entry, 1, 60, 20) == (-20.0, "stop")
    assert dg.first_touch_be([c(100, 165, 99, 160)], entry, 1, 60, 20, 15) == (60.0, "target")
    rep = dg.run(staircase_days(6, 3), mode="scalp", cost=2.0)
    assert "trail_check" in rep and rep["trail_check"][0]["be_at"] is None and "random_at_best" in rep and "halves" in rep
    assert "With / against the session's move (open → signal)" in rep["groups"]


def test_noise_gap_is_not_a_correction():
    base = {"n": 55, "avg": 0.06, "se": 3.32}
    assert not dg._diff_sig({"n": 26, "avg": 5.62, "se": 4.82}, base)      # the real standard-mode "not chasing" gap: 0.95 SE -> noise
    assert dg._diff_sig({"n": 40, "avg": 14.0, "se": 2.0}, base)


def test_random_walk_has_no_edge():
    rows = walk(5)
    rep = dg.run(rows, mode="scalp", cost=4.0, target=30, stop=20)
    n = rep["confirmed"]["stats"].get("n", 0)
    print("random walk:", n, "signals", rep["confirmed"]["stats"])
    if n >= 8:
        assert rep["confirmed"]["stats"]["avg"] < 2.0, "a random walk must not look profitable"
    assert not any(v["level"] == "good" and "measurable edge" in v["text"] for v in rep["verdict"])
    assert not any(v["level"] == "good" and "statistically real" in v["text"] for v in rep["verdict"]), "no edge may be claimed on noise"
    assert any("random entries" in v["text"] for v in rep["verdict"])


def test_no_look_ahead():
    rows = staircase_days(3, 2)
    full_cs, full = dg.replay(rows, mode="scalp")
    cut = len(rows) - 40
    part_cs, part = dg.replay(rows[:cut], mode="scalp")
    f = {r["i"]: (r["signal"], tuple(r["failed"]), r["score"]) for r in full}
    p = {r["i"]: (r["signal"], tuple(r["failed"]), r["score"]) for r in part}
    assert p and all(f[i] == v for i, v in p.items()), "signals on past bars must not depend on later candles"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
    print("all diagnostics tests passed")
