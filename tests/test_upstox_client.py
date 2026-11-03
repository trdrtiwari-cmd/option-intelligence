"""Upstox call layer (v1.18.11): shared client, in-flight de-duplication, retry on 429, graceful timeout.
Run: python tests/test_upstox_client.py   (main.py needs FastAPI, so the block is cut out and run against a fake httpx)"""
import asyncio, types
from pathlib import Path
SRC = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


class HTTPException(Exception):
    def __init__(self, status_code, detail=None): self.status_code, self.detail = status_code, detail


TimeoutException = type("TimeoutException", (Exception,), {})
TransportError = type("TransportError", (Exception,), {})


def load(script):
    fh = types.SimpleNamespace(TimeoutException=TimeoutException, TransportError=TransportError,
                               Timeout=lambda *a, **k: None, Limits=lambda **k: None)
    calls = []
    class Resp:
        def __init__(self, code, body): self.status_code, self._b, self.text = code, body, str(body)
        def json(self): return self._b
    class Client:
        is_closed = False
        async def get(self, url, params=None, headers=None, timeout=None):
            calls.append(url); await asyncio.sleep(0.02)
            step = script.pop(0) if script else (200, {"ok": True})
            if isinstance(step, Exception): raise step
            return Resp(*step)
    fh.AsyncClient = lambda **k: Client()
    a = SRC.index("_UPSTOX_CLIENT = None"); b = SRC.index("async def check_token_state():")
    ns = {"httpx": fh, "asyncio": asyncio, "os": __import__("os"), "HTTPException": HTTPException, "token_or_401": lambda: "tok"}
    exec(SRC[a:b], ns)
    return ns, calls, fh


def test_identical_calls_share_one_request_and_results_are_independent():
    async def run():
        ns, calls, _ = load([])
        rs = await asyncio.gather(*[ns["upstox_get"]("https://x/candles", {"a": 1}) for _ in range(6)])
        assert len(calls) == 1 and all(r == {"ok": True} for r in rs)
        rs[0]["ok"] = False; assert rs[1]["ok"] is True                     # callers cannot corrupt each other
        await ns["upstox_get"]("https://x/other"); assert len(calls) == 2
    asyncio.run(run())


def test_rate_limit_is_retried_then_succeeds():
    async def run():
        ns, calls, _ = load([(429, {"e": 1}), (200, {"data": 5})])
        assert await ns["upstox_get"]("https://x/q") == {"data": 5} and len(calls) == 2
    asyncio.run(run())


def test_timeout_becomes_clean_504_and_errors_keep_status():
    async def run():
        ns2, calls2, fh2 = load([TimeoutException("t")] * 3)
        try: await ns2["upstox_get"]("https://x/t"); assert False
        except HTTPException as e: assert e.status_code == 504 and len(calls2) == 3
        ns3, *_ = load([(401, {"m": "bad token"})])
        try: await ns3["upstox_get"]("https://x/u"); assert False
        except HTTPException as e: assert e.status_code == 401 and "expire" in e.detail["hint"]
    asyncio.run(run())


if __name__ == "__main__":
    test_identical_calls_share_one_request_and_results_are_independent(); test_rate_limit_is_retried_then_succeeds()
    test_timeout_becomes_clean_504_and_errors_keep_status(); print("upstox client tests passed")
