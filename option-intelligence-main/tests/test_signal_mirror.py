"""Signal log mirror/restore logic (v1.18.7). Run: python tests/test_signal_mirror.py
main.py needs FastAPI, so this checks the pieces it relies on: monthly JSON blobs in accounts.app_meta + INSERT OR IGNORE restore,
and that main.py wires them in."""
import importlib, json, os, re, sqlite3, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_meta_roundtrip_and_restore_after_wipe():
    os.environ.pop("DATABASE_URL", None); os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "a.db")
    import accounts as acc; acc = importlib.reload(acc)
    import signal_exits as sx
    path = os.path.join(tempfile.mkdtemp(), "s.sqlite3")
    def fresh():
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS signal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT, session_date TEXT, symbol TEXT, interval INTEGER, mode TEXT, candle_time TEXT, decision TEXT, final_action TEXT, side TEXT, score REAL, regime TEXT, pattern TEXT, pattern_confirmed INTEGER, last_close REAL, entry REAL, stop_loss REAL, target REAL, reason TEXT, UNIQUE(symbol, interval, mode, candle_time, final_action))")
            sx.ensure_columns(db)
    fresh()
    with sqlite3.connect(path) as db:
        for d, c in (("2026-10-29", "c1"), ("2026-11-02", "c2")):
            db.execute("INSERT INTO signal_log (generated_at,session_date,symbol,interval,mode,candle_time,final_action,side,entry,stop_loss,target,trade_status,net_pnl_points,atr_at_entry,sl_atr_mult,target_atr_mult) VALUES ('g',?,'NIFTY',5,'scalp',?,'BUY','CE',22800,22785,22845,'TARGET',41,20,0.75,3)", (d, c))
        db.commit(); db.row_factory = sqlite3.Row
        months = {}
        for r in db.execute("SELECT * FROM signal_log").fetchall():
            r = dict(r); r.pop("id"); months.setdefault(r["session_date"][:7], []).append(r)
    for mth, rows in months.items():
        acc.meta_set("signal_log:" + mth, json.dumps(rows))
    acc.meta_set("signal_log:2026-11", json.dumps(months["2026-11"]))        # upsert, not duplicate
    assert [i["key"] for i in acc.meta_list("signal_log:")] == ["signal_log:2026-10", "signal_log:2026-11"]
    os.remove(path); fresh()                                                  # "Render restarted": local file is empty
    n = 0
    for item in acc.meta_list("signal_log:"):
        with sqlite3.connect(path) as db:
            have = {r[1] for r in db.execute("PRAGMA table_info(signal_log)").fetchall()}
            for r in json.loads(item["value"]):
                cols = [c for c in r if c in have]
                n += db.execute(f"INSERT OR IGNORE INTO signal_log ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols]).rowcount
    assert n == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*), SUM(net_pnl_points), MAX(sl_atr_mult) FROM signal_log").fetchone() == (2, 82, 0.75)


def test_main_wires_it_in():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    for needle in ("mirror_signal_log", "restore_signal_log", '"/api/signals/summary"', '"/api/signals/config"', "_end_of_day_summary", "_SIGNAL_LOG_DIRTY.set()", "Monthly Summary"):
        assert needle in src, needle


if __name__ == "__main__":
    test_meta_roundtrip_and_restore_after_wipe(); test_main_wires_it_in(); print("signal mirror tests passed")
