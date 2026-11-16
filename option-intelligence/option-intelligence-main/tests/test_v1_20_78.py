"""v1.20.78: legal hardening - personal use by default, no NSE website downloads, TradingView notice on chart pages."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("AUTH_SECRET", "test-secret-please-ignore")

import accounts as acc

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def fresh(code="", extra=False):
    acc.SQLITE_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    acc._initialised = False
    acc._cache_clear()
    acc._FAILS.clear()
    acc.SIGNUP_CODE = code
    acc.ALLOW_EXTRA_USERS = extra


def _refused(fn):
    try:
        fn()
    except acc.AccountError as e:
        return str(e)
    raise AssertionError("was allowed")


def test_owner_can_always_create_the_first_account():
    for code in ("", "invite-123"):
        fresh(code)
        s = acc.signup_status()
        assert s["open"] and s["first_user"]
        assert acc.create_user("owner", "pass-word-1", code)["is_admin"] is True


def test_no_second_account_by_default_even_with_the_invite_code():
    fresh("invite-123")
    acc.create_user("owner", "pass-word-1", "invite-123")
    s = acc.signup_status()
    assert not s["open"] and "personal tool" in s["closed_reason"]
    assert "personal tool" in _refused(lambda: acc.create_user("friend", "pass-word-2", "invite-123"))
    assert acc.user_count() == 1


def test_owner_can_reopen_invites_on_purpose():
    fresh("invite-123", extra=True)
    acc.create_user("owner", "pass-word-1", "invite-123")
    assert acc.signup_status()["open"]
    assert "nvite" in _refused(lambda: acc.create_user("friend", "pass-word-2", "wrong"))
    assert acc.create_user("friend", "pass-word-2", "invite-123")["is_admin"] is False


def test_existing_accounts_keep_working_when_signups_are_closed():
    fresh("x", extra=True)
    acc.create_user("owner", "pass-word-1", "x")
    acc.create_user("second", "pass-word-2", "x")
    acc.ALLOW_EXTRA_USERS = False
    assert acc.authenticate("second", "pass-word-2")["username"] == "second"


def test_app_never_downloads_from_nse_sites():
    m = read("main.py")
    for gone in ("nsearchives.nseindia.com", "UNIVERSE_ONLINE:", "CSV_HEADERS", "IndexConstituent/"):
        assert gone not in m
    assert "_local_constituents(universe)" in m and "dd.FALLBACK_LISTS.get(universe)" in m          # own CSV and built-in list remain
    for f in ("main.py", "dashboard_data.py", "option_plan.py", "derivatives.py", "monitor.py", "notifier.py", "instruments.py"):
        if (ROOT / f).exists():
            assert "nseindia.com" not in read(f)


def test_tradingview_notice_on_every_page_that_draws_a_chart():
    notice = "TradingView Lightweight Charts™ Copyright (с) 2024 TradingView, Inc."
    for page in ("index.html", "dashboard.html", "chart.html"):
        h = read("static/" + page)
        assert notice in h and 'href="https://www.tradingview.com/"' in h
    assert "html.cw-embed footer{display:none}" in read("static/chart.html")     # embedded chart: the terminal page shows the notice


def test_personal_use_line_on_every_page_and_legal_file():
    for page in ("index.html", "dashboard.html", "signals.html", "diagnostics.html", "notifications.html", "account.html", "login.html"):
        h = read("static/" + page)
        assert h.count("<footer") == 1 and "Not investment advice" in h and "Do not share its prices or signals" in h
    assert "Not investment advice" in read("static/chart.html")
    legal = read("LEGAL.md")
    assert "ALLOW_EXTRA_USERS=1" in legal and "24 May 2024" in legal and "not legal advice" in legal
