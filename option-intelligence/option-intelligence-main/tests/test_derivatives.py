"""Derivatives filters (v1.20.57). Run: python tests/test_derivatives.py"""
import json, os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import derivatives as dv
import signal_board as sb

IST = timezone(timedelta(hours=5, minutes=30))
T0 = datetime(2026, 11, 9, 10, 0, tzinfo=IST).timestamp()


def chain(spot=55000, ce_oi=1000, pe_oi=1000, iv=14.0, ce=400.0, pe=380.0, prev=900):
    rows = []
    for k in range(-5, 6):
        st = 55000 + k * 100
        rows.append({"strike_price": st, "underlying_spot_price": spot,
                     "call_options": {"market_data": {"oi": ce_oi, "prev_oi": prev, "ltp": ce if k == 0 else 50}, "option_greeks": {"iv": iv}},
                     "put_options": {"market_data": {"oi": pe_oi, "prev_oi": prev, "ltp": pe if k == 0 else 50}, "option_greeks": {"iv": iv}}})
    return rows


def hist_with(first, later, minutes=16):
    h = dv.History()
    h.add("BANKNIFTY", T0, **{k: v for k, v in dv.chain_reading(first, 55000).items() if k != "atm_strike"})
    h.add("BANKNIFTY", T0 + minutes * 60, **{k: v for k, v in dv.chain_reading(later, 55000).items() if k != "atm_strike"})
    return h


def by(checks):
    return {c["key"][6:]: c for c in checks}


def test_put_writing_is_bullish_and_pcr_rises():
    h = hist_with(chain(), chain(pe_oi=1500, ce_oi=1050))
    c = by(dv.build_checks("BANKNIFTY", T0 + 16 * 60, h))
    assert c["atm_oi"]["ok"] and c["atm_oi"]["bull"] and not c["atm_oi"]["bear"], c["atm_oi"]
    assert c["pcr_trend"]["bull"], c["pcr_trend"]


def test_call_writing_blocks_ce_in_option_a():
    h = hist_with(chain(), chain(ce_oi=1600, pe_oi=1000))
    checks = dv.build_checks("BANKNIFTY", T0 + 16 * 60, h, fut={"state": "SHORT BUILDUP", "price_change": -20, "oi_change_pct": 0.3, "bars": 3})
    g = dv.gate_rows("CE", checks)
    assert g[0]["pass"] is False and "against" in g[0]["detail"], g[0]
    assert dv.gate_rows("PE", checks)[0]["pass"] is True


def test_missing_data_never_blocks():
    h = dv.History()
    checks = dv.build_checks("BANKNIFTY", T0, h)
    assert all(not c["ok"] for c in checks)
    assert all(g["pass"] for g in dv.gate_rows("CE", checks))
    assert dv.score_with([{"weight": 10, "ok": True, "bull": True, "bear": False}], checks) == (100, 0)


def test_iv_spike_and_straddle_range_penalise_both_sides():
    h = hist_with(chain(iv=14.0, ce=400, pe=380), chain(iv=16.0, ce=370, pe=350), minutes=31)
    c = by(dv.build_checks("BANKNIFTY", T0 + 31 * 60, h))
    assert c["vol_calm"]["ok"] and not c["vol_calm"]["bull"] and not c["vol_calm"]["bear"], c["vol_calm"]
    assert c["no_decay"]["ok"] and not c["no_decay"]["bull"], c["no_decay"]
    tech = [{"weight": 60, "ok": True, "bull": True, "bear": False}]
    bull_b, _ = dv.score_with(tech, list(c.values()))
    assert bull_b < 100          # Option B: the spike / range costs points


def test_futures_buildup_states():
    cs = [{"close": 100 + i, "oi": 1000 + 10 * i} for i in range(5)]
    assert dv.futures_buildup(cs)["state"] == "LONG BUILDUP"
    cs = [{"close": 100 + i, "oi": 1000 - 10 * i} for i in range(5)]
    assert dv.futures_buildup(cs)["state"] == "SHORT COVERING"
    cs = [{"close": 100 - i, "oi": 1000 + 10 * i} for i in range(5)]
    assert dv.futures_buildup(cs)["state"] == "SHORT BUILDUP"


def test_heavyweights_and_vix():
    h = dv.History()
    c = by(dv.build_checks("BANKNIFTY", T0, h, heavy={"HDFCBANK": (1700, 1690), "ICICIBANK": (1300, 1295)}, vix={"last": 15.0, "prev_close": 13.8}))
    assert c["heavy"]["bull"] and not c["heavy"]["bear"]
    assert c["vol_calm"]["ok"] and not c["vol_calm"]["bull"]           # VIX +8.7 % = spike


def test_risk_gates():
    now = datetime(2026, 12, 4, 10, 15, tzinfo=IST)
    g = dv.risk_gates("CE", now, ["2026-12-04"], 2, 2)
    assert [x["pass"] for x in g] == [False, False]
    g = dv.risk_gates("CE", datetime(2026, 12, 4, 12, 0, tzinfo=IST), ["2026-12-04"], 2, 1)
    assert all(x["pass"] for x in g)
    assert dv.risk_gates(None, now, ["2026-12-04"], 2, 5) == []


def test_modes_and_event_days():
    assert dv.clean_mode("SCORE") == "score" and dv.clean_mode("bad") == dv.DEFAULT_MODE
    assert dv.parse_event_days("2026-12-04, x, 2027-02-01") == ["2026-12-04", "2027-02-01"]
    assert dv.parse_event_days("none") == []


def test_board_records_and_study_per_option():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "b.sqlite3")
    sb.init(db)
    h = hist_with(chain(), chain(pe_oi=1500))
    checks = dv.build_checks("BANKNIFTY", T0 + 16 * 60, h)
    tech = [{"weight": 50, "ok": True, "bull": True, "bear": False}]
    summ = dv.summary("record", "CE", tech, checks, dv.gate_rows("CE", checks), [], 100, 0)
    f = sb.deriv_fields(summ, "CE")
    assert f["deriv_a"] == "PASS" and json.loads(f["deriv_checks"])["atm_oi"] == "agree"
    for i, (res, pts, a_) in enumerate([("TARGET", 30, "PASS"), ("STOP", -20, "BLOCK"), ("TARGET", 25, "PASS"), ("STOP", -20, "BLOCK")]):
        row = {"created_at": (datetime(2026, 11, 9, 10, 0, tzinfo=IST) + timedelta(minutes=20 * i)).isoformat(), "session_date": "2026-11-09", "symbol": "BANKNIFTY", "interval": 5, "mode": "standard",
               "side": "CE", "stage": "WATCH", "episode": str(i), "option_key": "k", "test_result": res, "test_points": pts,
               **dict(f, deriv_a=a_, deriv_b_score=70 if a_ == "PASS" else 50)}
        sb.insert(db, row)
    st = sb.deriv_study(sb.query(db), 60)
    assert st["options"][0]["tested"] == 4 and st["options"][0]["win_rate"] == 50.0
    assert st["options"][1]["win_rate"] == 100.0 and st["options"][2]["win_rate"] == 0.0     # Option A pass / block
    assert st["options"][3]["win_rate"] == 100.0 and st["options"][4]["win_rate"] == 0.0     # Option B >= 60 / below
    assert any(c["check"] == "atm_oi" for c in st["checks"]) and st["verdict"]


def test_analyze_accepts_extra_checks():
    sys.path.insert(0, os.path.dirname(__file__))
    import indicators as ind
    import test_regime as T
    rows = T.build([(14, 22800, 22810), (30, 22810, 23000)])
    now = ind.parse_time(rows[-1]["time"]) + timedelta(minutes=10)
    a0 = ind.analyze(rows, "NIFTY", 5, now=now)
    neutral = [{"key": "deriv_x", "label": "x", "weight": 50, "ok": True, "bull": False, "bear": False, "detail": "", "derivatives": True}]
    a1 = ind.analyze(rows, "NIFTY", 5, now=now, extra_checks=neutral)
    assert a0["status"] == "ok" and a1["bull_score"] < a0["bull_score"], (a0["bull_score"], a1["bull_score"])
    assert "incl. derivatives" in a1["score_basis"] and "incl." not in a0["score_basis"]
    assert any(c.get("derivatives") for c in a1["checks"]) and not any(c.get("derivatives") for c in a0["checks"])


def test_terminal_switch_keeps_other_settings():
    try:
        import main as M
    except ImportError:
        return
    saved = {"opt_steps": 3, "opt_lots": 2}
    store = {"signal_log_config": __import__("json").dumps(saved)}
    orig = (M.acc.meta_get, M.acc.meta_set, M._require_admin, dict(M.OPTION_OVERRIDES))
    M.acc.meta_get, M.acc.meta_set = (lambda k: store.get(k)), (lambda k, v: store.__setitem__(k, v))
    M._require_admin = lambda request: None
    try:
        _switch_checks(M, store)
    finally:
        M.acc.meta_get, M.acc.meta_set, M._require_admin = orig[:3]
        M._set_option_overrides({"opt_" + k: v for k, v in orig[3].items()})


def _switch_checks(M, store):
    import asyncio
    from fastapi import HTTPException
    run = lambda m: asyncio.run(M.derivatives_mode_set(M.DerivModeBody(mode=m), None))
    assert run("gates")["deriv_mode"] == "gates"
    got = __import__("json").loads(store["signal_log_config"])
    assert got["opt_deriv_mode"] == "gates" and got["opt_steps"] == 3 and got["opt_lots"] == 2
    assert M.deriv_mode() == "gates" and M.OPTION_OVERRIDES.get("steps") == 3
    try:
        run("bad")
        assert False, "bad mode accepted"
    except HTTPException as exc:
        assert exc.status_code == 400
    run("record")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(__file__))
    for n, fn in list(globals().items()):
        if n.startswith("test_") and callable(fn):
            fn(); print("ok", n)
    print("all derivatives tests passed")
