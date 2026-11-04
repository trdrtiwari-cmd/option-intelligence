"""v1.19.3 automatic trendlines (static/trendlines.js). Runs the JS check with node when node is installed."""
import os, shutil, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_trendlines_up_down_and_break():
    if not shutil.which("node"):
        return
    out = subprocess.run(["node", os.path.join(ROOT, "tests", "trendlines_check.js"), os.path.join(ROOT, "static", "trendlines.js")],
                         capture_output=True, text=True, timeout=30).stdout
    assert "UP support" in out and "dir: 'rising'" in out
    assert "DOWN resistance" in out and "dir: 'falling'" in out
    assert "BREAK support { dir: 'rising', broken: true }" in out
    assert '"support":null' in out          # too few candles -> no line
    import json, re
    day = json.loads(re.search(r"DAY (\{.*\})", out).group(1))
    assert day["sDir"] == "rising" and day["sFrom"] >= 70          # support starts at the afternoon swing low
    assert day["rFrom"] is None or day["rFrom"] >= 70               # never from the morning top across the V
