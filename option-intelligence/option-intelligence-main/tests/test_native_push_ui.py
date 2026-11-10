from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_native_push_does_not_require_vapid():
    """Android native push must register through Capacitor and the server, independent of Web Push (VAPID) keys."""
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = source.index("async function registerNative(")
    block = source[start:source.index("async function registerWeb(", start)]
    assert "/api/push/native/register" in block
    assert "vapid" not in block.lower()


if __name__ == "__main__":
    test_native_push_does_not_require_vapid()
    print("native push UI regression test passed")
