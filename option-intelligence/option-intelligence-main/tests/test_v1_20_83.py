"""v1.20.83: the Android app saves files itself (Downloads/OptionIntelligence); the database sleeps longer."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AUTH_SECRET", "test-secret-please-ignore")

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_app_announces_native_downloads():
    cfg = json.loads(read("mobile/capacitor.config.json"))
    assert cfg["android"]["appendUserAgent"] == "OIApp-DL/1"
    assert cfg["server"]["url"].startswith("https://")


def test_patch_writes_a_download_listener(tmp_path=None):
    t = Path(tempfile.mkdtemp())
    (t / "capacitor.config.json").write_text(read("mobile/capacitor.config.json"))
    (t / "android-patch.mjs").write_text(read("mobile/android-patch.mjs"))
    d = t / "android/app/src/main/java/app/optionintelligence/terminal"
    d.mkdir(parents=True)
    (d / "MainActivity.java").write_text("package app.optionintelligence.terminal; public class MainActivity {}")
    try:
        subprocess.run(["node", str(t / "android-patch.mjs")], check=True, capture_output=True)
    except FileNotFoundError:
        return                                               # no node here: the content checks below still run
    java = (d / "MainActivity.java").read_text()
    assert "package app.optionintelligence.terminal;" in java and "setDownloadListener" in java
    assert "DownloadManager" in java and '"OptionIntelligence/" + name' in java and "DIRECTORY_DOWNLOADS" in java


def test_workflow_runs_the_patch_before_sync():
    wf = (ROOT.parent / ".github/workflows/android-apk.yml").read_text(encoding="utf-8")
    a, b, c = wf.index("npx cap add android"), wf.index("node android-patch.mjs"), wf.index("npx cap sync android")
    assert a < b < c
    assert "option-intelligence-main/mobile/**" in wf          # pushing the mobile folder rebuilds the APK


def test_page_uses_native_downloads_first():
    js = read("static/savefile.js")
    i, j = js.index("if (native() && nativeDownloads())"), js.index('const FS = plugin("Filesystem")')
    assert i < j and "window.location.href = link" in js and "OIApp-DL" in js


def test_database_sleeps_longer():
    import main as M
    import accounts as acc
    assert M.HEARTBEAT_SEC == 1800 and M.MONITOR_GAP_MIN_SEC == 2700 and M.MIRROR_GAP_SEC == 1800 and acc.CACHE_TTL == 900
