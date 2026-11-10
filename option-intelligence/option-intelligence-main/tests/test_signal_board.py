"""v1.19.5 signal board: every option signal recorded once per stage, with its 60-minute outcome."""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signal_board as sb
import signal_exits as sx
import indicators as ind

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 5, 10, 5, tzinfo=IST)
PICK = {"instrument_key": "NSE_FO|1", "name": "NIFTY 23850 CE", "strike": 23850, "type": "CE", "label": "2 strikes ITM", "ltp": 180.0, "expiry": "2026-11-10"}


def _db():
    p = os.path.join(tempfile.mkdtemp(), "b.sqlite3"); sb.init(p); return p


def test_stages_and_dedupe():
    p = _db()
    a = {"side": "CE", "last_close": 23960.0, "atr": 18.4, "score": 82, "regime_label": "UPTREND", "last_closed_bar": "2026-11-05T10:00"}
    watch = sb.make_row(NOW, "NIFTY", 5, "scalp", a, {"level": "WATCH", "side": "CE", "headline": "x"}, {}, PICK, {"lean_since_text": "10:00 IST"})
    assert watch["stage"] == "WATCH" and sb.insert(p, watch) and not sb.insert(p, dict(watch, created_at="later"))   # same episode -> one row
    buy_final = {"level": "CONFIRMED", "side": "CE", "buy": {"ok": True, "summary": "BUY NIFTY 23850 CE @ 181",
                 "index": {"entry": 23960, "stop": 23932.4, "target": 23987.6, "rr": 1.0}, "option": {"entry": 181.0, "stop": 163.0, "target": 199.0}}}
    buy = sb.make_row(NOW, "NIFTY", 5, "scalp", a, buy_final, {"since": 123.0, "state": "CONFIRMED"}, PICK, {})
    assert buy["stage"] == "BUY" and buy["opt_entry"] == 181.0 and sb.insert(p, buy)
    nob = sb.make_row(NOW, "NIFTY", 5, "scalp", dict(a, buy_plan={"summary": "No buy - room"}), {"level": "WATCH", "side": "CE", "profit_rejected": True}, {}, PICK, {})
    assert nob["stage"] == "NO BUY" and nob["reason"].startswith("No buy")
    assert sb.make_row(NOW, "NIFTY", 5, "scalp", a, {"level": "WAIT"}, {}, PICK, {}) is None
    assert sb.make_row(NOW, "NIFTY", 5, "scalp", a, {"level": "WATCH", "side": "CE"}, {}, None, {}) is None    # no option -> nothing
    rows = sb.query(p)
    assert {r["stage"] for r in rows} == {"WATCH", "BUY"}


def test_outcome_after_60_minutes():
    p = _db()
    a = {"side": "CE", "last_close": 23960.0, "atr": 18.4, "score": 82}
    row = sb.make_row(NOW, "NIFTY", 5, "scalp", a, {"level": "WATCH", "side": "CE"}, {}, PICK, {"lean_since_text": "10:00"})
    sb.insert(p, row)
    assert not sb.pending(p, NOW + timedelta(minutes=30))
    pend = sb.pending(p, NOW + timedelta(minutes=61))
    assert len(pend) == 1
    t0 = NOW.replace(second=0) + timedelta(minutes=1)
    opt = ind.normalize_candles([{"time": (t0 + timedelta(minutes=i)).isoformat(), "open": 180 + i, "high": 182 + i, "low": 179 + i, "close": 181 + i} for i in range(70)])
    idx = ind.normalize_candles([{"time": (t0 + timedelta(minutes=i)).isoformat(), "open": 23960 + i, "high": 23962 + i, "low": 23959 + i, "close": 23961 + i} for i in range(70)])
    f = sb.outcome(pend[0], opt, idx, IST, sx._first_exit)
    assert f["opt_60"] == 240.0 and f["best_pct_60"] > 30 and f["test_result"] == "TARGET" and abs(f["test_points"] - 27.6) < 0.01
    sb.save_outcome(p, pend[0]["id"], f)
    s = {x["stage"]: x for x in sb.summary(sb.query(p))}
    assert s["WATCH"]["count"] == 1 and s["WATCH"]["pct_up20"] == 100.0 and s["WATCH"]["test_win_rate"] == 100.0
