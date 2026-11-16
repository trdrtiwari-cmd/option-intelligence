"""v1.20.67: bandwidth fixes (Render Hobby = 5 GB a month). Run: python tests/test_v1_20_67.py"""
import os, sys, asyncio, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_ws_live import FakeWS, M

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def _run(open_, run_for):
    old = (M.runtime_token, M.market_status, M._start_stream)
    M.runtime_token = None                                   # no pushes of bars/analysis, only the tick loop
    M.market_status = lambda *a, **k: {"open": open_}
    M._start_stream = lambda *a, **k: None
    try:
        ws = FakeWS([], run_for=run_for)
        asyncio.run(M.live_socket(ws, "NIFTY"))
    finally:
        M.runtime_token, M.market_status, M._start_stream = old
    return [m for m in ws.out if m["type"] == "ticks"]


def test_full_tick_snapshot_is_not_sent_every_second():
    if M is None:
        return
    assert len(_run(False, 3.2)) == 1                        # market closed: one snapshot, then once a minute
    assert len(_run(True, 3.2)) == 1                         # market open: full snapshot every 5 s (moves still go out at once)


def test_hidden_tabs_report_hidden():
    js = read("static/issues.js")
    head = js[:js.index("v1.20.49")]
    assert 'Object.defineProperty(document, "visibilityState"' in head and "__oiSetTabActive" in head
    assert "oiActive" in read("static/mobile.js")
    assert re.search(r"liveHiddenTimer=setTimeout", read("static/app.js"))
    assert "hiddenTimer = setTimeout" in read("static/chart.js")


def test_closed_market_mode_v1_20_68():
    js = read("static/issues.js")
    assert "__oiMarketOpen" in js and "__oiClosedWait" in js
    assert "keepLive" in read("static/mobile.js")                         # Terminal stays live behind other tabs in market hours
    for f in ("static/app.js", "static/chart.js", "static/dashboard.js", "static/signals.js"):
        assert "__oiClosedWait" in read(f), f
    assert M is None or (M.WS_CLOSED_PUSH_SEC >= 60 and M.WS_FULL_TICKS_CLOSED_SEC >= 60)


def test_signal_change_is_never_delayed_v1_20_69():
    src = read("main.py")
    assert 'sig_key == sent.get("an_sig")' in src                        # throttle only while the signal is unchanged


def test_selected_contract_every_tick_others_each_second_v1_20_70():
    if M is None:
        return
    cur = {"interval": 5, "mode": "standard", "score_min": 70, "reconfirm": -1, "view": "index"}
    assert M._ws_sub({"opt_key": "NSE_FO|12345"}, cur)["opt_key"] == "NSE_FO|12345"
    assert M._ws_sub({"opt_key": ""}, cur)["opt_key"] is None
    assert "opt_key" not in M._ws_sub({"interval": 3}, cur)                # older pages: unchanged behaviour for the rest
    src = read("main.py")
    assert 'x.get("instrument_key") == pri' in src and M.WS_OPT_OTHERS_SEC <= 1.0
    assert "opt_key:(paperSelected" in read("static/app.js") and "opt_key: OPT ? OPT.key" in read("static/chart.js")


if __name__ == "__main__":
    test_full_tick_snapshot_is_not_sent_every_second(); test_hidden_tabs_report_hidden(); test_closed_market_mode_v1_20_68(); print("all v1.20.67 tests passed")
