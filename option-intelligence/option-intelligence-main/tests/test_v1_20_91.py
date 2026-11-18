import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import monitor as mon


def test_intraday_kinds_and_reuse():
    import main
    assert main._intra_kind(main.INDEX_KEYS["NIFTY"]) == "index"
    assert main._intra_kind("NSE_FO|12345") == "fo" and main._intra_kind("NSE_EQ|INE002A01018") == "stock"
    assert main.INTRA_KIND_MIN_TTL["stock"] >= 60 and main.INTRA_KIND_MIN_TTL["index"] == 0
    main._INTRA_KIND_CALLS.clear()
    main._INTRA_KIND_CALLS.setdefault("fo", main.collections.deque()).extend([time.time() - 4000, time.time() - 10])
    assert main.intraday_by_kind()["fo"]["last_30min"] == 1


def test_setup_bought_minutes_later_is_not_missed():
    rows = [{"id": 1, "symbol": "NIFTY", "side": "CE", "stage": "SETUP", "option_name": "NIFTY 25300 CE",
             "created_at": "2026-11-17T10:50:10+05:30", "best_pct_60": 43, "test_result": "TARGET", "test_points": 95,
             "blocked_by": "Reconfirmed (60s hold: setup persists, price holds)"},
            {"id": 2, "symbol": "NIFTY", "side": "CE", "stage": "BUY", "option_name": "NIFTY 25300 CE",
             "created_at": "2026-11-17T10:57:09+05:30"},
            {"id": 3, "symbol": "BANKNIFTY", "side": "CE", "stage": "WATCH", "option_name": "BANKNIFTY 61900 CE",
             "created_at": "2026-11-17T10:02:00+05:30", "best_pct_60": 43, "test_result": "TARGET", "test_points": 120,
             "blocked_by": "Same-side re-entry"},
            {"id": 4, "symbol": "NIFTY", "side": "CE", "stage": "WATCH", "option_name": "NIFTY 25350 CE",
             "created_at": "2026-11-17T11:35:00+05:30", "best_pct_60": 46, "test_result": "TARGET", "test_points": 30,
             "blocked_by": "Momentum not overextended (RSI < 72 for CE)"}]
    missed = mon.missed_from_board(rows)
    assert [m["detail"]["board_id"] for m in missed] == [4]
    gates = {g["gate"] for g in mon.board_gate_stats(rows)}
    assert gates == {"Momentum not overextended (RSI < 72 for CE)"}
