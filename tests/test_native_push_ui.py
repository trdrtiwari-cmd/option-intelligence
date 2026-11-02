from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_native_push_does_not_require_vapid():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = source.index("// v1.18.5: choose the notification transport")
    block = source[start:]
    assert 'if(runtime.native)' in block
    assert 'if(!cfg.native_push)' in block
    assert 'if(!cfg.web_push||!cfg.vapid_public_key)' in block
    assert 'Android native push' in block
    assert 'Configure VAPID keys for Web Push and Firebase service account for Android' not in block

if __name__ == "__main__":
    test_native_push_does_not_require_vapid()
    print("native push UI regression test passed")
