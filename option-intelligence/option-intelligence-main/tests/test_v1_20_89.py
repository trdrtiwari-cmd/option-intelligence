import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
import diagnostics as dg
import option_plan as op
import paper_tests as pt
import test_v1_20_72 as T72
from indicators import IST

PROF = {"cfg": {"min_rr": 1.5, "min_target_atr": 1.2}, "sl_atr": 1.5, "min_sl": 20.0, "tgt_atr": 3.0}


def test_run_replays_the_profit_room_rule():
    raw = T72.downtrend_then_turn(40)
    rep = dg.run(raw, "NIFTY", 5, "standard", live_window=False,
                 live_rules={"decision_rules": "off", "plan": "trail", "profit": PROF})
    pr = rep.get("profit_room")
    assert pr is not None and "error" not in pr, pr
    assert {"live_pass", "live_blocked", "relaxed_trail", "relaxed_level", "verdict", "paper_verdict"} <= set(pr)
    for t in pr["trades"]:
        assert t["pass"] == (t["target_atr"] >= t["needs_atr"] - 1e-6) or t["target_atr"] < t["needs_atr"]
    if pr["n"]:
        assert any(v["text"].startswith("Profit check “Room to the target”") for v in rep["verdict"])


def test_room_needs_match_live_formula():
    # trail plan, min_rr 1.5, stop 1.5 ATR: target must be >= 2.25 ATR (min of the 3 ATR cap and max(1.5 x 1.5, 1.2))
    atr = 20.0
    sr = {"resistances": [{"price": 1000 + 0.9 * atr, "strength": "Strong"}], "supports": []}
    cfg = dict(op.DEFAULTS, min_rr=1.5, min_target_atr=1.2)
    p = op.profit_plan("CE", 1000.0, atr, sr, None, None, 1.5, 20.0, 3.0, cfg)
    assert p["checks"][0]["pass"] is False and "too close" in p["checks"][0]["detail"]


def _cs():
    t0 = datetime(2026, 11, 17, 10, 0, tzinfo=IST)
    return [{"_dt": t0 + timedelta(minutes=5 * k), "close": 1000.0} for k in range(3)]


def test_paper_room_relaxed_only_when_room_is_the_one_failure():
    a = {"side": "CE", "atr": 20.0, "score": 75}
    room_fail = {"label": "Room to the target (≥ 1.5 × risk and ≥ 1.2 ATR)", "pass": False}
    ok_chk = {"label": "Option contract priced", "pass": True}
    plan = {"ok": False, "checks": [room_fail, ok_chk], "index": {"reward_pts": 15.0}}
    e = pt.check_room_relaxed(a, plan, _cs())
    assert e and e["strategy"] == "room_relaxed" and e["side"] == "CE" and e["bar_time"] == "10:10"
    assert pt.check_room_relaxed(a, dict(plan, index={"reward_pts": 5.0}), _cs()) is None          # < 0.5 ATR room
    assert pt.check_room_relaxed(a, dict(plan, checks=[room_fail, dict(ok_chk, **{"pass": False})]), _cs()) is None
    assert pt.check_room_relaxed(a, dict(plan, ok=True), _cs()) is None
    assert "room_relaxed" in pt.MODES["all"] and "room_relaxed" not in pt.MODES["on"]


def test_hooks_in_main_and_page():
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '"profit": _profit_settings()' in m and "await _paper_room(" in m
    assert "All four: MACD turn + Stochastic + Early trend + Target to level" in (ROOT / "static" / "signals.html").read_text(encoding="utf-8")


def test_replay_vs_live_matches_board_rows():
    import main
    rep = {"trades": [{"day": "2026-11-16", "time": "10:10", "side": "CE", "pnl": -24, "exit": "stop"},
                      {"day": "2026-11-16", "time": "14:35", "side": "PE", "pnl": 26, "exit": "target"},
                      {"day": "2026-11-13", "time": "10:10", "side": "CE", "pnl": 26, "exit": "target"}]}
    board = [{"symbol": "NIFTY", "side": "CE", "stage": "WATCH", "created_at": "2026-11-16T10:16:02+05:30",
              "blocked_by": "Data Check dashboard agrees (≥ BIAS, same side)", "reason": "x"},
             {"symbol": "NIFTY", "side": "CE", "stage": "NO BUY", "created_at": "2026-11-16T10:21:00+05:30",
              "blocked_by": "Profit check: Room to the target", "reason": "y"},
             {"symbol": "NIFTY", "side": "CE", "stage": "BUY", "created_at": "2026-11-16T11:40:00+05:30", "blocked_by": "", "reason": ""}]
    out = main._replay_vs_live("2026-11-16", "NIFTY", rep, board)
    assert len(out) == 2
    assert out[0]["live_stage"] == "NO BUY" and out[0]["live_at"] == "10:21" and "Room" in out[0]["blocked_by"]
    assert out[1]["live_stage"] == "not on the board"
    assert "replay_vs_live" in (ROOT / "monitor.py").read_text(encoding="utf-8")
