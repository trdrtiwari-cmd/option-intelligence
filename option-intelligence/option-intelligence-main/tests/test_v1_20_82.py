"""v1.20.82: shared intraday candles (Upstox limit), a database that can sleep (Neon), and one-time download links for the app."""
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AUTH_SECRET", "test-secret-please-ignore")

import main as M
import accounts as acc

IST = M.IST
ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def clean():
    for d in (M.INTRA_CACHE, M.CANDLE_CACHE, M.HIST_CACHE, M._RL_CALLS, M._RL_COOL, M._DL):
        d.clear()
    yield
    for d in (M.INTRA_CACHE, M.CANDLE_CACHE, M.HIST_CACHE, M._RL_CALLS, M._RL_COOL, M._DL):
        d.clear()


def _ts(h, m, s):
    return datetime(2026, 11, 16, h, m, s, tzinfo=IST).timestamp()


# ---------------------------------------------------------------- intraday candles
def test_one_intraday_call_for_every_days_value(monkeypatch):
    calls = []

    async def fake(url, params=None):
        calls.append(url)
        if "/intraday/" in url:
            return {"data": {"candles": [["2026-11-16T09:15:00+05:30", 1, 2, 0.5, 1.5, 10, 0]]}}
        return {"data": {"candles": [["2026-11-13T15:25:00+05:30", 1, 2, 0.5, 1.5, 10, 0]]}}
    monkeypatch.setattr(M, "upstox_get", fake)
    monkeypatch.setattr(M, "_intra_ttl", lambda interval, now=None: 10.0)

    async def run():
        for days in (3, 5, 6, 10):
            out = await M.fetch_candles("NSE_INDEX|Nifty 50", 5, days)
            assert len(out) == 2
    asyncio.run(run())
    intra = [u for u in calls if "/intraday/" in u]
    assert len(intra) == 1                                   # was 4 before v1.20.82
    assert len([u for u in calls if "/intraday/" not in u]) == 4      # history differs per `days` (cached 10 min)


def test_intraday_ttl_is_short_after_a_bar_close_and_longer_mid_bar():
    assert M._intra_ttl(5, _ts(10, 5, 10)) == M.INTRA_FAST_SEC          # 10 s after the 10:05 bar closed
    assert M._intra_ttl(5, _ts(10, 7, 0)) == M.INTRA_SLOW_SEC           # mid-bar
    assert M._intra_ttl(15, _ts(10, 7, 0)) == M.INTRA_HTF_SEC           # higher timeframe mid-bar
    assert M._intra_ttl(15, _ts(10, 15, 5)) == M.INTRA_FAST_SEC


def test_intraday_slows_down_near_the_upstox_limit():
    import collections
    now = _ts(10, 5, 10)
    M._RL_CALLS["/v3/historical-candle/intraday"] = collections.deque([now - 100] * 900)
    assert M._intra_ttl(5, now) == 60.0
    M._RL_CALLS["/v3/historical-candle/intraday"] = collections.deque([now - 100] * 960)
    assert M._intra_ttl(5, now) == 120.0
    M._RL_CALLS["/v3/historical-candle/intraday"] = collections.deque([now - 100] * 720)
    assert M._intra_ttl(5, now) == 20.0


# ---------------------------------------------------------------- database sleeps
def test_heartbeat_only_in_market_hours_every_10_min():
    src = read("main.py")
    assert "time.time() - last_beat > HEARTBEAT_SEC and in_session(grace_minutes=5)" in src and M.HEARTBEAT_SEC == 1800


def test_gap_counts_from_todays_open_not_from_last_evening(monkeypatch):
    monkeypatch.setattr(M, "_session_for", lambda dt: {"open_day": True, "start": datetime(2026, 1, 1, 9, 15).time(),
                                                       "end": datetime(2026, 1, 1, 15, 30).time()})
    now = datetime(2026, 11, 16, 9, 40, tzinfo=IST)
    last_evening = datetime(2026, 11, 13, 15, 29, tzinfo=IST)
    assert M._gap_start(last_evening, now) == datetime(2026, 11, 16, 9, 15, tzinfo=IST)
    this_morning = datetime(2026, 11, 16, 9, 30, tzinfo=IST)
    assert M._gap_start(this_morning, now) == this_morning
    monkeypatch.setattr(M, "_session_for", lambda dt: {"open_day": False})
    assert M._gap_start(last_evening, now) is None


def test_backup_is_throttled_but_signals_and_exits_are_copied_at_once():
    src = read("main.py")
    assert "(_SL_URGENT[0] or not is_open or time.time() - _SL_LAST_MIRROR[0] >= MIRROR_GAP_SEC)" in src
    assert src.count("_SL_URGENT[0] = True") == 4                    # new signal + three exit paths
    assert M.MIRROR_GAP_SEC == 1800


def test_login_check_cached_for_5_minutes():
    assert acc.CACHE_TTL == 900


# ---------------------------------------------------------------- download links
class _Req:
    def __init__(self, body):
        self._b = body
        self.scope = {"oi_user": {"id": 1, "username": "owner", "is_admin": True}}

    async def json(self):
        return self._b


def test_download_link_roundtrip_and_limits(monkeypatch):
    monkeypatch.setattr(M, "_need_user", lambda request: {"id": 1})
    d = asyncio.run(M.dl_create(_Req({"name": "strategy review 2026-11-16.json", "mime": "application/json", "text": '{"a":1}'})))
    assert d["url"].startswith("/dl/") and d["url"].endswith("/strategyreview2026-11-16.json") and d["expires_in"] == 900
    tok = d["url"].split("/")[2]
    assert len(tok) >= 30
    r = asyncio.run(M.dl_get(tok, "x"))
    assert r.status_code == 200 and r.body == b'{"a":1}' and "attachment" in r.headers["content-disposition"]
    for _ in range(M.DL_MAX_USES - 1):
        asyncio.run(M.dl_get(tok, "x"))
    assert asyncio.run(M.dl_get(tok, "x")).status_code == 404               # used up
    d2 = asyncio.run(M.dl_create(_Req({"name": "a.bin", "b64": "AAEC"})))
    tok2 = d2["url"].split("/")[2]
    M._DL[tok2]["expires"] = time.time() - 1
    assert asyncio.run(M.dl_get(tok2, "a.bin")).status_code == 404           # expired
    with pytest.raises(M.HTTPException):
        asyncio.run(M.dl_create(_Req({"name": "e.txt", "text": ""})))


def test_only_get_on_dl_is_public():
    assert acc._public("/dl/abc/x.json", "GET") and not acc._public("/dl/abc/x.json", "POST")
    assert not acc._public("/api/dl", "POST") and acc._public("/health")


def test_page_offers_the_chrome_link_in_the_app():
    js = read("static/savefile.js")
    assert 'fetch("/api/dl"' in js and "Copy download link" in js and "if (native() && !nativeDownloads())" in js
    assert "return window.oiSave(name ||" in js
    assert "if (window.oiSave) { msg((await window.oiSave(name, text, 'application/json'))" in read("static/signals.js")
