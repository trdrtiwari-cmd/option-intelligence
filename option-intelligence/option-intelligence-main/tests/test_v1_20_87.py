from pathlib import Path
M = Path(__file__).resolve().parents[1] / "mobile" / "android-patch.mjs"

def test_app_asks_storage_and_reports_result():
    s = M.read_text(encoding="utf-8")
    assert "WRITE_EXTERNAL_STORAGE" in s and 'android:maxSdkVersion="28"' in s
    assert "requestPermissions" in s and "onRequestPermissionsResult" in s
    assert "ACTION_DOWNLOAD_COMPLETE" in s and "Saved: Downloads/OptionIntelligence/" in s
