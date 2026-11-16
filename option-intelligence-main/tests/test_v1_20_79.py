"""v1.20.79: the running server version is shown in the header of every page."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AUTH_SECRET", "test-secret-please-ignore")

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_health_reports_the_server_version():
    import main as M
    d = asyncio.run(M.health())
    assert d == {"ok": True, "version": M.APP_VERSION}
    import accounts as acc
    assert "/health" in acc.PUBLIC_PATHS                     # works before login too


def test_header_tag_is_added_by_the_script_every_page_loads():
    js = read("static/mobile.js")
    assert 'querySelector("header.topbar .brand")' in js and '"server v" + d.version' in js and 'cache: "no-store"' in js
    for page in ("index.html", "signals.html", "diagnostics.html", "dashboard.html", "account.html", "notifications.html", "login.html"):
        h = read("static/" + page)
        assert "/static/mobile.js" in h
        if page != "login.html":
            assert 'class="topbar"' in h and 'class="brand"' in h
