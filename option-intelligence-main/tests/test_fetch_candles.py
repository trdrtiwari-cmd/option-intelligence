"""fetch_candles (v1.20.55): a long history request that leaves out the latest session must not make the analysis
run on yesterday's picture. Run: python tests/test_fetch_candles.py   (the block is cut out of main.py, no FastAPI needed)"""
import asyncio, logging, sys, time, os
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from indicators import normalize_candles
SRC = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


class HTTPException(Exception):
    def __init__(self, status_code, detail=None): self.status_code, self.detail = status_code, detail


def bar(day, hhmm, px):
    return [f"{day}T{hhmm}:00+05:30", px, px + 5, px - 5, px + 1, 1000, 0]


def load(responses):
    """responses: function(url) -> list of rows (or an exception to raise)."""
    calls = []
    async def upstox_get(url, params=None):
        calls.append(url)
        r = responses(url)
        if isinstance(r, Exception): raise r
        return {"data": {"candles": r}}
    a = SRC.index("def _rows_to_candles(rows):"); b = SRC.index("# ---------------------------------------------------------------- index-futures volume proxy")
    ns = {"time": time, "asyncio": asyncio, "timedelta": timedelta, "HTTPException": HTTPException, "upstox_get": upstox_get, "API_V3": "V3",
          "CANDLE_CACHE": {}, "HIST_CACHE": {}, "CANDLE_CACHE_TTL": 3, "HIST_CACHE_TTL": 600, "normalize_candles": normalize_candles,
          "ist_now": lambda: __import__("datetime").datetime(2026, 11, 7, 0, 30), "logger": logging.getLogger("t")}
    exec(SRC[a:b], ns)
    return ns, calls


def test_missing_latest_session_is_filled_from_the_recent_range():
    thu, fri = "2026-11-05", "2026-11-06"
    def responses(url):
        if "/intraday/" in url: return []                                   # Saturday: no session today
        start = url.rsplit("/", 1)[1]
        if start == (date(2026, 11, 7) - timedelta(days=10)).isoformat():    # the long range is missing Friday
            return [bar(thu, "15:20", 24530), bar(thu, "15:25", 24536)]
        return [bar(fri, "15:25", 25030), bar(fri, "15:20", 24938), bar(thu, "15:25", 24536)]   # newest first, like Upstox
    ns, calls = load(responses)
    cs = asyncio.run(ns["fetch_candles"]("NSE_INDEX|Nifty 50", 5, 10))
    assert cs[-1]["time"].startswith(fri) and cs[-1]["close"] == 25031, cs[-1]
    assert [c["time"][:16] for c in cs] == [f"{thu}T15:20", f"{thu}T15:25", f"{fri}T15:20", f"{fri}T15:25"], cs
    assert len(calls) == 3, calls                                           # long range + recent range + intraday
    cs2 = asyncio.run(ns["fetch_candles"]("NSE_INDEX|Nifty 50", 5, 5))       # 5 > RECENT_DAYS too, but the recent range is cached
    assert cs2[-1]["time"].startswith(fri)
    assert sum("/intraday/" not in u for u in calls) == 3, calls            # 10-day, 4-day, 5-day; the 4-day one came from cache


def test_short_requests_make_one_history_call_and_recent_failure_is_not_fatal():
    def responses(url):
        if "/intraday/" in url: return []
        if url.endswith((date(2026, 11, 7) - timedelta(days=4)).isoformat()): return HTTPException(500, "upstream")
        return [bar("2026-11-06", "15:25", 25030)]
    ns, calls = load(responses)
    cs = asyncio.run(ns["fetch_candles"]("K", 5, 3))
    assert len(cs) == 1 and len(calls) == 2, calls                          # days <= RECENT_DAYS: no extra call
    cs = asyncio.run(ns["fetch_candles"]("K", 5, 10))                         # recent range fails -> long range still used
    assert len(cs) == 1 and cs[0]["time"].startswith("2026-11-06")
    ns2, _ = load(lambda url: HTTPException(401, "token") if "/minutes/5/" in url and "intraday" not in url and url.endswith("2026-11-03") else [])
    try:
        asyncio.run(ns2["fetch_candles"]("K", 5, 10)); assert False, "401 must propagate"
    except HTTPException as e:
        assert e.status_code == 401


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
    print("all fetch_candles tests passed")
