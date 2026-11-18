from pathlib import Path
S = Path(__file__).resolve().parents[1] / "static"

def test_terminal_watch_wait_follow_the_server_final():
    js = (S / "app.js").read_text(encoding="utf-8")
    assert 'fin.level==="WATCH"&&!/^WATCH/.test(text)' in js and 'fin.level==="WAIT"&&/^WATCH/.test(text)' in js


def test_data_check_no_longer_says_buy():
    import dashboard_data as dd
    src = (S.parent / "dashboard_data.py").read_text(encoding="utf-8")
    assert 'f"BUY {side} SETUP"' not in src and "SETUP CONFIRMED" in src
    assert "Market closed - last session's data" in (S / "dashboard.js").read_text(encoding="utf-8")


def test_summary_verdict_lines():
    import main
    assert main._summary_verdict({"level": "WATCH", "side": "CE"}, {"level": "CONFIRMED", "side": "CE"}, True)[0] == "warn"
    assert main._summary_verdict({"level": "WATCH", "side": "CE"}, {"level": "BIAS", "side": "PE"}, True)[0] == "bad"
    assert main._summary_verdict({"level": "CONFIRMED", "side": "PE", "buy": True}, {}, True)[0] == "good"
    assert main._summary_verdict({"level": "WATCH", "side": "CE"}, {}, False)[1].startswith("Market closed")


def test_summary_api_builds_without_errors(monkeypatch):
    import asyncio
    import main

    async def fake_analysis(**k):
        return {"status": "ok", "side": "CE", "bull_score": 77, "bear_score": 7, "last_close": 25291.1, "atr": 21.4,
                "final": {"level": "WATCH", "action": "WATCH CE", "side": "CE", "headline": "x"},
                "gates": [{"label": "Not chasing", "pass": False}], "feasibility": {"score": 50, "room_atr": 0.69}}

    async def fake_dash(**k):
        return {"final": {"action": "CE SETUP CONFIRMED · last session", "level": "CONFIRMED", "side": "CE", "score": 85, "agree": 4,
                          "oppose": 0, "available": 5, "headline": "h", "contributions": [{"name": "Zone", "label": "UP", "dir": 1}]}}

    async def fake_snap(day):
        return {"token_state": "VALID", "feeds": {"NIFTY": {"stream_running": True, "tick_age_s": 1.0}},
                "upstox_usage": {"by_api": {"intraday": {"peak_share_pct": 12.0, "last_30min": 120, "rate_limited_today": 0}}},
                "signal_log_today": {"signals": 0}, "signal_board_today": [], "monitor_status": {}, "settings": {}}

    class Req:
        pass

    monkeypatch.setattr(main, "analysis", fake_analysis)
    monkeypatch.setattr(main, "dashboard_summary", fake_dash)
    monkeypatch.setattr(main, "_terminal_snapshot", fake_snap)
    monkeypatch.setattr(main, "_need_user", lambda r: None)
    monkeypatch.setattr(main, "_poller_cfg", lambda: (["NIFTY"], 5, "standard", 60, None))
    d = asyncio.run(main.summary_api(Req()))
    r = d["symbols"][0]
    assert r["final"]["action"] == "WATCH CE" and r["data_check"]["level"] == "CONFIRMED" and r["verdict"]["text"]
    assert d["upstox_worst"]["pct"] == 12.0 and d["health"]
    html = (S / "summary.html").read_text(encoding="utf-8")
    assert "/api/summary" in html and 'href="/summary"' in (S / "mobile.js").read_text(encoding="utf-8")
