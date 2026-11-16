"""v1.20.80: the daily and the week / month review know about the paper tests and the indicator test."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AUTH_SECRET", "test-secret-please-ignore")

import monitor as mon

ROOT = Path(__file__).resolve().parents[1]


def test_instructions_explain_paper_tests_and_indicator_test():
    for s in (mon.SYSTEM_REVIEW, mon.SYSTEM_PERIOD_REVIEW):
        assert "'paper_tests'" in s and "never bought" in s and "15" in s and "both halves" in s
        assert "Indicator test" in s and "STRICT RULES" in s and "at least 1.5 ATR" in s
    assert "6. Paper tests" in mon.SYSTEM_REVIEW and "do not suggest changing a score weight" in mon.SYSTEM_REVIEW
    assert "7. Indicator test" in mon.SYSTEM_PERIOD_REVIEW and "two different 30-day runs" in mon.SYSTEM_PERIOD_REVIEW
    assert "'combined'" in mon.SYSTEM_PERIOD_REVIEW


def test_paper_brief_is_compact_and_never_raises(monkeypatch):
    import main as M

    async def fake(sym="NIFTY", days=30):
        return {"tests": [{"strategy": "macd_loose", "label": "MACD", "short": "MACD turn", "on": True, "all": {"n": 2, "win_rate": 50.0},
                           "first": None, "second": None, "verdict": "collecting", "trades": [{"day": "d", "bar_time": str(i)} for i in range(30)],
                           "secret_field": 1},
                          {"strategy": "early_trend", "label": "Early", "on": False, "all": {"n": 0}, "verdict": "collecting", "trades": []}]}
    monkeypatch.setattr(M, "_paper_results", fake)
    out = asyncio.run(M._paper_brief())
    assert [t["label"] for t in out] == ["MACD", "Early"] and len(out[0]["trades"]) == 12 and "secret_field" not in out[0]
    assert out[1]["on"] is False and out[1]["all"] == {"n": 0}

    async def boom(sym="NIFTY", days=30):
        raise RuntimeError("no candles")
    monkeypatch.setattr(M, "_paper_results", boom)
    assert asyncio.run(M._paper_brief()) == [{"error": "RuntimeError: no candles"}]


def test_daily_review_carries_the_paper_tests():
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    assert m.count('ctx["paper_tests"] = await _paper_brief()') == 2          # the review itself and the Download-for-Claude day file
    assert 'ctx["paper_tests"] = [{k: t_.get(k)' in m                         # week / month file as before
