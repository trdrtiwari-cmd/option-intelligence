import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import decision_flow as dfl  # noqa: E402

RETEST = {"side": "CE", "stage": "breakout", "name": "Bull flag breakout (retest holding)", "bars_ago": 2}
FRESH = {"side": "CE", "stage": "breakout", "name": "Bull flag breakout", "bars_ago": 0}
FORMING = {"side": "CE", "stage": "forming", "name": "Bull flag (forming)"}


def test_pattern_quality():
    assert dfl.pattern_quality(RETEST, "CE") == "retest" and dfl.pattern_quality(FRESH, "CE") == "breakout"
    assert dfl.pattern_quality(FORMING, "CE") == "forming" and dfl.pattern_quality(RETEST, "PE") == "against"
    assert dfl.pattern_quality(None, "CE") == "none"


def test_room_zones():
    assert not dfl.room_gate(0.2, "retest")["pass"]                      # < 0.25: blocked whatever the pattern
    assert not dfl.room_gate(0.4, "forming")["pass"] and dfl.room_gate(0.4, "retest")["pass"]   # 0.25-0.5: only exceptional
    assert dfl.room_gate(0.6, "none")["pass"] and dfl.room_gate(None, "none")["pass"]


def test_reentry():
    sb = {"CE": {"count": 1, "first": "2026-11-06 10:00", "last": "2026-11-06 10:00"}}
    assert dfl.reentry_gate("CE", {}, None, "2026-11-06 10:00", 5)["pass"]                     # first signal
    assert dfl.reentry_gate("CE", sb, None, "2026-11-06 10:00", 5)["pass"]                     # the BUY's own bar
    assert not dfl.reentry_gate("CE", sb, FORMING, "2026-11-06 11:00", 5)["pass"]              # forming: WAIT
    assert not dfl.reentry_gate("CE", sb, FRESH, "2026-11-06 11:00", 5)["pass"]                # no retest yet: WAIT
    assert dfl.reentry_gate("CE", sb, RETEST, "2026-11-06 11:00", 5)["pass"]                   # break 10:50 > 10:00, retest holding
    old = dict(RETEST, bars_ago=20)
    assert not dfl.reentry_gate("CE", sb, old, "2026-11-06 11:00", 5)["pass"]                  # break 09:20, before the last BUY
    assert dfl.reentry_gate("PE", sb, None, "2026-11-06 11:00", 5)["pass"]                     # the other side is free


def test_feasibility_and_flow():
    good = dfl.feasibility(1.6, 1.0, 0.3, 30, 28, "retest")
    bad = dfl.feasibility(0.1, 3.0, 1.4, 90, 30, "against")
    assert good["score"] >= 90 and good["label"] == "good" and bad["score"] < 30 and bad["label"] == "poor"
    gates_t = [{"label": "Score ≥ 60", "pass": True}, {"label": "Not a fresh breakout / breakdown bar (wait for the retest)", "pass": True}]
    gates_f = [{"label": "Data Check dashboard agrees (≥ BIAS, same side)", "pass": True}, dfl.room_gate(0.2, "none")]
    f = dfl.build(gates_t, gates_f, "WATCH", "CE", 72, good, "none")
    assert [s["step"] for s in f["steps"]] == ["signal", "count", "room", "pattern", "market"]
    assert f["stopped_at"] == "room" and f["final"] == "WAIT"
    ok = dfl.build(gates_t, gates_f[:1], "CONFIRMED", "CE", 72, good, "retest", buy_ok=True)
    assert ok["final"] == "BUY CE" and ok["stopped_at"] is None
    assert dfl.build(gates_t, gates_f[:1], "CONFIRMED", "CE", 72, good, "retest", buy_ok=False)["final"] == "WAIT"


def test_freshness():
    assert dfl.freshness_gate(5, True)["pass"] and not dfl.freshness_gate(45, True)["pass"] and dfl.freshness_gate(45, False) is None


def test_modes_and_advice():
    sb = {"CE": {"count": 1, "first": "2026-11-06 10:00", "last": "2026-11-06 10:00"}}
    assert dfl.room_gate(0.4, "none", "balanced")["pass"] and not dfl.room_gate(0.4, "none", "strict")["pass"]
    assert not dfl.room_gate(0.2, "retest", "balanced")["pass"]                        # below 0.25 blocks in both modes
    assert dfl.reentry_gate("CE", sb, None, "2026-11-06 11:00", 5, mode="balanced")["pass"]
    assert not dfl.reentry_gate("CE", sb, None, "2026-11-06 11:00", 5, mode="strict")["pass"]
    assert dfl.advise({"total": 122}, {"total": 212}) == "balanced" and dfl.advise({"total": 212}, {"total": 212}) == "strict"
    assert dfl.advise({}, {}) is None
    f = dfl.build([], [dfl.room_gate(0.2, "none")], "WATCH", "CE", 70, None, "none", mode="balanced", mode_note="x")
    assert [s["reached"] for s in f["steps"]] == [True, True, True, False, False] and f["steps"][0]["sub"] == "BUY CE / BUY PE candidate"
