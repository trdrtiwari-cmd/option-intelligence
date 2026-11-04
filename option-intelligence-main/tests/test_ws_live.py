"""v1.19.6: /ws/live pushes index moves, candles and analysis in real time (needs FastAPI or the test stubs to import main)."""
import os, sys, asyncio, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import main as M
except Exception:
    M = None


class FakeWS:
    def __init__(self, inbox, run_for):
        self.inbox, self.out, self.until = list(inbox), [], time.time() + run_for

    async def accept(self):
        pass

    async def close(self, code=1000):
        pass

    async def send_json(self, obj):
        if time.time() > self.until:
            raise M.WebSocketDisconnect()
        self.out.append(obj)

    async def receive_json(self):
        if self.inbox:
            return self.inbox.pop(0)
        while time.time() <= self.until:
            await asyncio.sleep(0.05)
        raise M.WebSocketDisconnect()


def test_sub_validation():
    if M is None:
        return
    cur = {"interval": None, "mode": "standard", "score_min": 70, "reconfirm": -1, "view": "index"}
    s = M._ws_sub({"interval": 3, "mode": "scalp", "score_min": 65, "reconfirm": 0}, cur)
    assert (s["interval"], s["mode"], s["score_min"], s["reconfirm"]) == (3, "scalp", 65, 0)
    bad = M._ws_sub({"interval": 7, "mode": "x", "score_min": 50, "reconfirm": 9999}, s)
    assert (bad["interval"], bad["mode"], bad["score_min"], bad["reconfirm"]) == (3, "scalp", 65, 0)


def test_socket_pushes_bars_and_analysis():
    if M is None:
        return
    calls = {"an": 0}
    bars = [{"time": f"2026-11-04T11:0{i}:00+05:30", "open": 1, "high": 2, "low": 0.5, "close": 1.5 + i, "volume": 10} for i in range(5)]

    async def fake_candles(symbol="NIFTY", interval=5, days=5, instrument_key=""):
        return {"candles": bars, "feed": {"price_source": "WEBSOCKET"}, "volume_source": "FUT"}

    async def fake_analysis(symbol="NIFTY", interval=5, mode="standard", score_min=70, reconfirm=-1):
        calls["an"] += 1
        M._AN_CACHE[(symbol, interval, mode, score_min, reconfirm)] = (1000.0, {})
        return {"status": "ok", "score": 77, "decision": "WATCH CE"}

    old = (M.candles, M.analysis, M.runtime_token, M.market_status, M._start_stream)
    M.candles, M.analysis, M.runtime_token = fake_candles, fake_analysis, "tok"
    M.market_status = lambda *a, **k: {"open": True}
    M._start_stream = lambda *a, **k: None
    try:
        ws = FakeWS([{"type": "sub", "interval": 1, "mode": "standard", "score_min": 70, "reconfirm": 0}], run_for=2.6)
        asyncio.run(M.live_socket(ws, "NIFTY"))
    finally:
        M.candles, M.analysis, M.runtime_token, M.market_status, M._start_stream = old
    types = [m["type"] for m in ws.out]
    assert "ticks" in types
    b = [m for m in ws.out if m["type"] == "bars"]
    a = [m for m in ws.out if m["type"] == "analysis"]
    assert len(b) == 1 and len(b[0]["candles"]) == 3 and b[0]["interval"] == 1        # unchanged candles are not re-sent
    assert len(a) == 1 and a[0]["data"]["score"] == 77 and a[0]["reconfirm"] == 0      # same cached result is pushed once
    assert calls["an"] >= 2                                                             # but it keeps being refreshed
