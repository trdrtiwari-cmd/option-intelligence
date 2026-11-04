"""Reconfirmation + side-specific RSI tests. Run: python tests/test_reconfirm.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import indicators as ind
import signal_fusion as sf
import test_fusion as F
import test_scalp as S

T0 = 1_000_000.0


def upd(tr, ts, cand=True, side="PE", price=22800.0, atr=30.0, window=60, **kw):
    return tr.update("NIFTY|5|scalp|70", cand, side, price, atr, T0 + ts, window, **kw)


def test_pending_then_confirmed_once():
    tr = sf.ReconfirmTracker()
    r = upd(tr, 0)
    assert r["state"] == "PENDING" and r["event"] == "STARTED" and r["remaining"] == 60
    r = upd(tr, 30)
    assert r["state"] == "PENDING" and r["event"] is None and r["remaining"] == 30
    r = upd(tr, 61)
    assert r["state"] == "CONFIRMED" and r["event"] == "CONFIRMED", r
    assert upd(tr, 70)["event"] is None and upd(tr, 70)["state"] == "CONFIRMED"      # the event fires exactly once


def test_needs_two_looks_not_one():
    tr = sf.ReconfirmTracker()
    upd(tr, 0)
    tr.state["NIFTY|5|scalp|70"]["polls"] = 0          # a single look after a long gap must not be enough
    assert upd(tr, 500, min_polls=2)["state"] == "PENDING"


def test_adverse_move_fails_and_stays_failed_until_setup_reforms():
    tr = sf.ReconfirmTracker()
    upd(tr, 0, price=22800.0)
    r = upd(tr, 20, price=22800.0 + 0.6 * 30)           # PE: price rose 0.6 ATR against the signal (limit 0.5)
    assert r["state"] == "FAILED" and r["event"] == "FAILED" and "against" in r["reason"], r
    assert upd(tr, 100, price=22800.0)["state"] == "FAILED"          # recovering price does not revive it
    assert upd(tr, 120, cand=False)["state"] == "NONE"               # still inside the grace period
    r = upd(tr, 160, cand=False)                                     # setup gone for > 20 s -> forgotten (a failed attempt sends no LOST event)
    assert r["state"] == "NONE" and r["event"] is None and "NIFTY|5|scalp|70" not in tr.state
    r = upd(tr, 200)
    assert r["state"] == "PENDING" and r["event"] == "STARTED"       # it re-formed: a fresh attempt


def test_short_gap_does_not_reset_clock():
    tr = sf.ReconfirmTracker()
    upd(tr, 0)
    upd(tr, 25, cand=False)                                         # gate flickered for a moment
    r = upd(tr, 40)
    assert r["state"] == "PENDING" and r["elapsed"] == 40 and r["event"] is None, r


def test_lost_event_for_pending_setup():
    tr = sf.ReconfirmTracker()
    upd(tr, 0)
    upd(tr, 10, cand=False)
    assert upd(tr, 40, cand=False)["event"] == "LOST"


def test_side_flip_starts_over_and_off_switch():
    tr = sf.ReconfirmTracker()
    upd(tr, 0, side="PE")
    r = upd(tr, 30, side="CE", price=22800.0)
    assert r["state"] == "PENDING" and r["elapsed"] == 0 and r["event"] == "STARTED"
    assert upd(tr, 40, window=0)["state"] == "OFF"


def fuse_with(tr, ts, **kw):
    return sf.fuse(F.tech(), F.dash(), F.run_of(1), None, F.NOW, True, 5,
                   reconfirm={"tracker": tr, "name": "n", "window": 60, "price": F.tech()["last_close"], "ts": T0 + ts, **kw})


def test_fuse_holds_signal_pending_then_confirms():
    tr = sf.ReconfirmTracker()
    r = fuse_with(tr, 0)
    assert r["final"]["level"] == "PENDING" and r["final"]["action"].startswith("RECONFIRMING PE"), r["final"]
    gate = [g for g in r["gates"] if g["label"].startswith("Reconfirmed")][0]
    assert not gate["pass"]
    r = fuse_with(tr, 65)
    assert r["final"]["level"] == "CONFIRMED" and r["reconfirm"]["event"] == "CONFIRMED", r["final"]
    assert [g for g in r["gates"] if g["label"].startswith("Reconfirmed")][0]["pass"]


def test_fuse_without_reconfirm_unchanged_and_no_extra_gate_on_weak_setup():
    assert sf.fuse(F.tech(), F.dash(), F.run_of(1), None, F.NOW, True, 5)["final"]["level"] == "CONFIRMED"
    weak = F.tech(sig="WATCH_PE")
    r = sf.fuse(weak, F.dash(), F.run_of(1), None, F.NOW, True, 5, reconfirm={"tracker": sf.ReconfirmTracker(), "name": "w", "window": 60, "price": 22800.0, "ts": T0})
    assert not any(g["label"].startswith("Reconfirmed") for g in r["gates"]) and r["final"]["level"] == "WATCH"


def test_rsi_bands_are_side_specific():
    assert ind.rsi_band("CE") == (60.0, 68.0) and ind.rsi_band("PE") == (32.0, 40.0)
    assert ind.rsi_band("CE", True) == (58.0, 68.0) and ind.rsi_band("PE", True) == (32.0, 42.0)   # scalp relaxes only the momentum edge


def test_rsi_exhaustion_edges_v116():
    # PE RSI 30-32 and CE RSI 68-70 (within 2 points of the old edges) must now be rejected; RSI < 30 still is
    import test_scalp as S2
    for mode in ("scalp", "standard"):
        a = S2.run(S2.selloff([-20]), mode)
        g = [x for x in a["gates"] if x["label"].startswith("Momentum not overextended")][0]
        assert "RSI > 32 for PE" in g["label"], g["label"]


def test_rsi_gates_present_and_pe_direction_checked():
    a = S.run(S.selloff([-20]), "scalp")
    labels = [g["label"] for g in a["gates"]]
    assert any(l.startswith("Momentum present (RSI ≤") and "PE" in l for l in labels), labels
    assert any(l.startswith("RSI direction supports PE (falling") for l in labels), labels
    assert a["rsi_band"]["PE"][1] >= 40 and a["rsi_prev"] is not None


def test_daily_loss_limit_blocks_third_entry():
    ok = sf.fuse(F.tech(), F.dash(), F.run_of(1), None, F.NOW, True, 5, daily_losses=1, max_daily_losses=2)
    assert ok["final"]["level"] == "CONFIRMED" and any(g["label"].startswith("Daily loss limit") and g["pass"] for g in ok["gates"])
    blocked = sf.fuse(F.tech(), F.dash(), F.run_of(1), None, F.NOW, True, 5, daily_losses=2, max_daily_losses=2)
    assert blocked["final"]["level"] != "CONFIRMED" and any(g["label"].startswith("Daily loss limit") and not g["pass"] for g in blocked["gates"])
    # off switch / unknown count must never block
    assert sf.fuse(F.tech(), F.dash(), F.run_of(1), None, F.NOW, True, 5, daily_losses=5, max_daily_losses=0)["final"]["level"] == "CONFIRMED"
    assert sf.fuse(F.tech(), F.dash(), F.run_of(1), None, F.NOW, True, 5, daily_losses=None, max_daily_losses=2)["final"]["level"] == "CONFIRMED"


def test_entry_window_starts_at_0945():
    from datetime import datetime
    early = datetime(2026, 10, 21, 9, 35, tzinfo=sf.IST)
    r = sf.fuse(F.tech(), F.dash(), F.run_of(1), None, early, True, 5)
    assert not [g for g in r["gates"] if g["label"].startswith("Entry time window")][0]["pass"]
    ok = datetime(2026, 10, 21, 9, 50, tzinfo=sf.IST)
    r = sf.fuse(F.tech(), F.dash(), F.run_of(1), None, ok, True, 5)
    assert [g for g in r["gates"] if g["label"].startswith("Entry time window")][0]["pass"]


def test_fresh_break_gate_both_modes():
    """v1.19.1: a fresh breakout / breakdown bar is never an entry, in either mode (real Oct-Nov replays: 25-44 % winners)."""
    import test_regime as R
    rows = R.build([(14, 22800, 22810), (30, 22810, 23000)])
    now = ind.parse_time(rows[-1]["time"]) + __import__("datetime").timedelta(minutes=10)
    for mode in ("scalp", "standard"):
        a = ind.analyze(rows, "NIFTY", 5, now=now, mode=mode)
        g = [x for x in a["gates"] if x["label"].startswith("Not a fresh breakout / breakdown")]
        assert a["side"] == "CE" and g, (mode, a["side"], [x["label"] for x in a["gates"]])
        assert g[0]["pass"] == (a["regime_phase"] != "BREAKOUT"), (mode, a["regime_phase"], g)
        if a["regime_phase"] == "BREAKOUT":
            assert a["signal"] != "CONFIRMED_CE"


def test_breakout_quality_weak_vs_strong():
    base = [{"time": f"2026-11-04T10:{i:02d}:00+05:30", "_dt": None, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 0} for i in range(30)]
    strong = base + [{"time": "x", "_dt": None, "open": 100.5, "high": 103.2, "low": 100.4, "close": 103.0, "volume": 0}]
    weak = base + [{"time": "x", "_dt": None, "open": 100.5, "high": 104.0, "low": 100.4, "close": 101.4, "volume": 0}]   # long upper wick
    assert ind.breakout_quality(strong, 2.0, "CE")[0]
    assert not ind.breakout_quality(weak, 2.0, "CE")[0]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
    print("all reconfirm tests passed")
