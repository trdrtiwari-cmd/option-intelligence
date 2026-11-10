import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import indicators as ind  # noqa: E402


def test_clean_tune_validates():
    assert ind.clean_tune({"rsi_ce_max": "75", "rsi_slope": "off", "junk": 1}) == {"rsi_ce_max": 75.0, "rsi_slope": False}
    with pytest.raises(ValueError):
        ind.clean_tune({"rsi_ce_min": 70, "rsi_ce_max": 65})
    with pytest.raises(ValueError):
        ind.clean_tune({"adx_min": 90})


def test_tune_layers_and_changes():
    try:
        ind.LIVE_TUNE.update({"adx_min": 25.0})
        assert ind.tune_values()["adx_min"] == 25.0
        assert ind.tune_values({"adx_min": 15.0})["adx_min"] == 15.0          # a test overrides the live value
        assert ind.tune_changes({"rsi_pe_min": 28.0}) == {"adx_min": 25.0, "rsi_pe_min": 28.0}
    finally:
        ind.LIVE_TUNE.clear()
    assert ind.tune_changes() == {}


def test_analyze_uses_the_values():
    import test_diagnostics as T
    rows = T.staircase_days(4, 1)
    base = ind.analyze(rows, "NIFTY", 5, mode="standard")
    t = ind.analyze(rows, "NIFTY", 5, mode="standard", tune={"rsi_ce_max": 75, "rsi_pe_min": 25, "adx_min": 30, "chase_atr": 2})
    assert base["rsi_band"] != t["rsi_band"] and t["rsi_band"]["CE"][1] == 75 and t["rsi_band"]["PE"][0] == 25
    assert t["tune_changes"] == {"rsi_ce_max": 75, "rsi_pe_min": 25, "adx_min": 30, "chase_atr": 2}
    labels = " ".join(g["label"] for g in t["gates"])
    if t.get("side"):
        assert "ADX ≥ 30" in labels and ("RSI > 25 for PE" in labels or "RSI < 75 for CE" in labels) and "≤ 2 ATR beyond EMA 9" in labels
    assert base["tune_changes"] == {}
