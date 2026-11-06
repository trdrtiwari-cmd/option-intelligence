"""v1.20.0 strategy monitor: plain-code findings (feed, rules, loss causes, missed moves, suggestions) and storage."""
import os, sys, json, asyncio, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import monitor as mon


def test_feed_checks():
    ok = mon.check_feed({"symbol": "NIFTY", "stream_running": True, "index_tick_age": 0.3, "analysis_age": 2, "token_ok": True})
    assert ok == []
    bad = mon.check_feed({"symbol": "NIFTY", "stream_running": False, "index_tick_age": 12, "analysis_age": 40, "token_ok": True, "signal_active": True})
    assert {f["detail"]["check"] for f in bad} == {"stream", "tick_age", "analysis_age"} and all(f["severity"] == "high" for f in bad)
    assert mon.check_feed({"token_ok": False})[0]["dedupe"] == "feed-token"


def test_trade_rules_and_loss_causes():
    rows = [
        {"id": 1, "symbol": "NIFTY", "side": "CE", "generated_at": "2026-11-04T12:40:00+05:30", "atr_at_entry": 20, "sl_atr_mult": 1.0,
         "entry": 24000, "stop_loss": 23980, "exit_price": 23968, "trade_status": "STOP", "net_pnl_points": -36, "peak_points": 25, "worst_points": -32,
         "option_entry": 200, "option_exit": 170, "option_delta": 0.6, "exit_time": "2026-11-04T12:55:00+05:30"},
        {"id": 2, "symbol": "NIFTY", "side": "PE", "generated_at": "2026-11-04T10:00:00+05:30", "atr_at_entry": 20, "sl_atr_mult": 1.5,
         "entry": 24000, "stop_loss": 24030, "exit_price": 23970, "trade_status": "TARGET", "net_pnl_points": 26},
    ]
    f = mon.check_trades(rows)
    kinds = {x["dedupe"].rsplit("-", 1)[0] for x in f}
    assert "rule-sl" in kinds and "rule-slip" in kinds                      # 1.0 ATR stop; 12-pt fill beyond the stop (0.6 ATR)
    assert not any(x["detail"].get("trade_id") == 2 for x in f)
    causes = [c["cause"] for c in mon.loss_causes(rows[0])]
    assert causes[0] == "Gave back a winner" and "Midday entry" in causes
    L = mon.losses(rows)
    assert len(L) == 1 and L[0]["detail"]["trade_id"] == 1
    assert mon.today_stats(rows) == {"signals": 2, "closed": 2, "wins": 1, "win_rate": 50.0, "net_points": -10.0}


def test_missed_and_suggestions_from_real_report():
    p = "/mnt/user-data/uploads/0a048331-diagnostics-NIFTY-scalp__13_.json"
    if not os.path.exists(p):
        return
    rep = json.load(open(p))
    day = rep["missed_moves"]["examples"][0]["day"]
    m = mon.missed_from_replay(rep, day)
    assert m and all(x["kind"] == "MISSED" and "blocked by" in x["title"] for x in m)
    s = mon.suggestions(rep)
    assert s and any(x["area"] == "missed" for x in s)
    assert any("protecting you" in x["title"] for x in s)       # the CE-structure gate blocked losing setups (avg -6.5)
    ctx = mon.review_context(day, "NIFTY", [], m, [], rep, s)
    assert len(json.dumps(ctx, default=str)) < 30000


def test_board_missed():
    rows = [{"id": 5, "stage": "NO BUY", "best_pct_60": 42, "test_result": "STOP", "created_at": "2026-11-04T10:05:00"},
            {"id": 6, "stage": "BUY", "best_pct_60": 80, "test_result": "TARGET"},
            {"id": 7, "stage": "WATCH", "best_pct_60": 5, "test_result": "STOP"}]
    assert [x["detail"]["board_id"] for x in mon.missed_from_board(rows)] == [5]


def test_storage_dedupe_and_reviews():
    d = tempfile.mkdtemp(); db = os.path.join(d, "t.sqlite3")
    mon.init(db)
    ev = {"day": "2026-11-04", "symbol": "NIFTY", "kind": "LOSS", "severity": "medium", "title": "x", "detail": {"a": 1}, "dedupe": "loss-1"}
    i = mon.add_event(db, ev)
    assert i and mon.add_event(db, ev) is None
    mon.set_note(db, i, "because", "claude-haiku-5-5")
    mon.add_review(db, {"day": "2026-11-04", "symbol": "NIFTY", "kind": "eod", "model": "claude-sonnet-5-5", "text": "t", "suggestions": [{"a": 1}], "context": {}})
    assert mon.calls_today(db, "2026-11-04") == 2
    e = mon.events(db, "2026-11-04", "2026-11-04")
    assert e[0]["detail"] == {"a": 1} and e[0]["claude_note"] == "because"
    assert mon.reviews(db)[0]["suggestions"] == [{"a": 1}]


def test_claude_call_without_key():
    old = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        text, err = asyncio.run(mon.claude_call("s", "u", "claude-haiku-5-5"))
        assert text is None and "ANTHROPIC_API_KEY" in err
    finally:
        if old:
            os.environ["ANTHROPIC_API_KEY"] = old


def test_review_without_token_or_key():
    try:
        import main as M
    except Exception:
        return
    old = (M.runtime_token, os.environ.pop("ANTHROPIC_API_KEY", None))
    M.runtime_token = None
    p = "/mnt/user-data/uploads/0a048331-diagnostics-NIFTY-scalp__13_.json"
    M._SMON["rep"] = json.load(open(p)) if os.path.exists(p) else {}
    pushed = []
    async def fake_push(*a, **k): pushed.append(a)
    M._dispatch_confirmed_push = fake_push
    try:
        r = asyncio.run(M.monitor_review("manual", force=True))
        assert r["status"] == "done" and r["claude"] is False
        rv = M.mon.reviews(M.SIGNAL_LOG_DB, limit=1)[0]
        assert "No AI connected" in rv["text"]
    finally:
        M.runtime_token = old[0]
        if old[1]:
            os.environ["ANTHROPIC_API_KEY"] = old[1]


def test_provider_routing():
    keep = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")}
    old = dict(mon.SETTINGS)
    try:
        assert mon.provider() is None and mon.ai_label() is None
        os.environ["GEMINI_API_KEY"] = "g"
        assert mon.provider() == "gemini" and mon.model_for("eod") == mon.SETTINGS["gemini_model"]
        os.environ["ANTHROPIC_API_KEY"] = "a"
        assert mon.provider() == "claude" and mon.model_for("eod") == mon.SETTINGS["review_model"]
        mon.SETTINGS["provider"] = "gemini"
        assert mon.provider() == "gemini"
        mon.SETTINGS["provider"] = "off"
        assert mon.provider() is None
        assert asyncio.run(mon.ai_call("s", "u")) == (None, None, None)
    finally:
        mon.SETTINGS.clear(); mon.SETTINGS.update(old)
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v:
                os.environ[k] = v


def test_gemini_request_shape():
    import types
    sent = {}

    class R:
        status_code = 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "Because "}, {"text": "midday."}]}}]}

    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            sent.update(url=url, body=json, headers=headers); return R()

    fake = types.ModuleType("httpx"); fake.AsyncClient = C
    old_mod = sys.modules.get("httpx"); sys.modules["httpx"] = fake
    os.environ["GEMINI_API_KEY"] = "g-key"
    try:
        text, err = asyncio.run(mon.gemini_call("sys", "user", "gemini-flash-lite-latest", 100))
        assert text == "Because midday." and err is None
        assert sent["url"].endswith("/models/gemini-flash-lite-latest:generateContent") and sent["headers"]["x-goog-api-key"] == "g-key"
        assert sent["body"]["systemInstruction"]["parts"][0]["text"] == "sys" and sent["body"]["contents"][0]["parts"][0]["text"] == "user"
    finally:
        os.environ.pop("GEMINI_API_KEY", None)
        if old_mod is not None:
            sys.modules["httpx"] = old_mod
        else:
            sys.modules.pop("httpx", None)


def test_chat_bundle():
    b = mon.chat_bundle({"day": "2026-11-04"})
    assert b["instructions"] == mon.SYSTEM_REVIEW and b["data"]["day"] == "2026-11-04" and "review this day" in b["how_to_use"]


def test_feed_state_only_for_open_pages():
    try:
        import main as M
    except Exception:
        return
    import time as _t
    now = _t.time()
    M._WS_ACTIVE.clear()
    M._AN_CACHE[("NIFTY", 5, "standard", 70, -1)] = (now - 300, {})     # an old result nobody is subscribed to
    st = M._monitor_feed_state("NIFTY", now)
    assert st["analysis_age"] is None
    M._WS_ACTIVE[1] = {"sym": "NIFTY", "akey": ("NIFTY", 5, "standard", 70, -1), "since": now - 60}
    assert M._monitor_feed_state("NIFTY", now)["analysis_age"] >= 299
    M._WS_ACTIVE.clear()


def test_gemini_retry_and_fallback():
    calls = []
    async def fake(system, user, model, mt=700, to=40.0):
        calls.append(model)
        return (None, "Gemini API 503: high demand") if len(calls) < 3 else ("OK", None)
    old, oldsleep = mon.gemini_call, asyncio.sleep
    os.environ["GEMINI_API_KEY"] = "AIzaX"; os.environ.pop("ANTHROPIC_API_KEY", None)
    async def nosleep(*a): return None
    mon.gemini_call = fake; asyncio.sleep = nosleep
    try:
        t, e, m = asyncio.run(mon.ai_call("s", "u"))
        assert t == "OK" and e is None and m == mon.GEMINI_FALLBACK and len(calls) == 3
    finally:
        mon.gemini_call = old; asyncio.sleep = oldsleep; os.environ.pop("GEMINI_API_KEY", None)


def test_blocked_by_and_gate_stats():
    import signal_board as sb
    a = {"gates": [{"label": "Score ≥ 70", "pass": True}, {"label": "Not sideways / not in a range", "pass": False},
                   {"label": "Closed-candle price-action confirmation", "pass": False}]}
    final = {"blockers": ["Entry time window (live app): 12:40 is outside 09:45-12:00, 14:30-15:00", "Profit check: R:R: 0.8 < 1.5"]}
    b = sb.blocked_by(a, final)
    assert b == "Not sideways / not in a range · Closed-candle price-action confirmation · Entry time window (live app) · Profit check: R:R"
    rows = [
        {"stage": "WATCH", "symbol": "NIFTY", "option_name": "N 24350 CE", "created_at": "2026-11-05T12:40:00", "test_result": "TARGET", "test_points": 30, "blocked_by": b, "best_pct_60": 45},
        {"stage": "WATCH", "symbol": "NIFTY", "option_name": "N 24350 CE", "created_at": "2026-11-05T12:40:30", "test_result": "TARGET", "test_points": 30, "blocked_by": b, "best_pct_60": 45},  # same setup, other mode
        {"stage": "WATCH", "symbol": "NIFTY", "option_name": "N 24400 CE", "created_at": "2026-11-05T10:05:00", "test_result": "STOP", "test_points": -38, "blocked_by": "Not sideways / not in a range"},
    ]
    st = {g["gate"]: g for g in mon.board_gate_stats(rows)}
    assert st["Not sideways / not in a range"]["setups"] == 2 and st["Not sideways / not in a range"]["target"] == 1
    assert st["Entry time window (live app)"]["win_rate"] == 100.0
    m = mon.missed_from_board(rows)
    assert len(m) == 1 and m[0]["detail"]["blocked_by"][0] == "Not sideways / not in a range"


def test_init_removes_duplicate_board_findings():
    d = tempfile.mkdtemp(); db = os.path.join(d, "t.sqlite3")
    mon.init(db)
    for i in (381, 40):
        mon.add_event(db, {"day": "2026-11-05", "kind": "MISSED", "severity": "info", "title": "WATCH NIFTY 24450 CE 13:38 not bought", "dedupe": f"board-missed-{i}"})
    mon.init(db)
    assert len(mon.events(db, "2026-11-05", "2026-11-05")) == 1


def test_gemini_404_skips_and_bad_key_stops():
    calls = []
    async def fake(system, user, model, mt=700, to=40.0):
        calls.append(model)
        if model == "dead-model":
            return None, "Gemini API 404: model not found"
        return "fine", None
    old, oldsleep = mon.gemini_call, asyncio.sleep
    async def nosleep(*a): return None
    mon.gemini_call = fake; asyncio.sleep = nosleep
    mon._GEMINI_GATE.update(lock=None, last=0.0, cooldown_until=0.0); mon._GEMINI_FOUND.update(at=0.0, models=[], good=None)
    try:
        t, e, m = asyncio.run(mon._gemini_robust("s", "u", "dead-model", 50, 5))
        assert t == "fine" and m != "dead-model" and calls[0] == "dead-model" and len(calls) == 2
        async def badkey(*a, **k): calls.append("x"); return None, "Gemini API 400: API key not valid"
        mon.gemini_call = badkey; calls.clear()
        t, e, m = asyncio.run(mon._gemini_robust("s", "u", "gemini-flash-lite-latest", 50, 5))
        assert t is None and "400" in e
    finally:
        mon.gemini_call = old; asyncio.sleep = oldsleep; mon._GEMINI_GATE.update(lock=None, last=0.0, cooldown_until=0.0)


def test_export_includes_terminal_and_page():
    try:
        import main as M
    except Exception:
        return
    import json as _j
    M._need_user = lambda r: {"id": 1}
    old_tok = M.runtime_token
    M.runtime_token = None
    day = M.ist_now().date().isoformat()
    M._AN_CACHE[("NIFTY", 5, "scalp", 70, -1)] = (__import__("time").time(), {"status": "ok", "score": 72, "regime_label": "SIDEWAYS",
        "gates": [{"label": "Not sideways / not in a range", "pass": False}, {"label": "Score ≥ 70", "pass": True}],
        "final": {"level": "WATCH", "action": "WATCH CE", "headline": "h", "blockers": ["x: y"]}, "option_pick": {"name": "NIFTY 24300 CE", "ltp": 301.4}})
    try:
        r = asyncio.run(M._monitor_export_impl(None, day, 0, {"chart_view": "option", "trendlines": {"support": {"broken": True}}}))
        b = _j.loads(r.body)
        t = b["data"]["terminal"]
        assert t["app_version"] == M.APP_VERSION and "settings" in t and "feeds" in t
        la = [x for x in t["live_analyses"] if x["symbol"] == "NIFTY" and x["interval"] == 5 and x["mode"] == "scalp"]
        assert la and la[0]["analysis"]["gates_failed"] == ["Not sideways / not in a range"] and la[0]["analysis"]["option_pick"]["ltp"] == 301.4
        assert b["data"]["page"]["chart_view"] == "option"
        assert "terminal" in b["instructions"]
    finally:
        M.runtime_token = old_tok


def test_gemini_retired_models_switch_to_suggested_and_listed(monkeypatch):
    import asyncio
    import monitor as mon
    monkeypatch.setattr(mon, "GEMINI_MIN_GAP", 0)
    mon._GEMINI_FOUND.update(at=0.0, models=[], good=None)
    mon._GEMINI_GATE.update(lock=None, last=0.0, cooldown_until=0.0)
    calls = []

    async def fake_call(system, text, model, max_tokens=700, timeout=40.0):
        calls.append(model)
        if model == "gemini-9-flash":
            return "ok review", None
        return None, f"Gemini API 404: This model models/{model} is no longer available to new users. Please update your code to use models/gemini-9-flash for the latest features."

    async def fake_discover(force=False):
        return ["gemini-9-flash-lite"]
    monkeypatch.setattr(mon, "gemini_call", fake_call)
    monkeypatch.setattr(mon, "gemini_discover", fake_discover)
    t, e, used = asyncio.run(mon._gemini_robust("s", "u", "gemini-2.5-flash", 100, 5))
    assert t == "ok review" and used == "gemini-9-flash" and e is None
    assert mon._GEMINI_FOUND["good"] == "gemini-9-flash"
    calls.clear()
    asyncio.run(mon._gemini_robust("s", "u", "gemini-2.5-flash", 100, 5))
    assert calls == ["gemini-9-flash"]                 # the working model is tried first next time
    mon._GEMINI_FOUND.update(at=0.0, models=[], good=None)


def test_suggested_model_parsed():
    import monitor as mon
    e = "Gemini API 404: This model models/gemini-2.5-flash is no longer available to new users. Please update your code to use models/gemini-3.8-flash for the latest"
    assert mon._suggested_model(e) == "gemini-3.8-flash"


def test_ai_live_terminal_view():
    try:
        import main as M
    except Exception:
        return
    old = (M._need_user, M.analysis, M.mon.ai_call, M.mon.provider, M.market_status, M.get_signal_log)
    seen = []

    async def fake_analysis(*a, **k):
        return {"status": "ok", "last_close": 24500.0, "final": {"level": "CONFIRMED", "side": "CE"}, "gates": []}

    async def fake_ai(system, text, kind="note", max_tokens=700, timeout=40.0):
        seen.append(text)
        return "Price above EMA 9; BUY agrees with data.", None, "gemini-test"

    async def fake_log(*a, **k):
        return {"rows": [{"symbol": "NIFTY", "trade_status": "OPEN", "side": "CE", "entry": 24490, "stop_loss": 24450}]}
    M._need_user = lambda r: {"id": 1}
    M.analysis = fake_analysis
    M.mon.ai_call = fake_ai
    M.mon.provider = lambda: "gemini"
    M.market_status = lambda: {"open": True}
    M.get_signal_log = fake_log
    M._AI_LIVE["day"] = None
    try:
        r = asyncio.run(M.ai_live(None, M.AiLiveRequest(reason="buy", event="CE|10:05")))
        assert r["ok"] and r["comment"]["reason"] == "buy" and r["comment"]["signal"] == "CONFIRMED CE"
        assert '"open_trades":[{' in seen[0] and "a BUY signal just fired" in seen[0]
        again = asyncio.run(M.ai_live(None, M.AiLiveRequest(reason="buy", event="CE|10:05")))
        assert not again["ok"] and "Already" in again["skipped"] and len(seen) == 1       # same moment from another tab: no second call
        m1 = asyncio.run(M.ai_live(None, M.AiLiveRequest(reason="manual")))
        m2 = asyncio.run(M.ai_live(None, M.AiLiveRequest(reason="manual")))
        assert m1["ok"] and not m2["ok"] and len(seen) == 2                               # second press within 20 s shows the last answer
        g = asyncio.run(M.ai_live_get(None, "NIFTY", 5, "standard"))
        assert len(g["comments"]) == 2 and g["comments"][0]["reason"] == "manual"
        M.market_status = lambda: {"open": False}
        assert "market is open" in asyncio.run(M.ai_live(None, M.AiLiveRequest(reason="near", event="x")))["skipped"]
    finally:
        M._need_user, M.analysis, M.mon.ai_call, M.mon.provider, M.market_status, M.get_signal_log = old
        M._AI_LIVE["day"] = None


def test_consistency_checks_6_nov():
    import monitor as mon
    ctx = {"terminal": {"settings": {"background_alerts": {"mode": "standard", "score_min": 60},
                                     "signals_config": {"exit_plan": "trail", "one_per_side": "off"}},
                        "combos_in_use": [["NIFTY", 5, "standard", 60], ["NIFTY", 5, "scalp", 70]],
                        "live_analyses": [{"symbol": "NIFTY", "mode": "scalp", "analysis": {"side": "CE"}}]},
           "replay": {"live_exit": {"avg": 15.12}, "verdict": ["Live exit plan (stop 1.5 ATR, trail 0.75 ATR once +1.2 ATR, square-off 15:20): 10 trades, 60.0% winners, +3.5 pts per trade (+35 total)."],
                      "near_miss_by_gate": [{"gate": "Not a fresh breakout / breakdown bar (wait for the retest)", "n": 3, "avg": -3.13}]},
           "today_blocking_rules": [{"gate": "Not a fresh breakout / breakdown bar (wait for the retest)", "setups": 24, "avg_points": 13.2},
                                    {"gate": "Data Check dashboard agrees (≥ BIAS, same side)", "setups": 5, "avg_points": -112.1}],
           "page": {"symbol": "NIFTY", "mode": "scalp", "chart_view": "option", "selected_option": {"name": "NIFTY 24600 PE", "type": "PE"}}}
    c = " | ".join(mon.consistency_checks(ctx))
    assert "SETTINGS MISMATCH" in c and "scalp / 70" in c
    assert "EXIT PLAN" in c and "+15.1" in c and "+3.5" in c
    assert "ONE TRADE PER SIDE is off" in c and "CHART ON THE WRONG SIDE" in c and "ONE DAY" in c
    assert "KEEP 'Not a fresh breakout" in c and "-3.1" in c
    assert "PROTECTING: 'Data Check dashboard" in c
    assert mon.consistency_checks({}) == []
    assert "STRICT RULES" in mon.SYSTEM_REVIEW and "STRICT RULES" in mon.SYSTEM_NOTE


def test_consistency_checks_live_state():
    import monitor as mon
    ctx = {"terminal": {"app_version": "1.20.31", "token_state": "EXPIRED", "market": {"open": True},
                        "settings": {"signals_config": {"trend_entry": "off"}},
                        "feeds": {"NIFTY": {"stream_running": True, "tick_age_s": 240.0}, "BANKNIFTY": {"stream_running": False}},
                        "live_analyses": [{"symbol": "NIFTY", "interval": 5, "mode": "standard", "age_s": 900,
                                           "analysis": {"status": "ok", "regime_note": "UPTREND (trend): 6.3 ATR move up in 20 bars (efficiency 0.62) · x",
                                                        "final": {"level": "WATCH"}, "option_pick": None}}],
                        "monitor_status": {"error": "Gemini API 503"}},
           "page": {"page_version": "1.20.29", "issues": [{"level": "high", "text": "Chart shows PE 24600 but the engine leans CE"}, {"level": "info", "text": "x"}],
                    "js_errors": [{"msg": "boom"}], "failed_requests": [{"url": "/api/analysis", "error": "timed out"}]}}
    c = " | ".join(mon.consistency_checks(ctx))
    for k in ("UPSTOX TOKEN", "FEED NIFTY: last tick 240", "FEED BANKNIFTY: the live stream is not running", "STALE ANALYSIS NIFTY 5m standard",
              "STRONG TREND NIFTY 5m standard: 6.3 ATR up", "NO OPTION PICKED", "AI MONITOR ERROR", "OLD PAGE", "PAGE (high): Chart shows PE",
              "PAGE ERRORS: 1", "FAILED REQUESTS: 1"):
        assert k in c, k
    assert "PAGE (info)" not in c
