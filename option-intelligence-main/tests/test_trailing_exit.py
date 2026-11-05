"""v1.20.9 trailing exit plan: stop 1.5 ATR; once +1.2 ATR the stop trails 0.75 ATR behind the best price."""
import os, sys, sqlite3, tempfile
from datetime import datetime, timedelta, time as dtime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signal_exits as sx

IST = timezone(timedelta(hours=5, minutes=30))


def bars(prices, start=datetime(2026, 11, 6, 10, 1, tzinfo=IST)):
    out = []
    for k, (o, h, l, c) in enumerate(prices):
        out.append({"_dt": start + timedelta(minutes=k), "open": o, "high": h, "low": l, "close": c})
    return out


def test_first_exit_trails_and_locks_profit():
    # CE at 100, ATR 20 -> stop 70, trail starts at +24 (124), trails 15 behind the best price
    path = bars([(100, 110, 99, 109), (109, 130, 108, 129), (129, 140, 128, 139), (139, 139, 120, 121)])
    st, px, bar, peak, worst = sx._first_exit(path, 1, 100.0, 70.0, 160.0, dtime(15, 20), (24.0, 15.0))
    assert st == "TRAIL" and px == 125.0 and peak == 40          # best 140 (bar 3) -> stop 125, hit in bar 4
    # without trailing the same path does nothing
    assert sx._first_exit(path, 1, 100.0, 70.0, 160.0, dtime(15, 20))[0] is None
    # PE mirror
    pe = bars([(100, 101, 90, 91), (91, 92, 70, 71), (71, 90, 68, 88)])
    st, px, *_ = sx._first_exit(pe, -1, 100.0, 130.0, 40.0, dtime(15, 20), (24.0, 15.0))
    assert st == "TRAIL" and px == 85.0          # best 70 -> stop 100 - (30 - 15) = 85
    # the trail never loosens the initial stop and uses only earlier bars (no look-ahead within a bar)
    one = bars([(100, 140, 65, 66)])
    assert sx._first_exit(one, 1, 100.0, 70.0, 200.0, dtime(15, 20), (24.0, 15.0))[:2] == ("STOP", 70.0)


def test_live_price_watcher_trails():
    d = tempfile.mkdtemp(); db = os.path.join(d, "s.sqlite3")
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE signal_log (id INTEGER PRIMARY KEY, generated_at TEXT, session_date TEXT, symbol TEXT, side TEXT, entry REAL, "
                  "last_close REAL, stop_loss REAL, target REAL, target_name TEXT)")
        sx.ensure_columns(c)
        c.execute("INSERT INTO signal_log (generated_at,session_date,symbol,side,entry,stop_loss,target,trail_start_pts,trail_pts,peak_points) "
                  "VALUES ('2026-11-06T10:00:00+05:30','2026-11-06','NIFTY','CE',100,70,160,24,15,40)")
    now = datetime(2026, 11, 6, 10, 30, tzinfo=IST)
    closed = sx.settle_open_signals(db, "NIFTY", 126.0, now)
    assert not closed                                              # 126 is above the trailed stop (125)
    closed = sx.settle_open_signals(db, "NIFTY", 124.5, now)
    assert closed and closed[0]["status"] == "TRAIL"
