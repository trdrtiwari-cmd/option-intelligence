"""Version consistency. Run: python tests/test_versions.py
The 'page is out of date' banner compares FRONTEND_VERSION (app.js) with the server's APP_VERSION; every ?v= in the HTML must match too."""
import glob, os, re
ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def test_versions_agree():
    server = re.search(r'APP_VERSION\s*=\s*"([\d.]+)"', read("main.py")).group(1)
    front = re.search(r'FRONTEND_VERSION\s*=\s*"([\d.]+)"', read("static/app.js")).group(1)
    assert front == server, f"static/app.js says {front} but main.py says {server} -> permanent 'page is out of date' banner"
    for f in glob.glob(os.path.join(ROOT, "static", "*.html")):
        for v in re.findall(r"\?v=([\d.]+)", open(f, encoding="utf-8").read()):
            assert v == server, f"{os.path.basename(f)} references ?v={v}, server is {server}"


if __name__ == "__main__":
    test_versions_agree(); print("ok test_versions_agree\nall version tests passed")


def test_version_is_116():
    assert re.search(r'APP_VERSION\s*=\s*"([\d.]+)"', read("main.py")).group(1) == "1.11.6"
