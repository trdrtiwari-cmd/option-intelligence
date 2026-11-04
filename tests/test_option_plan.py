"""v1.19 tests: option strike (2 out of the money), contract lock, profit check / BUY decision, candle-accurate exits, premium P/L,
and the trend-aware RSI exhaustion edge. Run: python tests/test_option_plan.py"""
import os, sys, sqlite3
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import option_plan as op
import signal_exits as sx
import dashboard_data as dd
import test_signal_exits as T

IST = timezone(timedelta(hours=5, minutes=30))


def chain(spot=23960.6, step=50, n=9, base_ce=120.0):
    atm = round(spot / step) * step
    rows = []
    for k in range(-n, n + 1):
        strike = atm + k * step
        rows.append({"strike_price": strike, "expiry": "2026-11-10",
                     "call_options": {"instrument_key": f"NSE_FO|C{strike:.0f}", "market_data": {"ltp": max(5.0, base_ce - 25 * k), "bid_price": 100, "ask_price": 101},
                                      "option_greeks": {"delta": max(0.1, 0.5 - 0.08 * k)}},
                     "put_options": {"instrument_key": f"NSE_FO|P{strike:.0f}", "market_data": {"ltp": max(5.0, base_ce + 25 * k), "bid_price": 100, "ask_price": 101},
                                     "option_greeks": {"delta": -max(0.1, 0.5 + 0.08 * k)}}})
    return rows


def test_pick_two_strikes_out_of_the_money():
    rows = chain()
    ce = op.pick_strike(rows, 23960.6, "CE")
    pe = op.pick_strike(rows, 23960.6, "PE")
    assert ce["atm_strike"] == 23950 and ce["strike"] == 24050 and ce["offset_steps"] == 2, ce
    assert pe["strike"] == 23850 and pe["offset_steps"] == -2, pe
    assert ce["instrument_key"] == "NSE_FO|C24050" and pe["instrument_key"] == "NSE_FO|P23850"
    itm = op.pick_strike(rows, 23960.6, "CE", where="ITM")
    assert itm["strike"] == 23850
    assert op.pick_strike([], 23960, "CE") is None and op.pick_strike(rows, None, "CE") is None


def test_lock_follows_the_same_contract_until_released():
    rows, lock = chain(), op.OptionLock(release_seconds=300)
    a = lock.pick("NIFTY", rows, 23960.6, "CE", 1000)
    assert a["strike"] == 24050 and a["relocked"]
    b = lock.pick("NIFTY", rows, 23978.0, "CE", 1010)               # ATM still 23950 -> same contract, not re-picked
    assert b["strike"] == 24050 and not b["relocked"]
    c = lock.pick("NIFTY", rows, 24005.0, "CE", 1020)               # ATM moved to 24000: the locked strike is only 1 step out - still allowed
    assert c["strike"] == 24050 and not c["relocked"]
    d = lock.pick("NIFTY", rows, 24060.0, "CE", 1030)               # price ran onto the locked strike: re-pick two out from the new ATM
    assert d["relocked"] and d["strike"] == 24150, d
    g = lock.pick("NIFTY", rows, 24130.0, "CE", 1035)               # price ran PAST the locked strike (it is now in the money): never keep an ITM leg
    assert g["relocked"] and g["strike"] == 24250 and g["offset_steps"] == 2, g
    e = lock.pick("NIFTY", rows, 24060.0, "PE", 1040)               # side flipped: new contract
    assert e["relocked"] and e["type"] == "PE"
    lock.idle("NIFTY", 1040 + 301)
    f = lock.pick("NIFTY", rows, 23960.6, "PE", 1400)               # setup gone for > 5 min: released, picks fresh
    assert f["relocked"]


def _plan(side="CE", opt_up=True, room=None, atr=18.4, price=23960.6, ltp=120.0):
    rows = chain(base_ce=ltp)
    pick = op.pick_strike(rows, price, side)
    read = {"status": "ok", "bull_score": 80 if opt_up else 20, "bear_score": 20 if opt_up else 80, "regime_label": "UPTREND"}
    return op.build_plan(side, "NIFTY", pick, price, atr, dd.THRESHOLDS, cost_points=4.0, lot=75, opt_read=read, room=room)


def test_buy_plan_has_option_entry_stop_target_and_rr():
    plan = _plan()
    assert plan["ok"], plan["summary"]
    lv = plan["levels"]
    assert lv["sl"] < lv["entry"] < lv["target"] and lv["rr"] >= op.MIN_RR
    assert lv["index_stop"] < lv["index_entry"] < lv["index_target"]
    assert abs(lv["target_points"] - 3 * 18.4) < 0.2                 # open air: the normal 3 ATR target
    assert plan["contract"]["label"] == "NIFTY 24050 CE" and plan["contract"]["lot"] == 75


def test_target_is_cut_back_to_the_wall_and_blocked_when_no_room():
    near = _plan(room={"price": 24010.0, "distance": 49.4, "basis": "swing high"})       # wall 49 pts away: target pulled in, still >= 1.5 x stop
    assert near["ok"] and near["levels"]["target_points"] < 3 * 18.4 and "swing high" in near["levels"]["target_name"], near["summary"]
    tight = _plan(room={"price": 23975.0, "distance": 14.4, "basis": "round number"})   # wall glued to price: no profit possible
    assert not tight["ok"] and any("Room" in c["label"] and not c["pass"] for c in tight["checks"])


def test_falling_premium_chart_vetoes_the_buy():
    plan = _plan(opt_up=False)
    assert not plan["ok"] and any(c["label"].startswith("Premium chart") and not c["pass"] for c in plan["checks"])


def test_decide_buy():
    fin = lambda lvl="CONFIRMED": {"level": lvl, "action": f"{lvl} CE SETUP", "headline": "h", "blockers": []}
    ok = op.decide_buy(fin(), _plan(), "CE")
    assert ok["buy"] and ok["buy_basis"] == "option" and ok["action"].startswith("BUY NIFTY 24050 CE")
    no = op.decide_buy(fin(), _plan(opt_up=False), "CE")
    assert not no["buy"] and "NO BUY" in no["action"] and no["blockers"]
    idx = op.decide_buy(fin(), None, "CE")                          # option chain unavailable: index-only buy, clearly labelled
    assert idx["buy"] and idx["buy_basis"] == "index" and "index levels" in idx["action"]
    assert not op.decide_buy(fin("WATCH"), _plan(), "CE")["buy"]    # a watch is never a buy


def test_premium_pnl():
    est = op.premium_pnl(120.0, 0.4, 55.2, 4.0, 75)                 # +55.2 index pts x 0.4 = +22.08 premium, cost 4 x 0.4 = 1.6
    assert est["exit"] == 142.08 and est["gross"] == 22.08 and est["net"] == 20.48 and est["net_rs"] == 1536 and "est." in est["basis"]
    live = op.premium_pnl(120.0, 0.4, -15.0, 4.0, 75, live_exit=112.0)
    assert live["exit"] == 112.0 and live["net"] == -9.6 and live["basis"] == "contract price"
    assert op.premium_pnl(None, 0.4, 5, 4)["net"] is None
    assert op.premium_pnl(10.0, 0.5, -400, 4)["exit"] == 0.05       # premium can not go below zero


# ---------------- candle-accurate exits: the -74 point "stop" regression
def bars(start, rows, step=5):
    out, t = [], start
    for o, h, l, c in rows:
        out.append({"time": t.isoformat(), "open": o, "high": h, "low": l, "close": c, "volume": 0}); t += timedelta(minutes=step)
    return out


def test_stop_is_filled_at_the_stop_not_at_the_price_the_server_woke_up_to():
    p = T.make_db()
    with sqlite3.connect(p) as db:
        db.execute("INSERT INTO signal_log (generated_at,session_date,symbol,interval,mode,candle_time,side,last_close,entry,stop_loss,target,target_name) "
                   "VALUES ('2026-11-04T10:02:00+05:30','2026-11-04','NIFTY',5,'scalp','c','PE',23880,23880,23895,23825,'T2')")
    # signal at 10:02 (inside the 10:00 bar). 10:05 bar rallies through the 23895 stop; the server only wakes up at 13:00 with price 23954
    cs = bars(datetime(2026, 11, 4, 10, 0, tzinfo=IST), [(23880, 23890, 23875, 23885), (23885, 23902, 23884, 23900), (23900, 23930, 23898, 23925)])
    now = datetime(2026, 11, 4, 13, 0, tzinfo=IST)
    closed = sx.settle_open_signals(p, "NIFTY", 23954.0, now, candles=cs, interval=5)
    r = T.row(p)
    assert closed[0]["status"] == "STOP" and r["exit_price"] == 23895 and r["gross_pnl_points"] == -15 and r["net_pnl_points"] == -19, r
    assert r["exit_time"].startswith("2026-11-04T10:05")


def test_gap_through_the_stop_is_a_real_loss_and_target_beats_nothing():
    p = T.make_db(); T.add(p, side="CE", entry=100.0, sl=85.0, tp=145.0, sdate="2026-10-29")
    with sqlite3.connect(p) as db:
        db.execute("UPDATE signal_log SET generated_at='2026-10-29T10:02:00+05:30'")
    cs = bars(datetime(2026, 10, 29, 10, 0, tzinfo=IST), [(100, 101, 99, 100), (80, 82, 70, 75)])      # next bar OPENS at 80, below the 85 stop
    sx.settle_open_signals(p, "NIFTY", 75.0, datetime(2026, 10, 29, 10, 20, tzinfo=IST), candles=cs, interval=5)
    r = T.row(p)
    assert r["trade_status"] == "STOP" and r["exit_price"] == 80 and r["gross_pnl_points"] == -20


def test_stop_wins_when_one_bar_touches_both_levels():
    p = T.make_db(); T.add(p, side="CE", entry=100.0, sl=85.0, tp=130.0)
    with sqlite3.connect(p) as db:
        db.execute("UPDATE signal_log SET generated_at='2026-10-29T10:02:00+05:30'")
    cs = bars(datetime(2026, 10, 29, 10, 0, tzinfo=IST), [(100, 101, 99, 100), (100, 135, 80, 120)])
    sx.settle_open_signals(p, "NIFTY", 120.0, datetime(2026, 10, 29, 10, 20, tzinfo=IST), candles=cs, interval=5)
    assert T.row(p)["trade_status"] == "STOP"


def test_entry_bar_range_is_ignored_and_open_trade_stays_open():
    p = T.make_db(); T.add(p, side="CE", entry=100.0, sl=85.0, tp=130.0)
    with sqlite3.connect(p) as db:
        db.execute("UPDATE signal_log SET generated_at='2026-10-29T10:03:00+05:30'")
    cs = bars(datetime(2026, 10, 29, 10, 0, tzinfo=IST), [(100, 140, 60, 100), (100, 110, 95, 105)])   # wild entry bar (pre-signal prices) must not exit the trade
    assert sx.settle_open_signals(p, "NIFTY", 105.0, datetime(2026, 10, 29, 10, 9, tzinfo=IST), candles=cs, interval=5) == []
    assert T.row(p)["trade_status"] == "OPEN"


def test_trade_from_an_earlier_session_is_reconstructed_not_expired():
    p = T.make_db(); T.add(p, side="CE", entry=100.0, sl=85.0, tp=160.0, sdate="2026-10-29")
    with sqlite3.connect(p) as db:
        db.execute("UPDATE signal_log SET generated_at='2026-10-29T10:02:00+05:30'")
    day = bars(datetime(2026, 10, 29, 10, 0, tzinfo=IST), [(100, 101, 99, 100)] + [(100 + i, 106 + i, 99 + i, 104 + i) for i in range(12)])
    sx.settle_open_signals(p, "NIFTY", None, datetime(2026, 10, 30, 9, 20, tzinfo=IST), candles=day, interval=5)
    r = T.row(p)
    assert r["trade_status"] == "SQUAREOFF" and r["exit_price"] == 115 and "reconstructed" in r["exit_reason"], r   # last bar close 104+11
    p2 = T.make_db(); T.add(p2, sdate="2026-10-29")
    sx.settle_open_signals(p2, "NIFTY", 22800, datetime(2026, 10, 30, 9, 20, tzinfo=IST))                    # no candles: still honest EXPIRED
    assert T.row(p2)["trade_status"] == "EXPIRED"


def test_option_result_is_recorded_on_exit():
    p = T.make_db(); T.add(p, side="CE", entry=100.0, sl=85.0, tp=130.0)
    with sqlite3.connect(p) as db:
        db.execute("UPDATE signal_log SET generated_at='2026-10-29T10:02:00+05:30', opt_entry=120.0, opt_delta=0.4, lot=75")
    cs = bars(datetime(2026, 10, 29, 10, 0, tzinfo=IST), [(100, 101, 99, 100), (101, 131, 100, 128)])
    sx.settle_open_signals(p, "NIFTY", 128.0, datetime(2026, 10, 29, 10, 20, tzinfo=IST), candles=cs, interval=5)
    r = T.row(p)
    assert r["trade_status"] == "TARGET" and r["opt_exit"] == 132.0 and r["opt_net"] == 10.4 and r["net_rs"] == 780, r     # +30 x 0.4 = 12, cost 4 x 0.4
    s = sx.summarize([r], "day")["total"]
    assert s["net_rs"] == 780


# ---------------- trend-aware RSI edge
def test_rsi_edge_widens_only_inside_a_confirmed_trend():
    import test_scalp as S
    a = S.run(S.selloff([-20], bounce=22), "standard")                # RSI ~35, DOWNTREND confirmed
    g = [x for x in a["gates"] if x["label"].startswith("Momentum not overextended")][0]
    assert "RSI > 28 for PE" in g["label"] and "widened" in g["detail"]
    b = S.run(S.selloff([-20], bounce=14), "standard")                # RSI ~26: climactic, still blocked even in a trend
    gb = [x for x in b["gates"] if x["label"].startswith("Momentum not overextended")][0]
    assert not gb["pass"], gb


def test_main_wires_option_leg():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"), encoding="utf-8").read()
    for needle in ("_option_leg(", "op.decide_buy(", "OPTION_LOCK", "_open_trade_exists", "candles=result[\"candles\"]", "_exit_candles", "final.get(\"buy\")", '"NOBUY"'):
        assert needle in src, needle


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
    print("all option plan tests passed")
