"""v1.19: option pick, profit check and exits from the 1-minute path. Run: python tests/test_option_plan.py"""
import os, sys, sqlite3, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import option_plan as op
import signal_exits as sx
import indicators as ind

IST = timezone(timedelta(hours=5, minutes=30))


def chain(spot=23960, step=50, n=10):
    rows = []
    atm = round(spot / step) * step
    for k in range(-n, n + 1):
        strike = atm + k * step
        ce = max(5.0, 120 - k * 30 + (spot - atm) * 0.5)
        pe = max(5.0, 120 + k * 30 - (spot - atm) * 0.5)
        rows.append({"strike_price": strike, "underlying_spot_price": spot,
                     "call_options": {"instrument_key": f"NSE_FO|C{strike}", "market_data": {"ltp": ce, "bid_price": ce - 0.5, "ask_price": ce + 0.5, "oi": 1e5},
                                      "option_greeks": {"delta": max(0.05, 0.5 - k * 0.08), "gamma": 0.0012, "theta": -9.0, "iv": 12}},
                     "put_options": {"instrument_key": f"NSE_FO|P{strike}", "market_data": {"ltp": pe, "bid_price": pe - 0.5, "ask_price": pe + 0.5, "oi": 1e5},
                                     "option_greeks": {"delta": -max(0.05, 0.5 + k * 0.08), "gamma": 0.0012, "theta": -9.0, "iv": 12}}})
    return rows


def test_default_is_two_strikes_in_the_money():
    import option_plan
    assert option_plan.DEFAULTS["moneyness"] == "ITM" and option_plan.DEFAULTS["steps"] == 2
    ce = op.pick_contract(chain(), 23960, "CE", "NIFTY", lot_size=65)
    pe = op.pick_contract(chain(), 23960, "PE", "NIFTY", lot_size=65)
    assert ce["strike"] == 23850 and pe["strike"] == 24050 and ce["label"] == "2 strikes ITM"


def test_pick_two_strikes_otm_each_side():
    ce = op.pick_contract(chain(), 23960, "CE", "NIFTY", steps=2, moneyness="OTM", expiry="2026-11-10", lot_size=65)
    pe = op.pick_contract(chain(), 23960, "PE", "NIFTY", steps=2, moneyness="OTM", expiry="2026-11-10", lot_size=65)
    assert ce["atm_strike"] == 23950 and ce["strike"] == 24050 and ce["type"] == "CE" and ce["instrument_key"] == "NSE_FO|C24050"
    assert pe["strike"] == 23850 and pe["type"] == "PE" and pe["label"] == "2 strikes OTM"
    itm = op.pick_contract(chain(), 23960, "CE", "NIFTY", steps=2, moneyness="ITM")
    assert itm["strike"] == 23850


def test_pick_falls_back_toward_atm_when_unpriced():
    rows = chain()
    for r in rows:
        if r["strike_price"] == 24050:
            r["call_options"]["market_data"]["ltp"] = 0
    p = op.pick_contract(rows, 23960, "CE", "NIFTY", steps=2, moneyness="OTM")
    assert p["strike"] == 24000 and p["steps"] == 1


def test_profit_plan_caps_target_before_resistance_and_passes():
    pick = op.pick_contract(chain(), 23960, "CE", "NIFTY", steps=2, moneyness="OTM", lot_size=65)
    sr = {"resistances": [{"price": 24000, "strength": "Strong", "basis": "PDH"}], "supports": []}
    plan = op.profit_plan("CE", 23960, 18.4, sr, pick, None, sl_atr=0.75, min_sl_pts=15, target_atr=3.0, cfg={"charges_rs": 60, "lots": 1})
    ix = plan["index"]
    assert abs(ix["target"] - (24000 - 0.15 * 18.4)) < 0.05, ix          # capped just before R1, not the full 3 ATR (55 pts)
    assert ix["stop"] == 23960 - 15 and ix["rr"] > 1.5
    assert plan["ok"], plan["checks"]
    o = plan["option"]
    assert o["entry"] == pick["ask"] and o["stop"] < o["entry"] < o["target"] and o["qty"] == 65 and o["exp_profit_rs"] > 120


def test_profit_plan_rejects_when_resistance_too_close():
    pick = op.pick_contract(chain(), 23960, "CE", "NIFTY", steps=2, lot_size=65)
    sr = {"resistances": [{"price": 23982, "strength": "Strong", "basis": "PDH"}], "supports": []}
    plan = op.profit_plan("CE", 23960, 18.4, sr, pick, None, cfg={"charges_rs": 60})
    assert not plan["ok"] and plan["summary"].startswith("No buy"), plan["summary"]
    assert not [c for c in plan["checks"] if c["label"].startswith("Room to the target")][0]["pass"]


def test_profit_plan_rejects_falling_premium_and_ignores_weak_levels():
    pick = op.pick_contract(chain(), 23960, "PE", "NIFTY", steps=2, lot_size=65)
    sr = {"supports": [{"price": 23950, "strength": "Weak"}, {"price": 23900, "strength": "Weak"}], "resistances": []}
    falling = {"status": "ok", "bull_score": 20, "bear_score": 80, "regime_label": "DOWNTREND"}
    plan = op.profit_plan("PE", 23960, 18.4, sr, pick, falling, cfg={"charges_rs": 60})
    assert plan["index"]["target_basis"] == "3 ATR"                       # weak zones are not walls
    assert not plan["ok"] and any(c["label"].startswith("Option premium not trending down") and not c["pass"] for c in plan["checks"])


def _db():
    path = os.path.join(tempfile.mkdtemp(), "t.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE signal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT, session_date TEXT, symbol TEXT, interval INTEGER, mode TEXT, "
                   "candle_time TEXT, decision TEXT, final_action TEXT, side TEXT, score REAL, regime TEXT, pattern TEXT, pattern_confirmed INTEGER, "
                   "last_close REAL, entry REAL, stop_loss REAL, target REAL, reason TEXT)")
        sx.ensure_columns(db)
    return path


def _bars(start, prices):
    out = []
    for i, (o, h, l, c) in enumerate(prices):
        out.append({"time": (start + timedelta(minutes=i)).isoformat(), "open": o, "high": h, "low": l, "close": c})
    return ind.normalize_candles(out)


def test_path_exit_fills_at_the_stop_not_at_a_later_price():
    """4 Nov 2026 case: a 15-pt stop recorded as -74 because the first price seen after a pause was used. The path fixes it."""
    p = _db()
    gen = datetime(2026, 11, 4, 11, 0, 20, tzinfo=IST)
    with sqlite3.connect(p) as db:
        db.execute("INSERT INTO signal_log (generated_at,session_date,symbol,interval,mode,candle_time,side,last_close,entry,stop_loss,target,option_entry,option_delta,qty,charges_rs) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (gen.isoformat(), "2026-11-04", "NIFTY", 5, "scalp", "c", "CE", 23900, 23900, 23885, 23955, 100.0, 0.4, 65, 60))
    bars = _bars(datetime(2026, 11, 4, 11, 0, tzinfo=IST), [(23900, 23905, 23895, 23898), (23898, 23902, 23890, 23892),
                                                           (23892, 23893, 23880, 23884), (23884, 23886, 23840, 23845), (23845, 23850, 23826, 23830)])
    closed = sx.settle_with_candles(p, "NIFTY", bars, datetime(2026, 11, 4, 11, 30, tzinfo=IST))
    assert closed and closed[0]["status"] == "STOP" and closed[0]["exit_price"] == 23885 and closed[0]["gross"] == -15
    assert closed[0]["exit_time"].startswith("2026-11-04T11:02")
    rs = sx.set_option_exit(p, closed[0]["id"], sx.estimate_option_exit(100.0, 0.4, -15), "delta estimate")
    with sqlite3.connect(p) as db:
        r = db.execute("SELECT trade_status, option_exit, option_pnl_points, net_pnl_rs, exit_basis FROM signal_log").fetchone()
    assert r[0] == "STOP" and r[1] == 94.0 and r[2] == -6.0 and r[3] == -6.0 * 65 - 60 and r[4] == "1-minute candles"
    assert rs == r[3]


def test_path_exit_gap_fills_at_open_and_target_and_squareoff():
    p = _db()
    gen = datetime(2026, 11, 4, 14, 0, 5, tzinfo=IST)
    with sqlite3.connect(p) as db:
        for side, e, sl, tp in (("PE", 23900, 23915, 23860), ("CE", 23900, 23880, 23960), ("CE", 23900, 23800, 24100)):
            db.execute("INSERT INTO signal_log (generated_at,session_date,symbol,interval,mode,candle_time,side,last_close,entry,stop_loss,target) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (gen.isoformat(), "2026-11-04", "NIFTY", 5, "scalp", f"c{side}{tp}", side, e, e, sl, tp))
    bars = _bars(datetime(2026, 11, 4, 14, 0, tzinfo=IST), [(23900, 23901, 23899, 23900), (23930, 23935, 23925, 23932)] +
                 [(23932, 23940, 23930, 23938)] * 3 + [(23938, 23965, 23937, 23962)])
    closed = {c["id"]: c for c in sx.settle_with_candles(p, "NIFTY", bars, datetime(2026, 11, 4, 14, 30, tzinfo=IST))}
    assert closed[1]["status"] == "STOP" and closed[1]["exit_price"] == 23930           # PE stop gapped through: filled at the open
    assert closed[2]["status"] == "TARGET" and closed[2]["exit_price"] == 23960
    assert 3 not in closed                                                               # still open
    late = _bars(datetime(2026, 11, 4, 15, 19, tzinfo=IST), [(23950, 23952, 23948, 23951), (23951, 23955, 23949, 23953)])
    closed = sx.settle_with_candles(p, "NIFTY", bars + late, datetime(2026, 11, 4, 15, 21, tzinfo=IST))
    assert closed and closed[0]["status"] == "SQUAREOFF" and closed[0]["exit_price"] == 23951


def test_summary_has_rupees():
    rows = [{"trade_status": "TARGET", "net_pnl_points": 20, "gross_pnl_points": 24, "net_pnl_rs": 900, "session_date": "2026-11-04", "symbol": "NIFTY", "side": "CE"},
            {"trade_status": "STOP", "net_pnl_points": -19, "gross_pnl_points": -15, "net_pnl_rs": -450, "session_date": "2026-11-04", "symbol": "NIFTY", "side": "PE"}]
    t = sx.summarize(rows)["total"]
    assert t["net_rs"] == 450 and t["rs_trades"] == 2


def test_sr_levels_finds_prior_day_high():
    rows = []
    t = datetime(2026, 11, 3, 9, 15, tzinfo=IST)
    for i in range(75):
        px = 23800 + (60 if i == 40 else 0) + (i % 5)
        rows.append({"time": (t + timedelta(minutes=5 * i)).isoformat(), "open": px, "high": px + 5, "low": px - 5, "close": px})
    t = datetime(2026, 11, 4, 9, 15, tzinfo=IST)
    for i in range(40):
        px = 23790 + i
        rows.append({"time": (t + timedelta(minutes=5 * i)).isoformat(), "open": px, "high": px + 4, "low": px - 4, "close": px + 1})
    sr = op.sr_levels(ind.normalize_candles(rows), 10.0, 5, "NIFTY")
    assert any(abs(l["price"] - 23865) < 8 for l in sr["resistances"]), sr["resistances"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)


def test_winrate_exit_plan_small_target_wide_stop():
    pick = op.pick_contract(chain(), 23960, "CE", "NIFTY", lot_size=65)
    w = op.EXIT_PLANS["winrate"]
    cfg = {"charges_rs": 60, "min_target_pts": w["min_target_pts"], "min_rr": w["min_rr"], "min_target_atr": w["min_target_atr"], "min_premium_rr": w["min_premium_rr"]}
    plan = op.profit_plan("CE", 23960, 18.4, None, pick, None, sl_atr=w["sl_atr"], min_sl_pts=w["min_sl_pts"], target_atr=w["target_atr"], cfg=cfg)
    ix = plan["index"]
    assert ix["risk_pts"] == 20 and abs(ix["reward_pts"] - 15) < 0.01, ix     # 0.6 ATR = 11 pts -> 15-pt minimum; 1 ATR = 18.4 -> 20-pt minimum
    assert plan["ok"], plan["checks"]


def test_winrate_finder_needs_both_halves():
    import diagnostics as dg
    rows = []
    for i, day in enumerate(["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]):
        for j in range(4):
            win = (j != 0)                                                    # 3 of 4 win -> 75 %
            g = {f"{t}/{s}": ((t - 4) if win else (-s - 4)) for t in dg.TARGET_GRID for s in dg.STOP_GRID}
            rows.append({"day": day, "grid": g, "atr_grid": {}})
    wf = dg.winrate_finder(rows)
    assert wf["split"] and wf["cells"] and all(c["worst_win"] >= 70 and c["worst_avg"] > 0 for c in wf["cells"])
    for r in rows:                                                          # second half collapses -> nothing passes
        if r["day"] >= "2026-10-06":
            r["grid"] = {k: -24 for k in r["grid"]}
    assert not dg.winrate_finder(rows)["cells"]
