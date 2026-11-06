import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import diagnostics as dg  # noqa: E402
import orb_live  # noqa: E402
from indicators import IST, normalize_candles  # noqa: E402


def day_rows(closes, start=datetime(2026, 11, 6, 9, 15, tzinfo=IST)):
    out = []
    for k, c in enumerate(closes):
        out.append({"time": (start + timedelta(minutes=5 * k)).isoformat(), "open": c, "high": c + 2, "low": c - 2, "close": c, "volume": 0})
    return out


# 6 bars of opening range around 100 (high 104, low 96), then flat, then a close above 104 at bar 9 (10:00)
CLOSES = [100, 102, 98, 101, 99, 100, 101, 100, 103, 108, 112, 101, 90]


def test_find_break_matches_diagnostics():
    cs = normalize_candles(day_rows(CLOSES))
    ob = orb_live.find_break(cs, 5, 30)
    assert ob["complete"] and ob["or_high"] == 104 and ob["or_low"] == 96
    assert ob["break"]["side"] == "CE" and ob["break"]["bar_time"] == "10:00"
    rep = [{"i": i, "atr": 5.0, "side": "CE", "signal": "WAIT", "failed": []} for i in range(len(cs))]
    ev = dg.orb_events(cs, rep, 5, 30, "plain")
    assert len(ev) == 1 and ev[0]["i"] == 9 and ev[0]["side"] == "CE"          # same bar, same side as the Diagnostics test


def test_apply_signal_and_one_trade_a_day():
    cs = normalize_candles(day_rows(CLOSES[:10]))                                # last closed bar is the break
    a, rep = orb_live.apply({"side": "PE", "signal": "WATCH_PE", "gates": []}, cs, 5, now=datetime(2026, 11, 6, 10, 6, tzinfo=IST))
    assert a["signal"] == "CONFIRMED_CE" and a["side"] == "CE" and rep["key"]["price"] == 108 and a["strategy"] == "orb30"
    cs2 = normalize_candles(day_rows(CLOSES))                                     # later bars, incl. a close below the range
    a2, _ = orb_live.apply({}, cs2, 5, now=datetime(2026, 11, 6, 10, 21, tzinfo=IST))
    assert a2["side"] == "CE"                                                     # the FIRST break stays the day's trade
    late, _ = orb_live.apply({}, cs2, 5, now=datetime(2026, 11, 6, 12, 5, tzinfo=IST))
    assert late["signal"] == "WAIT"                                               # no entries after 12:00


def test_incomplete_range_and_no_break():
    a, rep = orb_live.apply({}, normalize_candles(day_rows(CLOSES[:4])), 5, now=datetime(2026, 11, 6, 9, 36, tzinfo=IST))
    assert a["signal"] == "WAIT" and rep == {} and not a["gates"][0]["pass"]
    a, _ = orb_live.apply({}, normalize_candles(day_rows(CLOSES[:9])), 5, now=datetime(2026, 11, 6, 10, 0, tzinfo=IST))
    assert a["signal"] == "WAIT" and a["gates"][0]["pass"] and not a["gates"][1]["pass"]
