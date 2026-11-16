"""v1.20.74: a silent stream no longer blocks entries when the price is a fresh REST quote; after-close ages are not reported
as problems; the chart never switches on the saved copy of the last visit."""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import decision_flow as dfl

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _func(src, name):
    """Source of one top-level function of main.py, compiled alone (main.py itself needs FastAPI + a token to import)."""
    m = re.search(rf"^def {name}\(.*?(?=^\S)", src, re.S | re.M)
    assert m, name
    return m.group(0)


def test_fresh_rest_quote_stands_in_for_a_silent_stream():
    g = dfl.freshness_gate(45, True, rest_age_s=2)
    assert g["pass"] and "silent" in g["detail"] and "quote" in g["detail"]
    assert g["label"] == dfl.freshness_gate(5, True)["label"]            # same rule name: the signal board groups by it
    assert dfl.freshness_gate(600, True, rest_age_s=dfl.MAX_REST_AGE_S)["pass"]


def test_old_or_missing_rest_quote_still_blocks():
    assert not dfl.freshness_gate(45, True)["pass"]
    assert not dfl.freshness_gate(45, True, rest_age_s=None)["pass"]
    assert not dfl.freshness_gate(45, True, rest_age_s=dfl.MAX_REST_AGE_S + 1)["pass"]
    assert not dfl.freshness_gate(45, True, rest_age_s=-5)["pass"]


def test_unchanged_cases():
    assert dfl.freshness_gate(5, True, rest_age_s=1)["detail"] == "last tick 5 s ago"
    assert dfl.freshness_gate(None, True)["pass"] and dfl.freshness_gate(45, False, rest_age_s=1) is None


def test_rest_quote_age_only_for_rest_priced_candles():
    import time
    from datetime import datetime, timezone
    ns = {"time": time, "parse_time": lambda s: datetime.fromisoformat(s)}
    exec(_func(read("main.py"), "_rest_quote_age"), ns)
    age = ns["_rest_quote_age"]
    now = 1_800_000_000.0
    iso = datetime.fromtimestamp(now - 4, timezone.utc).isoformat()
    assert age({"price_source": "REST_QUOTE", "server_time": iso}, now) == 4.0
    assert age({"price_source": "WEBSOCKET", "server_time": iso}, now) is None
    assert age({"price_source": "NONE", "server_time": iso}, now) is None
    assert age(None, now) is None and age({"price_source": "REST_QUOTE", "server_time": "bad"}, now) is None
    assert 'market["open"], _rest_quote_age(result.get("feed")))' in read("main.py")


def test_after_close_ages_are_not_handed_over_as_ages():
    ns = {}
    exec(_func(read("main.py"), "_closed_ages"), ns)
    f = ns["_closed_ages"]
    an = [{"symbol": "BANKNIFTY", "age_s": 652.6, "analysis": {"status": "ok"}}]
    fd = {"NIFTY": {"stream_running": True, "tick_age_s": 680.9, "restarts_today": []}}
    a2, f2 = f(an, fd, False)
    assert "age_s" not in a2[0] and a2[0]["age_s_after_close"] == 652.6 and "normal" in a2[0]["age_note"]
    assert "tick_age_s" not in f2["NIFTY"] and f2["NIFTY"]["tick_age_s_after_close"] == 680.9 and f2["NIFTY"]["stream_running"]
    assert an[0]["age_s"] == 652.6 and fd["NIFTY"]["tick_age_s"] == 680.9          # inputs untouched
    assert f(an, fd, True) == (an, fd)                                             # market hours: unchanged


def test_monitor_checks_accept_the_after_close_shape():
    import monitor as mon
    src = read("monitor.py")
    assert '(f.get("tick_age_s") or 0) > 60' in src and '(x.get("age_s") or 0) > 300' in src   # .get(): a missing key is fine


def test_chart_does_not_switch_on_saved_copy_or_after_close():
    js = read("static/app.js")
    body = js[js.index("function autoSwitchOption(a){"):js.index("// ---------------- v1.19.3: automatic trendlines")]
    assert body.index("a.from_saved_copy)return") < body.index("selectPaperContract(")
    assert "marketKnown&&!marketOpen" in body and body.index("marketKnown&&!marketOpen") < body.index("selectPaperContract(")
    assert "marketKnown=true" in js
    assert "o.from_saved_copy = true" in read("static/boot.js")
    assert 'if(view==="option"&&a.side&&sel.type!==a.side)add("high",`Chart shows' in js
