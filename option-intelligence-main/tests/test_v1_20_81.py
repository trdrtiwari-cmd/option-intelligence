"""v1.20.81: Upstox calls are counted per API, kept under the per-second limit, and an API is paused after a 429 (never retried at once)."""
import asyncio
import collections
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AUTH_SECRET", "test-secret-please-ignore")

import main as M
import monitor as mon
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def clean():
    M._RL_CALLS.clear(); M._RL_COOL.clear(); M._RL_429.clear()
    yield
    M._RL_CALLS.clear(); M._RL_COOL.clear(); M._RL_429.clear()


def test_api_families():
    f = M.rl_family
    assert f("https://api.upstox.com/v3/historical-candle/NSE_INDEX|Nifty 50/minutes/5/2026-11-13/2026-11-01") == "/v3/historical-candle"
    assert f("https://api.upstox.com/v3/historical-candle/intraday/NSE_INDEX|Nifty 50/minutes/5") == "/v3/historical-candle/intraday"
    assert f("https://api.upstox.com/v2/option/chain") == "/v2/option/chain"
    assert f("https://api.upstox.com/v2/market-quote/ltp/") == "/v2/market-quote/ltp"


def test_limits_default_and_env(monkeypatch):
    assert M._rl_limits() == {1: 25, 60: 250, 1800: 1000}
    monkeypatch.setenv("UPSTOX_RATE_LIMITS", "50,500,2000")
    assert M._rl_limits() == {1: 50, 60: 500, 1800: 2000}
    monkeypatch.setenv("UPSTOX_RATE_LIMITS", "junk")
    assert M._rl_limits() == {1: 25, 60: 250, 1800: 1000}


def test_counts_and_share():
    now = time.time()
    M._RL_CALLS["/v2/option/chain"] = collections.deque([now - 1700] * 10 + [now - 30] * 100 + [now - 0.2] * 5)
    c = M.rl_counts(now)["/v2/option/chain"]
    assert (c["last_sec"], c["last_min"], c["last_30min"]) == (5, 105, 115)
    assert c["peak_share_pct"] == round(100 * 105 / 250, 1)


def test_per_second_share_is_kept():
    async def burst():
        t0 = time.time()
        for _ in range(int(M.RL_LIMITS[1] * M.RL_SEC_SHARE) + 3):
            await M._rl_take("/x")
        return time.time() - t0
    took = asyncio.run(burst())
    assert took >= 0.5                                                   # the extra calls waited for the next second
    assert M.rl_counts()["/x"]["last_30min"] == int(M.RL_LIMITS[1] * M.RL_SEC_SHARE) + 3


class _Resp:
    def __init__(self, code, headers=None):
        self.status_code, self.headers, self.text = code, headers or {}, "x"

    def json(self):
        return {"status": "error"}


class _Client:
    def __init__(self, codes):
        self.codes, self.calls = list(codes), 0

    async def get(self, url, params=None, headers=None):
        self.calls += 1
        return _Resp(self.codes.pop(0), {"Retry-After": "45"})


def test_429_is_not_retried_and_pauses_the_api(monkeypatch):
    cl = _Client([429, 200])
    monkeypatch.setattr(M, "_upstox_client", lambda: cl)
    url = "https://api.upstox.com/v2/option/chain"
    with pytest.raises(HTTPException) as e:
        asyncio.run(M._upstox_get_once(url, {}, "tok"))
    assert e.value.status_code == 429 and cl.calls == 1                      # one request only
    assert 40 < M._RL_COOL["/v2/option/chain"] - time.time() <= 45            # Retry-After honoured
    with pytest.raises(HTTPException) as e2:                                  # while paused: refused without calling Upstox
        asyncio.run(M._upstox_get_once(url, {}, "tok"))
    assert e2.value.status_code == 429 and cl.calls == 1 and "rate limit" in str(e2.value.detail)
    assert M.rl_counts()["/v2/option/chain"]["rate_limited_today"] == 1
    asyncio.run(M._upstox_get_once("https://api.upstox.com/v2/market-quote/ltp", {}, "tok"))   # other APIs are not paused
    assert cl.calls == 2


def test_server_errors_still_retried(monkeypatch):
    cl = _Client([503, 200])
    monkeypatch.setattr(M, "_upstox_client", lambda: cl)
    assert asyncio.run(M._upstox_get_once("https://api.upstox.com/v2/option/chain", {}, "tok")) == {"status": "error"}
    assert cl.calls == 2


def test_review_warns_about_limits():
    base = {"terminal": {"settings": {}, "upstox_usage": {"limits": {"per_min": 250, "per_30min": 1000}, "by_api": {
        "/v2/option/chain": {"peak_share_pct": 85.0, "last_min": 212, "last_30min": 700, "rate_limited_today": 0},
        "/v2/market-quote/ltp": {"peak_share_pct": 99.0, "last_min": 10, "last_30min": 990, "rate_limited_today": 2},
        "/v2/option/contract": {"peak_share_pct": 5.0, "last_min": 1, "last_30min": 9, "rate_limited_today": 0}}}}}
    out = " | ".join(mon.consistency_checks(base))
    assert "UPSTOX USAGE HIGH: /v2/option/chain is at 85%" in out
    assert "UPSTOX RATE LIMIT: /v2/market-quote/ltp" in out and "2 time(s)" in out
    assert "/v2/option/contract" not in out


def test_usage_endpoint_needs_login():
    import accounts as acc
    assert "/api/upstox/usage" not in acc.PUBLIC_PATHS
    d = asyncio.run(M.upstox_usage())
    assert d["limits"] == {"per_sec": M.RL_LIMITS[1], "per_min": M.RL_LIMITS[60], "per_30min": M.RL_LIMITS[1800]} and "by_api" in d
