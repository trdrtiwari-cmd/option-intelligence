"""Regression checks for push database initialization and FCM delivery."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_push_db_initialized_before_delivery_queries():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for fn in ("_send_native_fcm", "_send_web_push"):
        start = source.index("async def " + fn)
        assert "_push_db()" in source[start:start+180]
    assert "async def _init_push_storage():" in source
    assert "CREATE TABLE IF NOT EXISTS push_devices" in source
    assert "CREATE TABLE IF NOT EXISTS notification_history" in source

def test_push_endpoint_and_version():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '@app.post("/api/push/native/register")' in source
    assert 'APP_VERSION = "1.18.4"' in source

if __name__ == "__main__":
    test_push_db_initialized_before_delivery_queries()
    test_push_endpoint_and_version()
    print("all push storage tests passed")
