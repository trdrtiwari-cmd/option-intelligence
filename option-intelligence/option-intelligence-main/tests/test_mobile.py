"""Mobile app shell (v1.17): manifest, icons, service worker, public paths and page wiring. Run: python tests/test_mobile.py"""
import glob, json, os, re, struct, sys
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
os.environ.setdefault("SQLITE_PATH", os.path.join(os.environ.get("TMPDIR", "/tmp"), "oi_mobile_test.db"))
import accounts as acc


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def png_size(path):
    with open(os.path.join(ROOT, path), "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


def test_manifest_and_icons():
    m = json.loads(read("static/manifest.webmanifest"))
    assert m["display"] == "standalone" and m["start_url"].startswith("/") and m["scope"] == "/"
    purposes = {i["purpose"] for i in m["icons"]}
    assert {"any", "maskable"} <= purposes
    for i in m["icons"]:
        w, h = map(int, i["sizes"].split("x"))
        assert png_size(i["src"].lstrip("/")) == (w, h), i


def test_public_paths_cover_shell():
    for p in ("/sw.js", "/static/manifest.webmanifest", "/static/offline.html", "/static/mobile.css", "/static/mobile.js",
              "/static/icons/icon-192.png", "/static/icons/apple-touch-icon.png", "/favicon.ico"):
        assert p in acc.PUBLIC_PATHS, p
    assert "/api/userdata" not in acc.PUBLIC_PATHS and "/static/app.js" not in acc.PUBLIC_PATHS   # app code and data stay behind login


def test_service_worker_never_caches_live_data():
    sw = read("static/sw.js")
    assert "__VERSION__" in sw and '"/api/"' in sw and '"/ws/"' in sw
    assert "startsWith(\"/api/\")" in sw


def test_every_page_is_wired():
    for f in glob.glob(os.path.join(ROOT, "static", "*.html")):
        name = os.path.basename(f)
        if name == "offline.html":
            continue
        h = open(f, encoding="utf-8").read()
        for needle in ("viewport-fit=cover", "manifest.webmanifest", "mobile.css", "mobile.js", "apple-touch-icon"):
            assert needle in h, f"{name} is missing {needle}"
    for name in ("index", "dashboard", "diagnostics", "signals", "account"):
        assert "sync.js" in read(f"static/{name}.html"), f"{name}.html must load sync.js so settings are saved to the account"


def test_settings_keys_are_synced():
    sync = read("static/sync.js")
    for k in ("oi-settings-v1", "oi-score-min", "oi-reconfirm", "oi-alert-sound", "oi-sig-qty", "oi-sig-delta"):
        assert f'"{k}"' in sync, k
    assert len(re.findall(r'^\s+"[a-z0-9-]+": "[a-z0-9-]+",?\s*(//.*)?$', sync, re.M)) < acc.MAX_KEYS_PER_USER


def test_prefs_controls_exist_in_pages():
    prefs = read("static/prefs.js")
    block = re.search(r"const FIELDS = \{(.*?)\n  \};", prefs, re.S).group(1)
    pages = {"terminal": "index", "dashboard": "dashboard", "diagnostics": "diagnostics", "signals": "signals"}
    for page, ids in re.findall(r"(\w+): \[(.*?)\]", block, re.S):
        html = read(f"static/{pages[page]}.html")
        for i in re.findall(r'"(\w+)"', ids):
            assert f'id="{i}"' in html, f"{page}: control #{i} not found"


if __name__ == "__main__":
    for n, fn in list(globals().items()):
        if n.startswith("test_"):
            fn(); print("ok", n)
    print("all mobile tests passed")
