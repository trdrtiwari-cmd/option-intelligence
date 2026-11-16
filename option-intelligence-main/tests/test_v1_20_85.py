from pathlib import Path
S = Path(__file__).resolve().parents[1] / "static"

def test_chart_stays_on_index_when_closed():
    c = (S / "chart.js").read_text(encoding="utf-8")
    assert 'api("/api/status")' in c and "mktOpen === false" in c and "auto: true" in c

def test_monitor_buttons_show_toast():
    s = (S / "signals.js").read_text(encoding="utf-8")
    assert "function monToast" in s and s.count("monToast(") >= 5

def test_app_download_keeps_copy_link():
    f = (S / "savefile.js").read_text(encoding="utf-8")
    assert "LAST_LINK" in f
