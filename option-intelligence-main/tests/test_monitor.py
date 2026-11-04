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
        assert "not connected" in rv["text"]
    finally:
        M.runtime_token = old[0]
        if old[1]:
            os.environ["ANTHROPIC_API_KEY"] = old[1]
