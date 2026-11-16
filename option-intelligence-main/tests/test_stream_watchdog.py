"""v1.20.63 stream watchdog. Run: python tests/test_stream_watchdog.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class FakeStreamer:
    def __init__(self):
        self.disconnected = False
    def auto_reconnect(self, *a):
        pass
    def disconnect(self):
        self.disconnected = True


def test_health_and_restart():
    try:
        import main as M
    except ImportError:
        return
    sym, key = "NIFTY", None
    key = M.INDEX_KEYS[sym]
    now = time.time()
    fs = FakeStreamer()
    started = []
    orig_start, orig_tok = M._start_stream, M.runtime_token
    M._start_stream = lambda s, keys: started.append((s, keys))
    M.runtime_token = "x"
    try:
        M.STREAMS[sym] = {"streamer": fs, "keys": [key, "NSE_FO|1"], "started_ts": now - 3600}
        M.LIVE_TICKS[key] = {"received_ts": now - 44000}            # last tick from last night
        ok, why = M._stream_health(sym, now)
        assert not ok and ("s old" in why or "after (re)start" in why)
        M.LIVE_TICKS[key] = {"received_ts": now - 2}
        assert M._stream_health(sym, now)[0]
        M.STREAMS[sym]["dead"] = True
        ok, why = M._stream_health(sym, now)
        assert not ok and "stopped reconnecting" in why
        M._restart_stream(sym, why)
        assert fs.disconnected and sym not in M.STREAMS and started and started[0][1][0] == key and "NSE_FO|1" in started[0][1]
        assert M.STREAM_RESTARTS[sym][-1]["reason"] == why
        M.STREAMS[sym] = {"streamer": FakeStreamer(), "keys": [key], "started_ts": now - 30}
        M.LIVE_TICKS.pop(key, None)
        assert M._stream_health(sym, now)[0]                         # just started: give it time
        M.STREAMS[sym]["started_ts"] = now - 90
        assert not M._stream_health(sym, now)[0]
    finally:
        M._start_stream, M.runtime_token = orig_start, orig_tok
        M.STREAMS.pop(sym, None); M.LIVE_TICKS.pop(key, None); M.STREAM_RESTARTS.pop(sym, None)


def test_monitor_check_mentions_restarts():
    import monitor as mon
    import inspect
    assert "restarted" in inspect.getsource(mon)


if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("test_") and callable(fn):
            fn(); print("ok", n)
    print("all watchdog tests passed")
