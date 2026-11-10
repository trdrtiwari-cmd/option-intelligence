"""Diagnostics matched to the live trade-sequence rules (v1.20.62). Run: python tests/test_live_rules.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import diagnostics as dg

T = dg.TRAIL_NAME


def row(i, day, side, pts, room=1.0):
    return {"i": i, "day": day, "hhmm": f"{9 + i // 12:02d}:{(i % 12) * 5:02d}", "side": side, "pnl": pts, "strat": {T: pts},
            "atr_grid": {"1.5/1.5": pts}, "sr_room": room, "pattern": "None", "pattern_side": None, "pattern_stage": "none"}


ROWS = [row(10, "2026-10-26", "CE", 18.8), row(12, "2026-10-26", "CE", 26), row(20, "2026-10-28", "PE", 26), row(29, "2026-10-28", "PE", -24),
        row(40, "2026-10-29", "CE", 26), row(60, "2026-10-29", "CE", -24), row(70, "2026-10-30", "CE", 26)]


def test_nothing_on_keeps_everything():
    kept, info = dg.apply_live_rules(ROWS, {"plan": "trail"})
    assert len(kept) == 7 and info["removed_trades"] == []


def test_one_per_side_matches_the_signal_of_the_day_table():
    kept, info = dg.apply_live_rules(ROWS, {"plan": "trail", "one_per_side": True})
    assert [r["i"] for r in kept] == [10, 20, 40, 70]
    assert {t["why"] for t in info["removed_trades"]} == {"one trade per side per day"}
    assert info["kept_plan"]["total"] == round(18.8 + 26 + 26 + 26, 1) and "One trade per side per day" in info["applied"]


def test_max_buys_and_daily_losses():
    rows = [row(1, "d", "CE", -10), row(2, "d", "PE", -10), row(3, "d", "CE", 30)]
    kept, _ = dg.apply_live_rules(rows, {"plan": "trail", "max_daily_losses": 2})
    assert [r["i"] for r in kept] == [1, 2]
    kept, _ = dg.apply_live_rules(rows, {"plan": "trail", "max_buys_day": 1})
    assert [r["i"] for r in kept] == [1]


def test_decision_rules_room_gate_and_warnings():
    rows = [row(1, "d", "CE", 10, room=0.1), row(2, "d", "CE", 10, room=1.0)]
    kept, info = dg.apply_live_rules(rows, {"plan": "trail", "decision_rules": "balanced"})
    assert [r["i"] for r in kept] == [2] and "room" in info["removed_trades"][0]["why"]
    _, info = dg.apply_live_rules(rows, {"plan": "trail", "strategy": "orb30", "symbol_enabled": False})
    assert len(info["warnings"]) == 2


if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("test_") and callable(fn):
            fn(); print("ok", n)
    print("all live-rules tests passed")
