"""v1.20.72: fast trend identifier + priority score model (classic stays the default and is unchanged)."""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import indicators as ind

IST = timezone(timedelta(hours=5, minutes=30))


def series(closes, start=datetime(2026, 11, 12, 9, 15, tzinfo=IST), wick=3.0):
    out, prev = [], closes[0]
    for k, c in enumerate(closes):
        out.append({"time": (start + timedelta(minutes=5 * k)).isoformat(), "open": prev, "high": max(prev, c) + wick, "low": min(prev, c) - wick,
                    "close": c, "volume": 1000})
        prev = c
    return out


def downtrend_then_turn(up_bars):
    closes = [25500 - 6 * k + (4 if k % 2 else -4) for k in range(60)]        # steady fall: slow indicators all bearish
    base = closes[-1]
    closes += [base + 22 * (k + 1) for k in range(up_bars)]                   # sharp one-way turn up
    return series(closes)


def test_fast_trend_sees_the_turn_within_a_few_bars():
    raw = downtrend_then_turn(4)
    cs = ind.normalize_candles(raw)
    closes = [c["close"] for c in cs]
    ft = ind.fast_trend(cs, ind.ema(closes, 9), ind.ema(closes, 21), ind.atr_wilder(cs))
    assert ft["direction"] == 1 and ft["age_bars"] == 0 and ft["move_atr"] >= 1.5


def test_fast_trend_quiet_market_and_gap_are_not_a_trend():
    flat = ind.normalize_candles(series([25000 + (3 if k % 2 else -3) for k in range(60)]))
    cl = [c["close"] for c in flat]
    assert ind.fast_trend(flat, ind.ema(cl, 9), ind.ema(cl, 21), ind.atr_wilder(flat))["direction"] == 0
    day1 = series([25000 + (3 if k % 2 else -3) for k in range(60)], start=datetime(2026, 11, 11, 10, 0, tzinfo=IST))
    day2 = series([25150, 25152, 25151], start=datetime(2026, 11, 12, 9, 15, tzinfo=IST))      # opening gap only
    day2[0]["open"] = 25148
    gap = ind.normalize_candles(day1 + day2)
    cl = [c["close"] for c in gap]
    assert ind.fast_trend(gap, ind.ema(cl, 9), ind.ema(cl, 21), ind.atr_wilder(gap))["direction"] == 0


def _htf(raw):
    out = []
    for k in range(0, len(raw) - len(raw) % 3, 3):
        g = raw[k:k + 3]
        out.append({"time": g[0]["time"], "open": g[0]["open"], "high": max(x["high"] for x in g), "low": min(x["low"] for x in g),
                    "close": g[-1]["close"], "volume": 3000})
    return out


def test_priority_follows_price_while_classic_still_leans_the_old_way():
    closes = [25800 - 6 * k + (4 if k % 2 else -4) for k in range(150)]       # two days of steady fall: 15-min trend, Supertrend, ADX all bearish
    closes += [closes[-1] + 16 * (k + 1) for k in range(4)]                    # sharp one-way turn up
    raw = series(closes, start=datetime(2026, 11, 11, 9, 15, tzinfo=IST))
    kw = dict(htf_raw=_htf(raw), htf_interval=15, now=datetime(2026, 11, 13, 15, 0, tzinfo=IST), mode="standard")
    classic = ind.analyze(raw, "NIFTY", 5, score_model="classic", **kw)
    priority = ind.analyze(raw, "NIFTY", 5, score_model="priority", **kw)
    assert classic["fast_trend"]["direction"] == 1 and priority["fast_trend"]["direction"] == 1     # identifier runs in both
    assert classic["side"] == "PE"                                    # slow indicators still vote for the old fall
    assert priority["side"] == "CE" and priority["score"] == priority["bull_score"]
    assert priority["bull_score"] > classic["bull_score"] and priority["bear_score"] < classic["bear_score"]
    assert priority["score_classic"] == {"bull": classic["bull_score"], "bear": classic["bear_score"], "side": "PE"}
    assert sum(c["weight"] for c in priority["checks"] if c["key"] in ind.PRIORITY_WEIGHTS) == 100
    assert {g["label"] for g in priority["gates"]} >= {"Not sideways / not in a range", "CE needs price-structure uptrend (not indicators alone)"}


def test_fast_trend_ends_after_a_counter_move():
    closes = [25000 + (3 if k % 2 else -3) for k in range(60)]
    closes += [closes[-1] + 20 * (k + 1) for k in range(4)]                    # burst up
    closes += [closes[-1] - 9 * (k + 1) for k in range(4)]                     # gives most of it back, still above EMA 21
    cs = ind.normalize_candles(series(closes))
    cl = [c["close"] for c in cs]
    assert ind.fast_trend(cs, ind.ema(cl, 9), ind.ema(cl, 21), ind.atr_wilder(cs))["direction"] == 0


def test_classic_is_the_default_and_unchanged():
    assert ind.SCORE_MODEL == "classic"
    raw = downtrend_then_turn(0)
    a = ind.analyze(raw, "NIFTY", 5, now=datetime(2026, 11, 12, 15, 0, tzinfo=IST))
    assert a["score_model"] == "classic" and not any(c["key"] == "structure" for c in a["checks"])
    assert a["score"] == max(a["bull_score"], a["bear_score"])
    assert ind.set_score_model("priority") == "priority" and ind.set_score_model("nonsense") == "classic"


def test_gemini_limit_leaves_room_for_thinking_and_reads_finish_reason():
    import monitor as mon
    assert mon.gemini_output_limit(1200) >= 1200 + 2000
    data = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "hidden", "thought": True}, {"text": "A CE move of +58.0"}]}}]}
    assert mon.gemini_reply(data) == ("A CE move of +58.0", "MAX_TOKENS")
    assert mon.gemini_reply({"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]}) == ("", "MAX_TOKENS")


def test_stuck_analysis_is_dropped_not_kept_forever():
    import asyncio
    import time
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    ns = {"time": time, "asyncio": asyncio, "market_status": lambda: {"open": True}}
    exec(src[src.index("_AN_CACHE = {}"):src.index('@app.get("/api/option-analysis")')], ns)
    ns["AN_COMPUTE_TIMEOUT"] = 0.05

    async def go():
        async def never():
            await asyncio.sleep(30)

        async def ok():
            return {"v": 1}
        try:
            await ns["shared_analysis"](("stuck",), never)
            return "no timeout"
        except asyncio.TimeoutError:
            pass
        assert ("stuck",) not in ns["_AN_TASKS"]                 # released: later requests are not stuck behind it
        ns["_AN_FAIL"].clear()
        return (await ns["shared_analysis"](("stuck",), ok))["v"]
    assert asyncio.run(go()) == 1


def _early_case(tail):
    closes = [25000 + (3 if k % 2 else -3) for k in range(60)]
    closes += [closes[-1] + 18 * (k + 1) for k in range(4)]            # burst up (fast trend fires on its last bar)
    base = closes[-1]
    closes += [base + x for x in tail]
    cs = ind.normalize_candles(series(closes))
    cl = [c["close"] for c in cs]
    atr, e9 = ind.atr_wilder(cs), ind.ema(cl, 9)
    return cs, atr, (cl[-1] - e9[-1]) / atr


def test_early_trend_entry_needs_burst_pause_and_break():
    import trend_entry as te
    PB = "Pullback-and-resume trigger (retest holds)"
    FRESH = "Not a fresh breakout / breakdown bar (wait for the retest)"
    assert "early" in te.VARIANTS
    cs, atr, st = _early_case([-4, -6, 6])                             # pause for two bars, then a close above them
    hit = te.check(cs, "CE", atr, 70, 24000, min(st, 1.4), [PB, FRESH], "early")
    assert hit and hit["variant"] == "early" and "burst" in hit["detail"]
    assert te.check(cs, "CE", atr, 70, 24000, min(st, 1.4), [PB, "Score ≥ 60"], "early") is None        # score is never relaxed
    assert te.check(cs, "CE", atr, 70, 24000, min(st, 1.4), [PB, "Not sideways / not in a range"], "early") is None
    assert te.check(cs, "CE", atr, 70, 24000, 2.0, [PB], "early") is None                                 # too far beyond EMA 9
    assert te.check(cs, "CE", atr, 85, 24000, 1.0, [PB], "early") is None                                 # RSI beyond 80
    assert te.check(cs, "CE", atr, 70, 99999, 1.0, [PB], "early") is None                                 # below VWAP
    assert te.check(cs, "PE", atr, 30, 99999, 1.0, [PB], "early") is None                                 # wrong side
    burst_only, atr2, _ = _early_case([])
    assert te.check(burst_only, "CE", atr2, 70, 24000, 1.0, [PB], "early") is None                        # the burst bar itself is never bought
    no_break, atr3, _ = _early_case([-4, -6, -2])
    assert te.check(no_break, "CE", atr3, 70, 24000, 1.0, [PB], "early") is None                          # still pausing
