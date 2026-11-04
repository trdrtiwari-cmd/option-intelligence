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


def test_managed_exits():
    c2 = lambda o, h, l, cl: {"open": o, "high": h, "low": l, "close": cl}
    # trailing: best +30, then price gives back 20 -> stop at +10 locks the profit
    assert dg.managed_exit([c2(100, 125, 99, 124), c2(124, 130, 118, 119), c2(119, 121, 100, 105)], 100, 1, 20, activate=20, trail=20) == (10.0, "trail")
    assert dg.managed_exit([c2(100, 101, 75, 80)], 100, 1, 20, activate=20, trail=20) == (-20.0, "stop")
    # PE direction mirrors CE
    assert dg.managed_exit([c2(100, 101, 68, 70), c2(70, 90, 69, 85)], 100, -1, 20, activate=20, trail=20) == (12.0, "trail")
    # scale out: half banked at +30, rest stopped at the trailing level
    assert dg.managed_exit([c2(100, 132, 99, 130), c2(130, 135, 105, 110)], 100, 1, 20, trail=20, half_at=30) == (21.0, "trail")
    assert dg.managed_exit([c2(100, 132, 99, 130), c2(130, 131, 128, 129)], 100, 1, 20, trail=20, half_at=30) == (29.5, "time")
    # a stop on the very candle that would reach the first target counts as a loss (adverse first)
    assert dg.managed_exit([c2(100, 135, 70, 100)], 100, 1, 20, trail=20, half_at=30) == (-20.0, "stop")


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
    assert len(rep["strategies"]) == len(dg.STRATEGIES) and next(x for x in rep["cost_table"] if x["cost"] == 2)["avg"] == rep["confirmed"]["stats"]["avg"]
    assert rep["trades"] and {"day", "time", "side", "pnl", "zone"} <= set(rep["trades"][0])
    assert "Price zone vs signal side (dashboard's zone check)" in rep["groups"]
    assert rep["grid"] and rep["verdict"] and all("level" in v and "text" in v for v in rep["verdict"])
    std = dg.run(rows, mode="standard", cost=2.0)
    assert std["confirmed"]["count"] < rep["confirmed"]["count"]               # standard mode takes far fewer entries on this data


def _walk(seed, days=10):
    rnd, price, rows = random.Random(seed), 22800.0, []
    for d in range(days):
        t = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(days=d)
        for k in range(75):
            o = price; price += rnd.gauss(0, 9)
            rows.append({"time": (t + timedelta(minutes=5 * k)).isoformat(), "open": o, "high": max(o, price) + rnd.uniform(0, 5),
                         "low": min(o, price) - rnd.uniform(0, 5), "close": price, "volume": 0})
    return rows


def test_random_walk_has_no_edge():
    """A statistical test cannot be exact for one seed (about 1 in 40 noise runs looks 'significant'), so look at several seeds:
    the average edge over random entries must be ~0 and 'statistically real' may be claimed at most rarely."""
    flagged, edges = 0, []
    for seed in range(1, 7):
        rep = dg.run(_walk(seed), mode="scalp", cost=4.0)
        b, r = rep["confirmed"]["stats"], rep["random_entries"]
        if b.get("n", 0) >= 8:
            edges.append(b["avg"] - r["avg"])
        flagged += any(v["level"] == "good" and "statistically real" in v["text"] for v in rep["verdict"])
        assert any("random entries" in v["text"] for v in rep["verdict"])
    mean_edge = sum(edges) / len(edges)
    print("random walks: mean edge over random entries %+.2f pts, flagged %d of 6" % (mean_edge, flagged))
    assert abs(mean_edge) < 3.0 and flagged <= 1


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
