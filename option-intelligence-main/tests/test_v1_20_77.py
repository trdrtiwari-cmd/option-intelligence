"""v1.20.77: the replay reads the room to the next level on the signal bar itself, like the live app - so a trade's room (and with
it which trades the decision rules keep) no longer depends on where the 30-day window starts."""
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import decision_flow as dfl
import diagnostics as dg
import indicators as ind

IST = ind.IST


def walk(days=9, seed=5):
    rnd, price, rows = random.Random(seed), 25000.0, []
    for d in range(days):
        t = datetime(2026, 10, 5, 9, 15, tzinfo=IST) + timedelta(days=d)
        for k in range(75):
            o = price
            price += rnd.gauss(0, 9)
            rows.append({"time": (t + timedelta(minutes=5 * k)).isoformat(), "open": o, "high": max(o, price) + rnd.uniform(0, 5),
                         "low": min(o, price) - rnd.uniform(0, 5), "close": price, "volume": 0})
    return rows


def _rows(cs, idx):
    return [{"i": i, "side": "CE" if n % 2 else "PE", "atr": 14.0, "day": cs[i]["_dt"].astimezone(IST).strftime("%Y-%m-%d")} for n, i in enumerate(idx)]


def test_replay_room_equals_the_live_room_on_the_same_bar():
    cs = ind.normalize_candles(walk())
    idx = [460, 461, 463, 465, 500, 517, 610, 640]                     # several inside one old 6-bar snapshot
    rows = dg.sr_features(cs, _rows(cs, idx), 5, "NIFTY")
    for r in rows:
        live = dfl.sr_room(cs[:r["i"] + 1], r["atr"], 5, "NIFTY", r["side"], cs[r["i"]]["close"])
        assert r["sr_room"] == live["room_atr"] and r["sr_dist"] == live["own_atr"]


def test_room_does_not_depend_on_where_the_data_starts():
    full = ind.normalize_candles(walk())
    cut = full[75:]                                                     # the same candles with the first day dropped (the window moved a day)
    idx = [460, 461, 463, 465, 500, 517, 610, 640]
    a = dg.sr_features(full, _rows(full, idx), 5, "NIFTY")
    b = dg.sr_features(cut, _rows(cut, [i - 75 for i in idx]), 5, "NIFTY")
    assert [(r["sr_room"], r["sr_dist"], r["sr_strength"]) for r in a] == [(r["sr_room"], r["sr_dist"], r["sr_strength"]) for r in b]


def test_nth_signal_of_the_day_unchanged():
    cs = ind.normalize_candles(walk())
    rows = dg.sr_features(cs, [{"i": 460, "side": "CE", "atr": 14.0, "day": "d"}, {"i": 470, "side": "CE", "atr": 14.0, "day": "d"},
                               {"i": 480, "side": "PE", "atr": 14.0, "day": "d"}, {"i": 490, "side": "CE", "atr": None, "day": "d"}], 5, "NIFTY")
    assert [r["nth_today"] for r in rows] == [1, 2, 1, 3] and rows[3]["sr_room"] is None
