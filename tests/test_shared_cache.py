"""Shared / stale-while-revalidate analysis cache (v1.18.8). Run: python tests/test_shared_cache.py
main.py needs FastAPI, so the cache block is cut out of main.py and run against stubs."""
import asyncio, re, time
from pathlib import Path
SRC = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def load(open_=True):
    a = SRC.index("_AN_CACHE = {}"); b = SRC.index('@app.get("/api/analysis")')
    ns = {"time": time, "asyncio": asyncio, "market_status": lambda: {"open": open_}}
    exec(SRC[a:b], ns)
    return ns


def test_coalesce_and_stale():
    async def run():
        ns = load(); calls = []
        async def fn():
            calls.append(1); await asyncio.sleep(0.05)
            return {"v": len(calls), "reconfirm": {"event": "CONFIRMED"}}
        sa = ns["shared_analysis"]
        r = await asyncio.gather(*[sa(("k",), fn) for _ in range(5)])
        assert len(calls) == 1 and all(x["v"] == 1 for x in r)             # 5 phones, one computation
        assert r[0]["reconfirm"]["event"] == "CONFIRMED" and all(x["reconfirm"]["event"] is None for x in r[1:])   # shown to one page once
        again = await sa(("k",), fn)                                         # fresh hit: no new compute, event not repeated
        assert len(calls) == 1 and again["reconfirm"]["event"] is None
        ns["_AN_CACHE"][("k",)] = (time.time() - 5, ns["_AN_CACHE"][("k",)][1])   # stale but usable: instant answer + background refresh
        t0 = time.time(); stale = await sa(("k",), fn)
        assert time.time() - t0 < 0.03 and stale["v"] == 1 and stale["cache_age_s"] >= 5
        await asyncio.sleep(0.12); assert len(calls) == 2 and ns["_AN_CACHE"][("k",)][1]["v"] == 2
        ns["_AN_CACHE"][("k",)] = (time.time() - 60, {"v": 0})               # too old while market open: must wait for fresh
        assert (await sa(("k",), fn))["v"] == 3
    asyncio.run(run())


def test_errors_not_cached_and_closed_market_serves_old():
    async def run():
        ns = load(); n = []
        async def bad():
            n.append(1); raise ValueError("boom")
        try: await ns["shared_analysis"](("e",), bad); assert False
        except ValueError: pass
        assert ("e",) not in ns["_AN_CACHE"] and ("e",) not in ns["_AN_TASKS"]
        ns2 = load(open_=False)
        async def ok(): return {"v": 1}
        ns2["_AN_CACHE"][("c",)] = (time.time() - 600, {"v": 9})
        t0 = time.time(); r = await ns2["shared_analysis"](("c",), ok)
        assert r["v"] == 9 and time.time() - t0 < 0.03                       # closed: 10-min-old result is instant
    asyncio.run(run())


def test_wiring():
    for needle in ("GZipMiddleware", "_warmer", "shared_analysis((sym", "_dashboard_summary_compute"):
        assert needle in SRC, needle


if __name__ == "__main__":
    test_coalesce_and_stale(); test_errors_not_cached_and_closed_market_serves_old(); test_wiring(); print("shared cache tests passed")
