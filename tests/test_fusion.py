"""Signal fusion + signal-time tests. Run: python tests/test_fusion.py"""
import os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import signal_fusion as sf
import test_regime as T

IST = sf.IST
NOW = datetime(2026, 10, 21, 11, 0, tzinfo=IST)


def tech(sig="CONFIRMED_PE", side="PE", last=22800.0, atr=30.0):
    return {"status": "ok", "signal": sig, "side": side, "bull_score": 20, "bear_score": 82, "last_close": last, "atr": atr,
            "regime": "BEARISH", "regime_label": "DOWNTREND", "gates": []}


def dash(side="PE", level="BIAS", score=-55.0, avail=5):
    return {"action": f"{side} BIAS", "level": level, "side": side, "score": score, "agree": 4, "oppose": 0, "available": avail,
            "headline": "x", "cautions": [], "contributions": []}


def run_of(bars_ago, price=22820.0):
    """Replay result whose current signal began `bars_ago` 5-minute bars before NOW (bar close = start + 5m)."""
    close = NOW - timedelta(minutes=5 * bars_ago)
    bar = {"index": 0, "bar_open": "x", "generated_at": close.isoformat(), "text": close.strftime("%H:%M") + " IST", "price": price}
    return {"key": bar, "lean": bar, "bars": bars_ago, "capped": False}


def go(a=None, d=None, replay=None, track=None, now=NOW, open_=True):
    return sf.fuse(a or tech(), d if d is not None else dash(), replay if replay is not None else run_of(1), track, now, open_, 5)


def test_fresh_confirmed_when_both_agree():
    r = go()
    assert r["final"]["level"] == "CONFIRMED" and r["timing"]["state"] == "FRESH", r["final"]
    assert r["timing"]["generated_at_text"] == "10:55 IST" and r["timing"]["age_minutes"] == 5.0


def test_old_signal_is_stale_not_confirmed():
    r = go(replay=run_of(5))                     # 25 min old on a 5m chart (> 15)
    assert r["final"]["level"] == "STALE" and r["timing"]["state"] == "LATE", r["final"]
    assert any(not g["pass"] and g["label"].startswith("Signal fresh") for g in r["gates"])


def test_hour_old_signal_expired():
    r = go(replay=run_of(14))
    assert r["timing"]["state"] == "EXPIRED" and r["final"]["level"] == "STALE"


def test_chasing_blocked_even_if_young():
    r = go(a=tech(last=22740.0), replay=run_of(1, price=22820.0))      # PE: price fell 80 pts = 2.7 ATR since the signal
    assert r["timing"]["moved_atr"] > 1 and r["final"]["level"] == "STALE", r["timing"]


def test_dashboard_opposite_is_conflict():
    r = go(d=dash(side="CE", level="BIAS", score=48))
    assert r["final"]["level"] == "CONFLICT" and not r["gates"][0]["pass"]


def test_dashboard_unavailable_caps_at_watch():
    r = go(d={"action": "NO SIGNAL", "level": "INSUFFICIENT DATA", "side": None, "score": None, "available": 2, "contributions": []})
    assert r["final"]["level"] == "WATCH" and not r["gates"][0]["pass"]


def test_dashboard_weak_is_not_enough():
    r = go(d=dash(level="WATCH", score=-30))
    assert r["final"]["level"] == "WATCH"


def test_outside_entry_window_blocks():
    early = datetime(2026, 10, 21, 9, 20, tzinfo=IST)
    r = go(replay=run_of(0), now=early)
    assert r["final"]["level"] != "CONFIRMED" and any("Entry time window" in g["label"] and not g["pass"] for g in r["gates"])
    late = datetime(2026, 10, 21, 15, 10, tzinfo=IST)
    assert go(now=late, replay=run_of(0))["final"]["level"] != "CONFIRMED"


def test_market_closed_blocks():
    assert go(open_=False)["final"]["level"] != "CONFIRMED"


def test_dashboard_alignment_started_later_resets_clock():
    since = (NOW - timedelta(minutes=2)).timestamp()
    r = go(replay=run_of(6), track={"since": since, "exact": True, "price": 22810.0, "key": "PE:ALIGNED"})
    assert r["timing"]["basis"].startswith("dashboard") and r["timing"]["age_minutes"] == 2.0 and r["final"]["level"] == "CONFIRMED"


def test_inexact_dashboard_start_uses_older_technical_time():
    since = (NOW - timedelta(minutes=1)).timestamp()      # just started tracking (server restart) - NOT proof the signal is new
    r = go(replay=run_of(6), track={"since": since, "exact": False, "price": 22810.0, "key": "PE:ALIGNED"})
    assert r["timing"]["estimated"] and r["final"]["level"] == "STALE", r["timing"]


def test_dashboard_alone_never_creates_entry():
    r = go(a=tech(sig="WAIT", side=None), d=dash(level="CONFIRMED", score=-80))
    assert r["final"]["level"] == "WAIT" and r["gates"] == []


def test_unknown_age_refuses():
    r = go(replay={"key": None, "lean": None, "bars": 0, "capped": False})
    assert r["timing"]["state"] == "UNKNOWN" and r["final"]["level"] == "STALE"


def test_replay_finds_start_of_current_signal(monkeypatch=None):
    rows = T.build([(60, 22900, 22900)], seed=2)
    N0 = 52   # pretend the PE signal first appeared when 53 candles existed

    def fake(cs, symbol="NIFTY", interval=5, **kw):
        n = len(cs)
        return {"status": "ok", "signal": "CONFIRMED_PE" if n >= N0 + 1 else "WAIT", "side": "PE"}
    real = sf.analyze
    sf.analyze = fake
    try:
        now = sf.normalize_candles(rows)[-1]["_dt"] + timedelta(minutes=10)
        cur = {"signal": "CONFIRMED_PE", "side": "PE", "symbol": "NIFTY"}
        rp = sf.replay_signal_start(rows, 5, cur, now=now)
    finally:
        sf.analyze = real
    assert rp["key"]["index"] == N0 and rp["bars"] == 59 - N0, rp


def test_tracker_marks_restart_inexact_then_exact_on_change():
    t = sf.SignalTracker()
    e1 = t.update("NIFTY", "PE:ALIGNED", 22800, 1000.0)
    assert not e1["exact"] and e1["since"] == 1000.0
    e2 = t.update("NIFTY", "PE:ALIGNED", 22790, 1010.0)
    assert e2["since"] == 1000.0
    e3 = t.update("NIFTY", "PE:WEAK", 22785, 1020.0)
    assert e3["exact"] and e3["since"] == 1020.0


def test_dashboard_times_stamped_and_change_resets():
    tr = sf.SignalTracker()
    mk = lambda action, st: {"action": action, "level": "BIAS", "contributions": [{"key": "zone", "state": st, "label": "x"}]}
    blocks = {"zone": {"price": 22800.0}}
    f1 = sf.stamp_dashboard_times(tr, "NIFTY", blocks, mk("PE BIAS", "BEARISH"), 1000.0)
    assert f1["timing"]["since_text"].endswith("IST") and not f1["timing"]["exact"] and f1["contributions"][0]["timing"]["price_at_change"] == 22800.0
    f2 = sf.stamp_dashboard_times(tr, "NIFTY", blocks, mk("PE BIAS", "BEARISH"), 1300.0)
    assert f2["timing"]["since"] == f1["timing"]["since"] and f2["timing"]["age_minutes"] == 5.0
    f3 = sf.stamp_dashboard_times(tr, "NIFTY", blocks, mk("WAIT", "NEUTRAL"), 1400.0)
    assert f3["timing"]["exact"] and f3["timing"]["age_minutes"] == 0.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all fusion tests passed")


def test_default_entry_cutoff_is_noon():
    assert sf.ENTRY_CUTOFF_MIN == 12 * 60


def test_score_below_80_cannot_be_finally_confirmed():
    a = tech()
    a["bear_score"] = 78
    r = go(a=a)
    assert r["final"]["level"] == "WATCH"
    assert any(g["label"] == "Final-signal score ≥ 80" and not g["pass"] for g in r["gates"])


def test_score_80_can_pass_final_score_gate():
    a = tech()
    a["bear_score"] = 82
    r = go(a=a)
    assert any(g["label"] == "Final-signal score ≥ 80" and g["pass"] for g in r["gates"])
