"""Accounts / sessions / saved-data tests (SQLite, no web framework needed). Run: python tests/test_accounts.py"""
import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.pop("DATABASE_URL", None)
os.environ["SIGNUP_CODE"] = ""
os.environ["DASHBOARD_PASSWORD"] = ""
import accounts as acc


def fresh():
    """new empty database for every test"""
    acc.SQLITE_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
    acc._initialised = False
    acc._cache_clear()
    acc._FAILS.clear()
    acc.SIGNUP_CODE = ""


def raises(fn, status=None, text=None):
    try:
        fn()
    except acc.AccountError as e:
        assert status is None or e.status == status, (e.status, e.message)
        assert text is None or text in e.message, e.message
        return e
    raise AssertionError("expected AccountError")


def test_password_hashing():
    h = acc.hash_password("correct horse 9")
    assert h.startswith("scrypt$") and "correct" not in h
    assert acc.verify_password("correct horse 9", h) and not acc.verify_password("correct horse 8", h)
    assert acc.hash_password("same") != acc.hash_password("same")           # salted
    assert not acc.verify_password("x", "garbage")


def test_validation():
    fresh()
    raises(lambda: acc.create_user("ab", "goodpass123"), 400)                # too short
    raises(lambda: acc.create_user("bad name", "goodpass123"), 400)
    raises(lambda: acc.create_user("trader1", "short"), 400)
    raises(lambda: acc.create_user("trader1", "12345678"), 400)             # digits only
    raises(lambda: acc.create_user("trader1", "aaaaaaaaaa"), 400)           # no variety
    raises(lambda: acc.create_user("trader1", "Trader1"), 400)              # == username (also too short)


def test_signup_rules():
    fresh()
    s = acc.signup_status(); assert s["open"] and s["first_user"] and not s["needs_code"]
    u1 = acc.create_user("Owner", "pass-word-1")
    assert u1["username"] == "owner" and u1["is_admin"] is True
    assert not acc.signup_status()["open"]
    raises(lambda: acc.create_user("friend", "pass-word-2"), 403, "closed")
    acc.SIGNUP_CODE = "invite-123"
    s = acc.signup_status(); assert s["open"] and s["needs_code"]
    raises(lambda: acc.create_user("friend", "pass-word-2", "wrong"), 403, "invite")
    u2 = acc.create_user("friend", "pass-word-2", "invite-123")
    assert u2["is_admin"] is False
    raises(lambda: acc.create_user("FRIEND", "pass-word-3", "invite-123"), 409)   # case-insensitive duplicate


def test_login_and_sessions():
    fresh()
    acc.create_user("trader", "pass-word-1")
    raises(lambda: acc.authenticate("trader", "nope-nope-1"), 401, "Wrong username or password")
    raises(lambda: acc.authenticate("ghost", "nope-nope-1"), 401, "Wrong username or password")   # same message, no user enumeration
    u = acc.authenticate("TRADER", "pass-word-1")
    tok = acc.create_session(u["id"], "ua")
    assert acc.session_user(tok)["username"] == "trader"
    assert acc.session_user("not-a-token") is None and acc.session_user("") is None
    with acc.db() as c:                                                      # only the hash is stored
        assert c.one("SELECT COUNT(*) AS n FROM sessions WHERE token_hash = ?", (tok,))["n"] == 0
    acc.end_session(tok)
    assert acc.session_user(tok) is None


def test_session_expiry_and_sliding():
    fresh()
    u = acc.create_user("trader", "pass-word-1")
    tok = acc.create_session(u["id"])
    h = acc._hash_token(tok)
    with acc.db() as c:
        c.run("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (time.time() - 5, h))
    acc._cache_clear()
    assert acc.session_user(tok) is None                                     # expired
    tok2 = acc.create_session(u["id"]); h2 = acc._hash_token(tok2)
    with acc.db() as c:
        c.run("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (time.time() + 86400, h2))   # 1 day left
    acc._cache_clear()
    assert acc.session_user(tok2)
    with acc.db() as c:
        assert c.one("SELECT expires_at FROM sessions WHERE token_hash = ?", (h2,))["expires_at"] > time.time() + 20 * 86400


def test_password_change_signs_out_other_devices():
    fresh()
    u = acc.create_user("trader", "pass-word-1")
    a, b = acc.create_session(u["id"]), acc.create_session(u["id"])
    raises(lambda: acc.change_password(u["id"], "wrong-pass-1", "new-pass-word-2", a), 403)
    acc.change_password(u["id"], "pass-word-1", "new-pass-word-2", a)
    assert acc.session_user(a) and acc.session_user(b) is None
    raises(lambda: acc.authenticate("trader", "pass-word-1"), 401)
    assert acc.authenticate("trader", "new-pass-word-2")["id"] == u["id"]
    acc.reset_password("trader", "reset-pass-3")
    assert acc.session_user(a) is None and acc.authenticate("trader", "reset-pass-3")


def test_throttle():
    fresh()
    for _ in range(5):
        acc.throttle_check("k", 5, 600); acc.throttle_fail("k")
    raises(lambda: acc.throttle_check("k", 5, 600), 429)
    acc.throttle_clear("k"); acc.throttle_check("k", 5, 600)


def test_saved_data_versions_and_conflicts():
    fresh()
    u = acc.create_user("trader", "pass-word-1")["id"]
    r1 = acc.put_data(u, "paper-trades", [{"id": "a"}], None)
    assert r1["version"] == 1
    r2 = acc.put_data(u, "paper-trades", [{"id": "a"}, {"id": "b"}], 1)
    assert r2["version"] == 2
    try:
        acc.put_data(u, "paper-trades", [{"id": "z"}], 1); raise AssertionError("expected Conflict")
    except acc.Conflict as c:                                                # stale device gets the current copy back
        assert c.current["version"] == 2 and len(c.current["value"]) == 2
    try:
        acc.put_data(u, "paper-trades", [], None); raise AssertionError("expected Conflict")   # never-synced device must not clobber
    except acc.Conflict:
        pass
    assert acc.put_data(u, "paper-trades", [{"id": "z"}], None, force=True)["version"] == 3
    d = acc.get_all_data(u)
    assert d["paper-trades"]["value"] == [{"id": "z"}] and d["paper-trades"]["version"] == 3
    acc.put_data(u, "pref-signal-mode", "scalp", None); acc.put_data(u, "paper-capital", 150000, None)
    d = acc.get_all_data(u)
    assert d["pref-signal-mode"]["value"] == "scalp" and d["paper-capital"]["value"] == 150000
    acc.delete_data(u, "paper-capital"); assert "paper-capital" not in acc.get_all_data(u)


def test_saved_data_limits_and_isolation():
    fresh()
    acc.SIGNUP_CODE = "x"
    a = acc.create_user("alice", "pass-word-1", "x")["id"]; b = acc.create_user("bob", "pass-word-1", "x")["id"]
    for bad in ("", "UPPER", "has space", "../x", "a" * 70, "-lead"):
        raises(lambda bad=bad: acc.put_data(a, bad, 1, None), 400)
    raises(lambda: acc.put_data(a, "big", "x" * (acc.MAX_VALUE_BYTES + 10), None), 413)
    for i in range(acc.MAX_KEYS_PER_USER):
        acc.put_data(a, f"k{i}", i, None)
    raises(lambda: acc.put_data(a, "one-too-many", 1, None), 400, "Too many")
    acc.put_data(b, "paper-trades", ["bob-only"], None)
    assert "paper-trades" not in acc.get_all_data(a) and acc.get_all_data(b)["paper-trades"]["value"] == ["bob-only"]


def test_delete_account_removes_everything():
    fresh()
    acc.SIGNUP_CODE = "x"
    acc.create_user("owner", "pass-word-1", "x")
    u = acc.create_user("alice", "pass-word-1", "x")["id"]
    tok = acc.create_session(u); acc.put_data(u, "paper-trades", [1], None)
    raises(lambda: acc.delete_user(u, "wrong-pass-1"), 403)
    acc.delete_user(u, "pass-word-1")
    assert acc.session_user(tok) is None
    with acc.db() as c:
        assert c.one("SELECT COUNT(*) AS n FROM user_data")["n"] == 0
        assert c.one("SELECT COUNT(*) AS n FROM users")["n"] == 1


def test_last_owner_cannot_leave_others_stranded():
    fresh()
    acc.SIGNUP_CODE = "x"
    o = acc.create_user("owner", "pass-word-1", "x")["id"]; acc.create_user("friend", "pass-word-1", "x")
    raises(lambda: acc.delete_user(o, "pass-word-1"), 409, "only owner")
    acc.make_admin("friend"); acc.delete_user(o, "pass-word-1")


# ---------------- middleware ----------------
async def call(mw, path, method="GET", headers=None, typ="http", query=b""):
    sent, seen = [], {}

    async def app(scope, receive, send):
        seen["user"] = scope.get("oi_user"); sent.append(("APP", None))
    scope = {"type": typ, "path": path, "method": method, "headers": headers or [], "query_string": query}

    async def send(m):
        sent.append((m.get("type"), m.get("status") or m.get("code")))
        seen["location"] = dict(m.get("headers", [])).get(b"location", seen.get("location"))
    await acc.SessionAuthMiddleware(app)(scope, None, send)
    return sent, seen


def test_middleware():
    fresh()
    u = acc.create_user("trader", "pass-word-1"); tok = acc.create_session(u["id"])
    run = lambda *a, **k: asyncio.run(call(None, *a, **k))
    sent, seen = run("/");                       assert sent[0] == ("http.response.start", 302) and seen["location"] == b"/login?next=/", (sent, seen)
    sent, seen = run("/dashboard", query=b"x=1"); assert sent[0][1] == 302 and seen["location"].startswith(b"/login?next=/dashboard"), seen
    sent, _ = run("/api/analysis");              assert sent[0][1] == 401, sent
    sent, _ = run("/api/userdata", "PUT");       assert sent[0][1] == 401, sent
    sent, _ = run("/ws/live", typ="websocket");  assert sent[0] == ("websocket.close", 4401), sent
    for pub in ("/health", "/login", "/api/auth/login", "/api/tradingview/webhook", "/static/style.css"):
        sent, _ = run(pub, "POST" if "webhook" in pub or "auth" in pub else "GET"); assert sent[0][0] == "APP", (pub, sent)
    cookie = [(b"cookie", f"x=1; oi_session={tok}".encode())]
    sent, seen = run("/api/analysis", headers=cookie); assert sent[0][0] == "APP" and seen["user"]["username"] == "trader"
    sent, _ = run("/ws/live", typ="websocket", headers=cookie); assert sent[0][0] == "APP"
    bad = [(b"cookie", b"oi_session=forged")]
    sent, _ = run("/api/analysis", headers=bad); assert sent[0][1] == 401
    # cross-site writes are refused even with a valid cookie
    evil = cookie + [(b"origin", b"https://evil.example"), (b"host", b"app.example")]
    sent, _ = run("/api/userdata/paper-trades", "PUT", headers=evil); assert sent[0][1] == 403, sent
    ok = cookie + [(b"origin", b"https://app.example"), (b"host", b"app.example")]
    sent, _ = run("/api/userdata/paper-trades", "PUT", headers=ok); assert sent[0][0] == "APP"
    # the webhook (called by TradingView, no Origin match) is not subject to the origin check
    sent, _ = run("/api/tradingview/webhook", "POST", headers=[(b"origin", b"https://tradingview.com"), (b"host", b"app.example")]); assert sent[0][0] == "APP"
    assert acc.safe_next("/dashboard") == "/dashboard" and acc.safe_next("//evil.com") == "/" and acc.safe_next("https://evil.com") == "/"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
    print("all account tests passed")
