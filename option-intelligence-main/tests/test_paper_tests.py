import math
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import leading  # noqa: E402
import paper_tests as pt  # noqa: E402
import signal_fusion as sf  # noqa: E402
from indicators import IST, normalize_candles  # noqa: E402

START = datetime(2026, 11, 5, 9, 15, tzinfo=IST)


def rows(n=150):
    out = []
    for k in range(n):                       # two sessions of a wave, so the MACD histogram turns several times
        day, slot = divmod(k, 75)
        t = START + timedelta(days=day, minutes=5 * slot)
        c = 25000 + 60 * math.sin(k / 6.0) + 0.5 * k
        out.append({"time": t.isoformat(), "open": c - 1, "high": c + 6, "low": c - 6, "close": c, "volume": 0})
    return out


def first_fire(cs, side):
    for i in range(41, len(cs)):
        t = cs[i]["_dt"].astimezone(IST)
        if leading.fires(cs, i, side, "macd_turn") and sf.in_entry_window(t.hour * 60 + t.minute + 5):
            return i
    return None


def test_logs_only_when_macd_turns_with_score_and_window():
    cs = normalize_candles(rows())
    i = first_fire(cs, "CE")
    assert i is not None
    sub = cs[:i + 1]
    a = {"side": "CE", "atr": 12.0, "score": 66, "gates": [{"label": "Score ≥ 60", "pass": True}, {"label": "Not chasing", "pass": False}]}
    e = pt.check_macd_loose(a, sub, 5, sf.in_entry_window)
    assert e and e["side"] == "CE" and e["bar_time"] == sub[-1]["_dt"].astimezone(IST).strftime("%H:%M")   # other failed rules ignored
    assert pt.check_macd_loose(dict(a, gates=[{"label": "Score ≥ 60", "pass": False}]), sub, 5, sf.in_entry_window) is None
    assert pt.check_macd_loose(dict(a, side="PE"), sub, 5, sf.in_entry_window) is None or not leading.fires(sub, len(sub) - 1, "PE", "macd_turn")
    assert pt.check_macd_loose(a, sub, 5, lambda m: False) is None


def test_record_score_and_mirror(tmp_path):
    db = str(tmp_path / "p.sqlite3")
    pt.init(db)
    raw = rows()
    cs = normalize_candles(raw)
    i = first_fire(cs, "CE")
    e = pt.check_macd_loose({"side": "CE", "atr": 12.0, "score": 66, "gates": [{"label": "Score ≥ 60", "pass": True}]}, cs[:i + 1], 5, sf.in_entry_window)
    assert pt.record(db, "NIFTY", 5, e) is True and pt.record(db, "NIFTY", 5, e) is False          # one row per candle
    got = pt.rows(db, "macd_loose", "NIFTY", "2026-11-01", "2026-11-30")
    res = pt.results(got, raw, 5, now=datetime(2026, 11, 7, 10, 0, tzinfo=IST))
    t = res["trades"][0]
    assert t["status"] == "closed" and t["entry"] == round(cs[i + 1]["open"], 2) and t["pts"] is not None
    assert res["all"]["n"] == 1 and res["verdict"].startswith("collecting")
    data = pt.dump(db)
    db2 = str(tmp_path / "q.sqlite3")
    pt.init(db2)
    assert pt.load(db2, data) == 1 and pt.rows(db2, "macd_loose", "NIFTY", "2026-11-01", "2026-11-30") == got
