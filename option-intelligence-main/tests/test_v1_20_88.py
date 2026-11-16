import asyncio, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def test_apk_tag_and_banner():
    cfg = json.loads((ROOT / "mobile" / "capacitor.config.json").read_text())
    assert "OIApp-APK/2" in cfg["android"]["appendUserAgent"] and "OIApp-DL/1" in cfg["android"]["appendUserAgent"]
    js = (ROOT / "static" / "mobile.js").read_text(encoding="utf-8")
    assert "/api/app/apk" in js and "apkCheck" in js and "setInterval" not in js.split("Check for app update")[-1]
    assert 'id="apkPanel" hidden' in (ROOT / "static" / "account.html").read_text(encoding="utf-8")
    wf = (ROOT.parent / ".github" / "workflows" / "android-apk.yml").read_text(encoding="utf-8")
    assert "apk-version.json --target" in wf


def test_apk_info_endpoint(monkeypatch):
    import main

    class R:
        status_code = 200
        def json(self): return {"build": 3}

    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): assert url.endswith("/apk-version.json"); return R()

    monkeypatch.setattr(main.httpx, "AsyncClient", C)
    main._APK_INFO.update(at=0.0, data=None)
    d = asyncio.run(main.app_apk_info())
    assert d["build"] == 3 and d["url"].endswith("/OptionIntelligence.apk")
    main._APK_INFO.update(at=0.0, data=None)
