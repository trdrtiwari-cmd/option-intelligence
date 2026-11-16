from pathlib import Path
S = Path(__file__).resolve().parents[1] / "static"

def test_chart_waits_for_market_state():
    c = (S / "chart.js").read_text(encoding="utf-8")
    assert "if (mktOpen === null) return;" in c and 'mktOpen === false && OPT && OPT.auto && S.view === "auto"' in c

def test_export_message_counts_seconds():
    s = (S / "signals.js").read_text(encoding="utf-8")
    assert "setInterval(tick, 5000)" in s and s.count("clearInterval(timer)") >= 3
