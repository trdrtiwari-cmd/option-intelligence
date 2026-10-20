"""Run: python tests/test_dashboard_data.py   (no pytest needed)"""
import os, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard_data as dd
from indicators import IST, normalize_candles


def test_csv_and_quotes_and_breadth():
    csv_text = "Company Name,Industry,Symbol,Series,ISIN Code\nA Ltd,Fin,AAA,EQ,INE000A01011\nB Ltd,IT,BBB,EQ,INE000B01012\nC Ltd,IT,CCC,EQ,INE000C01013\nD Ltd,IT,DDD,BE,INE000D01014\n"
    cons = dd.parse_constituents_csv(csv_text)
    assert [c["symbol"] for c in cons] == ["AAA", "BBB", "CCC"], cons
    resp = {"data": {
        "NSE_EQ:AAA": {"instrument_token": "NSE_EQ|INE000A01011", "last_price": 110, "net_change": 10, "ohlc": {"close": 110}},
        "NSE_EQ:BBB": {"instrument_token": "NSE_EQ|INE000B01012", "last_price": 95, "net_change": -5, "ohlc": {"close": 95}},
        "NSE_EQ:CCC": {"instrument_token": "NSE_EQ|INE000C01013", "last_price": 50, "net_change": 0, "ohlc": {"close": 50}}}}
    q = dd.quotes_by_key(resp)
    assert abs(q["NSE_EQ|INE000A01011"]["pct"] - 10.0) < 1e-9
    r = dd.advance_decline(cons, q, "Test")
    assert (r["advances"], r["declines"], r["unchanged"]) == (1, 1, 1) and r["signal"]["state"] == "NEUTRAL"
    # strongly bullish
    assert dd.breadth_signal(2.0, 30, 15)["state"] == "BULLISH"
    assert dd.breadth_signal(0.5, 10, 30)["state"] == "BEARISH"


def test_sector_resolution_and_signal():
    idx = [{"segment": "NSE_INDEX", "name": n, "trading_symbol": n.upper(), "instrument_key": "NSE_INDEX|" + n}
           for n in ["Nifty Bank", "Nifty IT", "Nifty Auto", "Nifty FMCG", "Nifty Pharma", "Nifty Metal", "Nifty Fin Service"]]
    keys, missing = dd.resolve_sector_keys(idx)
    assert keys["Bank"] == "NSE_INDEX|Nifty Bank" and keys["Fin Services"] == "NSE_INDEX|Nifty Fin Service"
    assert "Realty" in missing
    quotes = {k: {"pct": 0.8, "ltp": 1.0} for k in keys.values()}
    res = dd.sector_heatmap(keys, quotes, missing)
    assert res["signal"]["state"] == "BULLISH" and res["green"] == 7
    quotes = {k: {"pct": (-0.8 if i % 2 else 0.8), "ltp": 1.0} for i, k in enumerate(keys.values())}
    assert dd.sector_heatmap(keys, quotes, missing)["signal"]["state"] == "NEUTRAL"


def chain(ce_ltp, ce_close, pe_ltp, pe_close, spot=24510):
    rows = []
    for s in (24400, 24450, 24500, 24550, 24600):
        rows.append({"strike_price": s, "underlying_spot_price": spot,
                     "call_options": {"instrument_key": f"CE{s}", "market_data": {"ltp": ce_ltp, "close_price": ce_close}},
                     "put_options": {"instrument_key": f"PE{s}", "market_data": {"ltp": pe_ltp, "close_price": pe_close}}})
    return rows


def test_option_premium():
    r = dd.build_option_premium(chain(120, 100, 80, 100), 24510)
    assert r["atm_strike"] == 24500 and r["signal"]["state"] == "BULLISH", r["signal"]
    assert abs(r["ce_pct"] - 20) < 1e-9 and abs(r["pe_pct"] + 20) < 1e-9
    r = dd.build_option_premium(chain(80, 100, 120, 100), 24510)
    assert r["signal"]["state"] == "BEARISH"
    r = dd.build_option_premium(chain(80, 100, 85, 100), 24510)
    assert r["signal"]["label"] == "PREMIUM DECAY" and r["signal"]["state"] == "CAUTION"
    r = dd.build_option_premium(chain(120, 100, 115, 100), 24510)
    assert r["signal"]["label"] == "VOLATILITY UP"
    # live tick overrides the REST snapshot
    r = dd.build_option_premium(chain(100, 100, 100, 100), 24510, live={"CE24500": (130, 100)})
    assert abs(r["ce_pct"] - 30) < 1e-9
    # new contract with no previous close -> no percentage, never a fake one
    r = dd.build_option_premium(chain(100, 0, 100, 0), 24510)
    assert r["ce_pct"] is None and r["signal"]["state"] == "UNAVAILABLE"


def mk(day, start_min, closes, base_open=None, interval=5):
    out, t0 = [], datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST) + timedelta(minutes=start_min - 555)
    prev = base_open if base_open is not None else closes[0]
    for i, c in enumerate(closes):
        out.append({"time": (t0 + timedelta(minutes=interval * i)).isoformat(), "open": prev, "high": max(prev, c) + 1,
                    "low": min(prev, c) - 1, "close": c, "volume": 0})
        prev = c
    return out


def test_graph_and_zone():
    d1, d2 = datetime(2026, 10, 19, tzinfo=IST), datetime(2026, 10, 20, tzinfo=IST)
    up = [100 + i * 2 for i in range(20)]
    down = [140 - i * 2 for i in range(20)]
    ce, pe = normalize_candles(mk(d2, 555, up)), normalize_candles(mk(d2, 555, down))
    g = dd.build_premium_graph(ce, pe, {"strike": 24500}, {"strike": 24500})
    assert g["signal"]["state"] == "BULLISH" and g["ce"]["state"] == "RISING" and g["pe"]["state"] == "FALLING"
    assert g["session"] == "2026-10-20"
    # zone: previous day flat ~24500, today climbs above everything -> UP
    prev = mk(d1, 555, [24500 + (i % 3) for i in range(75)])
    today_up = mk(d2, 555, [24510 + i * 3 for i in range(30)], base_open=24505)
    z = dd.index_zone(normalize_candles(prev + today_up), None, "Nifty")
    assert z["zone"] == "UP" and z["signal"]["state"] == "BULLISH", z["checks"]
    today_dn = mk(d2, 555, [24490 - i * 3 for i in range(30)], base_open=24495)
    z = dd.index_zone(normalize_candles(prev + today_dn), None, "Nifty")
    assert z["zone"] == "DOWN" and z["signal"]["state"] == "BEARISH"
    today_mid = mk(d2, 555, [24500 + (i % 2) for i in range(30)], base_open=24500)
    z = dd.index_zone(normalize_candles(prev + today_mid), 24500.5, "Nifty")
    assert z["zone"] == "MID", (z["zone"], z["score"])
    assert dd.index_zone(normalize_candles(today_up), None)["status"] == "UNAVAILABLE"


def test_overall():
    S = lambda s: {"state": s}
    assert dd.overall_bias({"a": S("BULLISH"), "b": S("BULLISH"), "c": S("BULLISH"), "d": S("NEUTRAL"), "e": S("BEARISH")})["state"] == "BULLISH"
    assert dd.overall_bias({"a": S("BEARISH"), "b": S("BEARISH"), "c": S("BEARISH"), "d": S("CAUTION")})["state"] == "BEARISH"
    assert dd.overall_bias({"a": S("BULLISH"), "b": S("BEARISH"), "c": S("NEUTRAL")})["state"] == "NEUTRAL"
    assert dd.overall_bias({"a": S("BULLISH"), "b": S("UNAVAILABLE"), "c": S("UNAVAILABLE")})["state"] == "UNAVAILABLE"


def blk(state, label=None, detail="d"):
    return {"signal": {"state": state, "label": label or state, "detail": detail}}


ZONE_LEVELS = {"day_open": 24500, "prev_close": 24480, "pdh": 24650, "pdl": 24400, "orh": 24520, "orl": 24480,
               "avg_price": 24510, "avg_basis": "Session avg price", "day_high": 24600, "day_low": 24470}


def test_final_signal():
    z = {**blk("BULLISH", "UP"), "levels": ZONE_LEVELS}
    allbull = {"zone": z, "premium": blk("BULLISH"), "graph": blk("BULLISH"), "breadth": blk("BULLISH"), "sectors": blk("BULLISH")}
    f = dd.final_signal(allbull)
    assert f["action"] == "BUY CE SETUP" and f["level"] == "CONFIRMED" and f["score"] == 100, f
    assert f["plan"] and f["invalidation"] and len(f["contributions"]) == 5
    # premium decay caps CONFIRMED at BIAS and adds a caution
    f = dd.final_signal({**allbull, "premium": blk("CAUTION", "PREMIUM DECAY")})
    assert f["level"] == "BIAS" and any("decaying" in c for c in f["cautions"]), f
    # price zone opposing -> only WATCH even with a positive score
    f = dd.final_signal({"zone": {**blk("BEARISH", "DOWN"), "levels": ZONE_LEVELS}, "premium": blk("BULLISH"), "graph": blk("BULLISH"),
                         "breadth": blk("BULLISH"), "sectors": blk("BULLISH")})
    assert f["side"] == "CE" and f["level"] == "WATCH" and f["against"], f
    # bearish confirmed
    f = dd.final_signal({"zone": {**blk("BEARISH", "DOWN"), "levels": ZONE_LEVELS}, "premium": blk("BEARISH"), "graph": blk("BEARISH"),
                         "breadth": blk("BEARISH"), "sectors": blk("NEUTRAL")})
    assert f["action"] == "BUY PE SETUP" and f["state"] == "BEARISH", f
    # cancelling checks -> WAIT
    f = dd.final_signal({"zone": blk("NEUTRAL", "MID"), "premium": blk("BULLISH"), "graph": blk("BEARISH"), "breadth": blk("NEUTRAL"), "sectors": blk("NEUTRAL")})
    assert f["action"] == "WAIT" and f["level"] == "NO EDGE"
    # not enough data
    f = dd.final_signal({"zone": blk("BULLISH"), "premium": blk("UNAVAILABLE"), "graph": blk("UNAVAILABLE"), "breadth": blk("UNAVAILABLE"), "sectors": blk("BULLISH")})
    assert f["action"] == "NO SIGNAL"
    # market closed warning
    assert any("closed" in c for c in dd.final_signal(allbull, market_open=False)["cautions"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
