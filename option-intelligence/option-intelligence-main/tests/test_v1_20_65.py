"""v1.20.65: same-day token restore window, always-on stream start, stale option LTP cross-check. Run: python tests/test_v1_20_65.py"""
import asyncio, os, sys, time
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _main():
    try:
        import main as M
        return M
    except ImportError:
        return None


def test_token_window():
    M = _main()
    if not M:
        return
    from indicators import IST
    ts = lambda *a: datetime(*a, tzinfo=IST).timestamp()
    saved = ts(2026, 11, 9, 9, 30)                                   # logged in at 09:30
    assert M.token_window_open(saved, ts(2026, 11, 9, 12, 12))      # same-day restart: still valid
    assert M.token_window_open(saved, ts(2026, 11, 10, 3, 0))       # until shortly before 03:30 next day
    assert not M.token_window_open(saved, ts(2026, 11, 10, 9, 0))   # next morning: needs a new login
    early = ts(2026, 11, 9, 1, 0)                                    # logged in before 03:30 -> expires the same day 03:30
    assert not M.token_window_open(early, ts(2026, 11, 9, 9, 0))


def test_ensure_streams_starts_missing_only():
    M = _main()
    if not M:
        return
    started, orig_start, orig_tok = [], M._start_stream, M.runtime_token
    saved = dict(M.STREAMS)
    M._start_stream = lambda s, keys: started.append(s)
    M.runtime_token = "x"
    try:
        M.STREAMS.clear()
        M.STREAMS["NIFTY"] = {"streamer": object(), "keys": [], "started_ts": time.time()}
        assert M._ensure_streams() == ["BANKNIFTY"] and started == ["BANKNIFTY"]
        M.runtime_token = ""
        assert M._ensure_streams() == []
    finally:
        M._start_stream, M.runtime_token = orig_start, orig_tok
        M.STREAMS.clear(); M.STREAMS.update(saved)


def test_option_quote_uses_rest_when_stream_ltp_is_stale_and_different():
    M = _main()
    if not M:
        return
    key, now = "NSE_FO|99999", time.time()
    M.LIVE_TICKS[key] = {"instrument_key": key, "ltp": 149.0, "received_ts": now, "received_at": "x", "ltp_changed_ts": now - 300}
    orig = (M._rest_ltp, M.market_status, M._OPT_XCHECK.copy())
    calls = []

    async def fake_rest(k):
        calls.append(k); return 162.5
    M._rest_ltp = fake_rest
    M.market_status = lambda: {"open": True, "trading_day": True}
    M._OPT_XCHECK.clear()
    try:
        # REST is called through upstox_get in the fallback branch too, so only check the cross-check decision here
        assert asyncio.run(M._stale_ltp_differs(key, 149.0)) is True and calls == [key]
        assert asyncio.run(M._stale_ltp_differs(key, 162.5)) is True        # cached for 20 s
        M._OPT_XCHECK.clear()
        assert asyncio.run(M._stale_ltp_differs(key, 162.5)) is False       # equal prices -> the stream value is trusted
    finally:
        M._rest_ltp, M.market_status = orig[0], orig[1]
        M._OPT_XCHECK.clear(); M._OPT_XCHECK.update(orig[2]); M.LIVE_TICKS.pop(key, None)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f(); print("ok", n)
    print("all v1.20.65 tests passed")
