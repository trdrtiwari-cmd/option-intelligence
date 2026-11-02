"""Run: python tests/test_signal_exits.py   (no pytest / web framework needed)"""
import os, sys, sqlite3, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signal_exits as sx
import dashboard_data as dd

IST = timezone(timedelta(hours=5, minutes=30))
DAY = datetime(2026, 10, 29, 11, 0, tzinfo=IST)


def make_db():
    path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE signal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT, session_date TEXT, symbol TEXT, "
                   "interval INTEGER, mode TEXT, candle_time TEXT, decision TEXT, final_action TEXT, side TEXT, score REAL, regime TEXT, "
                   "pattern TEXT, pattern_confirmed INTEGER, last_close REAL, entry REAL, stop_loss REAL, target REAL, reason TEXT)")
        sx.ensure_columns(db)
    return path


def add(path, side="CE", entry=22800.0, sl=22780.0, tp=22825.0, sdate="2026-10-29", last_close=None):
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO signal_log (generated_at,session_date,symbol,interval,mode,candle_time,side,last_close,entry,stop_loss,target,target_name) "
                   "VALUES ('x',?,'NIFTY',5,'scalp','c',?,?,?,?,?,'T1')", (sdate, side, last_close or entry, entry, sl, tp))


def row(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM signal_log ORDER BY id DESC LIMIT 1").fetchone())


def test_pick_levels_reads_real_trade_levels_keys():
    lv = dd.trade_levels({"side": "CE", "level": "CONFIRMED"}, {}, atr=16.5, price=22854.55)
    entry, stop, target, name = sx.pick_levels(lv, 22854.55, "T1")
    assert entry == 22854.55 and name == "T1"
    assert stop < entry < target, (stop, entry, target)
    assert abs((target - entry) - 1.5 * 16.5) < 0.1
    _, _, t2, _ = sx.pick_levels(lv, 22854.55, "T2")
    assert t2 > target


def test_target_exit_ce_net_after_cost():
    p = make_db(); add(p)
    assert sx.settle_open_signals(p, "NIFTY", 22810, DAY) == []           # inside the range: stays open
    assert row(p)["trade_status"] == "OPEN" and row(p)["peak_points"] == 10
    closed = sx.settle_open_signals(p, "NIFTY", 22831, DAY)               # overshoots target -> filled at target
    r = row(p)
    assert closed[0]["status"] == "TARGET" and r["exit_price"] == 22825 and r["gross_pnl_points"] == 25 and r["net_pnl_points"] == 21
    assert r["exit_reason"] and r["exit_time"]


def test_stop_exit_pe_filled_at_observed_price():
    p = make_db(); add(p, side="PE", entry=22800, sl=22820, tp=22775)
    sx.settle_open_signals(p, "NIFTY", 22824, DAY)
    r = row(p)
    assert r["trade_status"] == "STOP" and r["exit_price"] == 22824 and r["gross_pnl_points"] == -24 and r["net_pnl_points"] == -28


def test_stop_wins_when_both_levels_crossed():
    p = make_db(); add(p, sl=22780, tp=22825)
    sx.settle_open_signals(p, "NIFTY", 22700, DAY)
    assert row(p)["trade_status"] == "STOP"


def test_squareoff_and_expired_and_no_double_close():
    p = make_db(); add(p)
    late = DAY.replace(hour=15, minute=21)
    sx.settle_open_signals(p, "NIFTY", 22805, late)
    r = row(p); assert r["trade_status"] == "SQUAREOFF" and r["net_pnl_points"] == 1
    assert sx.settle_open_signals(p, "NIFTY", 22900, late) == []          # already closed, untouched
    p2 = make_db(); add(p2, sdate="2026-10-28")
    sx.settle_open_signals(p2, "NIFTY", 22900, DAY)
    r = row(p2); assert r["trade_status"] == "EXPIRED" and r["net_pnl_points"] is None


def test_backfill_for_old_rows_without_levels():
    p = make_db()
    with sqlite3.connect(p) as db:   # row as the old logger saved it: no entry / stop / target
        db.execute("INSERT INTO signal_log (generated_at,session_date,symbol,interval,mode,candle_time,side,last_close) VALUES ('x','2026-10-29','NIFTY',5,'scalp','c','CE',22854.55)")
    bf = sx.backfill_points(lambda a: dd.trade_levels({"side": "CE"}, {}, atr=a, price=100.0), 16.5, "T2")
    assert bf and bf["target_points"] > 45
    sx.settle_open_signals(p, "NIFTY", 22854.55 + 60, DAY, atr_backfill=bf)
    r = row(p)
    assert r["trade_status"] == "TARGET" and r["levels_backfilled"] == 1 and r["net_pnl_points"] > 0


def test_no_price_leaves_trade_open():
    p = make_db(); add(p)
    assert sx.settle_open_signals(p, "NIFTY", None, DAY) == [] and row(p)["trade_status"] == "OPEN"


def test_custom_atr_levels_and_summary():
    sx.set_overrides({"sl_atr": 1.0, "target_atr": 2.0})
    try:
        c = sx.config(); assert c["sl_atr"] == 1.0 and c["target_atr"] == 2.0
        e, stop, tgt, slp, tpp = sx.custom_levels("CE", 22800.0, 20.0, 1.0, 2.0, 10)
        assert (e, stop, tgt, slp, tpp) == (22800.0, 22780.0, 22840.0, 20.0, 40.0)
        e, stop, tgt, *_ = sx.custom_levels("PE", 22800.0, 20.0, 0.25, 2.0, 10)
        assert stop == 22810.0 and tgt == 22760.0                       # minimum stop distance respected, PE mirrored
        assert sx.custom_levels("CE", 22800.0, None, 1, 2) is None
    finally:
        sx.set_overrides({})
    assert sx.config()["sl_atr"] is None


def test_summarize_day_and_month():
    R = lambda d, st, g, side="CE", t="10:00": {"session_date": d, "trade_status": st, "gross_pnl_points": g, "net_pnl_points": None if g is None else g - 4,
                                              "side": side, "symbol": "NIFTY", "exit_time": f"{d}T{t}:00+05:30"}
    rows = [R("2026-10-29", "TARGET", 30), R("2026-10-29", "STOP", -20, "PE", "11:00"), R("2026-10-30", "SQUAREOFF", 5),
            R("2026-11-02", "TARGET", 40), R("2026-11-02", "OPEN", None), R("2026-11-02", "EXPIRED", None)]
    d = sx.summarize(rows, "day")
    by = {p["period"]: p for p in d["periods"]}
    assert by["2026-10-29"]["net_points"] == 26 + (-24) and by["2026-10-29"]["win_rate"] == 50.0
    assert by["2026-11-02"]["open"] == 1 and by["2026-11-02"]["expired"] == 1 and by["2026-11-02"]["closed"] == 1
    m = sx.summarize(rows, "month")
    mm = {p["period"]: p for p in m["periods"]}
    assert mm["2026-10"]["net_points"] == 26 - 24 + 1 and mm["2026-11"]["net_points"] == 36
    t = m["total"]; assert t["closed"] == 4 and t["net_points"] == 39 and t["max_drawdown"] == 24 and t["profit_factor"] == round(63 / 24, 2)
    assert sx.summarize([], "day")["total"]["closed"] == 0


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
