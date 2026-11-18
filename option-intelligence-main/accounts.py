"""Accounts, login sessions and per-user saved data (v1.10).

Storage: SQLite by default (local file), PostgreSQL when DATABASE_URL is set. Use PostgreSQL on Render - the free web
service's disk is erased on every restart/redeploy, so a SQLite file there would lose every account.

No third-party packages are needed for SQLite. Passwords use scrypt from the standard library. Session tokens are random,
only their SHA-256 is stored, and they travel in an HttpOnly cookie.

Nothing in this module imports FastAPI, so it can be unit-tested on its own.
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

BASE_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = os.getenv("SQLITE_PATH", str(BASE_DIR / "data" / "option_intelligence.db"))
AUTH_DISABLED = os.getenv("AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")   # local development only
# Invite code needed to create an account. Falls back to the old DASHBOARD_PASSWORD so an existing deployment is not left open.
SIGNUP_CODE = os.getenv("SIGNUP_CODE", "").strip() or os.getenv("DASHBOARD_PASSWORD", "").strip()

COOKIE_NAME = "oi_session"
SESSION_DAYS = 30
MAX_VALUE_BYTES = 2_000_000       # per saved key
MAX_KEYS_PER_USER = 40
MAX_USERS = 200                   # sanity cap on an invite-code deployment
# v1.20.78: the prices come from the OWNER's Upstox login. Letting other people see them (or the BUY signals) is sharing real-time
# market data and trading calls with third parties, which needs exchange / SEBI permissions an individual does not have. So after
# the owner's account no new account can be created unless the owner sets ALLOW_EXTRA_USERS=1 - a deliberate, visible choice.
ALLOW_EXTRA_USERS = os.getenv("ALLOW_EXTRA_USERS", "").strip().lower() in ("1", "true", "yes")
PERSONAL_USE_REASON = ("Sign-ups are closed: this is a personal tool. Its prices come from the owner's own Upstox login and may not be "
                       "shared with other people. (Owner: ALLOW_EXTRA_USERS=1 on the server re-opens invite-code sign-ups - read LEGAL.md first.)")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,31}$")


class AccountError(Exception):
    """Raised with a user-safe message and an HTTP status."""
    def __init__(self, message, status=400, extra=None):
        super().__init__(message)
        self.message, self.status, self.extra = message, status, extra or {}


# ---------------------------------------------------------------- database layer
_PK = "BIGSERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
_F = "DOUBLE PRECISION" if IS_PG else "REAL"
SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS users (
        id {_PK},
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        pw_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at {_F} NOT NULL,
        last_login_at {_F})""",
    f"""CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at {_F} NOT NULL,
        expires_at {_F} NOT NULL,
        user_agent TEXT)""",
    f"""CREATE TABLE IF NOT EXISTS user_data (
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        version INTEGER NOT NULL,
        updated_at {_F} NOT NULL,
        PRIMARY KEY (user_id, key))""",
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)",
    # Push registrations and alert history live with the accounts so they survive Render redeploys (PostgreSQL).
    f"""CREATE TABLE IF NOT EXISTS push_devices (
        id {_PK},
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        endpoint TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    f"""CREATE TABLE IF NOT EXISTS notification_history (
        id {_PK},
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS idx_push_history_user ON notification_history(user_id, id DESC)",
    # Small server-wide key/value store (v1.18.6): holds the auto-generated Web Push (VAPID) key pair.
    "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
]
if IS_PG:
    import psycopg
    from psycopg.rows import dict_row
    INTEGRITY_ERRORS = (psycopg.IntegrityError,)
else:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)

_init_lock = threading.Lock()
_initialised = False


class _Conn:
    def __init__(self, raw):
        self.raw = raw

    def _sql(self, sql):
        return sql.replace("?", "%s") if IS_PG else sql

    def run(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), tuple(params))
        n = cur.rowcount
        cur.close()
        return n

    def one(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), tuple(params))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row is not None else None

    def all(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(self._sql(sql), tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def insert_returning_id(self, sql, params=()):
        if IS_PG:
            cur = self.raw.cursor()
            cur.execute(self._sql(sql) + " RETURNING id", tuple(params))
            new_id = cur.fetchone()["id"]
            cur.close()
            return new_id
        cur = self.raw.cursor()
        cur.execute(sql, tuple(params))
        new_id = cur.lastrowid
        cur.close()
        return new_id


def _connect_raw():
    if IS_PG:
        return psycopg.connect(DATABASE_URL, connect_timeout=10, row_factory=dict_row)
    Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(SQLITE_PATH, timeout=10, check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    try:
        raw.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass
    return raw


@contextmanager
def db():
    """One short transaction: commit on success, roll back on any error."""
    global _initialised
    raw = _connect_raw()
    conn = _Conn(raw)
    try:
        if not _initialised:
            with _init_lock:
                if not _initialised:
                    for stmt in SCHEMA:
                        conn.run(stmt)
                    raw.commit()
                    _initialised = True
        yield conn
        raw.commit()
    except BaseException:
        try:
            raw.rollback()
        except Exception:
            pass
        raise
    finally:
        raw.close()


def storage_info():
    ephemeral = bool(os.getenv("RENDER")) and not IS_PG
    return {"engine": "postgres" if IS_PG else "sqlite", "persistent": not ephemeral,
            "warning": ("Accounts and saved data are stored in a local file, which Render erases on every restart or deploy. "
                        "Set DATABASE_URL to a PostgreSQL database (see README) so they survive.") if ephemeral else None}


# ---------------------------------------------------------------- passwords
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${dk.hex()}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt_hex, dk_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(dk_hex) // 2)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


_DUMMY_HASH = None


def _dummy_verify(password):
    """Spend the same time as a real check so an unknown username cannot be told apart by response time."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("not-a-real-password")
    verify_password(password, _DUMMY_HASH)


def normalise_username(raw):
    return str(raw or "").strip().lower()


def validate_username(username):
    if not USERNAME_RE.match(username):
        raise AccountError("Username must be 3–32 characters: letters, numbers, dot, dash or underscore (starting with a letter or number).")


def validate_password(password, username=""):
    if not isinstance(password, str) or len(password) < 8:
        raise AccountError("Password must be at least 8 characters.")
    if len(password) > 128:
        raise AccountError("Password is too long (max 128 characters).")
    if username and password.lower() == username.lower():
        raise AccountError("Password must not be the same as the username.")
    if password.isdigit() or len(set(password)) < 4:
        raise AccountError("Password is too easy to guess. Mix letters, numbers or symbols.")


# ---------------------------------------------------------------- throttling (in memory)
_FAILS = {}
_FAIL_LOCK = threading.Lock()


def throttle_check(key, limit=5, window=600):
    now = time.time()
    with _FAIL_LOCK:
        hits = [t for t in _FAILS.get(key, []) if now - t < window]
        _FAILS[key] = hits
        if len(hits) >= limit:
            raise AccountError(f"Too many attempts. Try again in {int(window - (now - hits[0])) // 60 + 1} minute(s).", 429)


def throttle_fail(key):
    with _FAIL_LOCK:
        _FAILS.setdefault(key, []).append(time.time())
        if len(_FAILS) > 5000:
            for k in list(_FAILS)[:1000]:
                _FAILS.pop(k, None)


def throttle_clear(key):
    with _FAIL_LOCK:
        _FAILS.pop(key, None)


# ---------------------------------------------------------------- users
def _public_user(row):
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"], "is_admin": bool(row["is_admin"]),
            "created_at": row["created_at"]}


def user_count():
    with db() as c:
        return c.one("SELECT COUNT(*) AS n FROM users WHERE username <> ?", (REVIEW_USERNAME,))["n"]


def signup_status():
    """What the sign-up form should ask for."""
    n = user_count()
    if n > 0 and not ALLOW_EXTRA_USERS:
        return {"open": False, "needs_code": bool(SIGNUP_CODE), "first_user": False, "closed_reason": PERSONAL_USE_REASON}
    if SIGNUP_CODE:
        return {"open": n < MAX_USERS, "needs_code": True, "first_user": n == 0}
    return {"open": n == 0, "needs_code": False, "first_user": n == 0,
            "closed_reason": None if n == 0 else "Sign-ups are closed. Set SIGNUP_CODE on the server to invite more people."}


def create_user(username, password, code=""):
    username = normalise_username(username)
    validate_username(username)
    validate_password(password, username)
    with db() as c:
        n = c.one("SELECT COUNT(*) AS n FROM users WHERE username <> ?", (REVIEW_USERNAME,))["n"]
        if n > 0 and not ALLOW_EXTRA_USERS:
            raise AccountError(PERSONAL_USE_REASON, 403)
        if SIGNUP_CODE:
            if not hmac.compare_digest(str(code or ""), SIGNUP_CODE):
                raise AccountError("Invalid invite code.", 403)
            if n >= MAX_USERS:
                raise AccountError("The user limit has been reached.", 403)
        elif n > 0:
            raise AccountError("Sign-ups are closed. Ask the owner to set SIGNUP_CODE on the server.", 403)
        if c.one("SELECT id FROM users WHERE username = ?", (username,)):
            raise AccountError("That username is already taken.", 409)
        try:
            uid = c.insert_returning_id(
                "INSERT INTO users (username, display_name, pw_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, username, hash_password(password), 1 if n == 0 else 0, time.time()))
        except INTEGRITY_ERRORS:
            raise AccountError("That username is already taken.", 409)
        return _public_user(c.one("SELECT * FROM users WHERE id = ?", (uid,)))


def authenticate(username, password):
    username = normalise_username(username)
    with db() as c:
        row = c.one("SELECT * FROM users WHERE username = ?", (username,))
        if not row:
            _dummy_verify(str(password or ""))
            raise AccountError("Wrong username or password.", 401)
        if not verify_password(str(password or ""), row["pw_hash"]):
            raise AccountError("Wrong username or password.", 401)
        c.run("UPDATE users SET last_login_at = ? WHERE id = ?", (time.time(), row["id"]))
        return _public_user(row)


def change_password(user_id, old_password, new_password, keep_token=None):
    with db() as c:
        row = c.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if not row or not verify_password(str(old_password or ""), row["pw_hash"]):
            raise AccountError("Current password is wrong.", 403)
        validate_password(new_password, row["username"])
        c.run("UPDATE users SET pw_hash = ? WHERE id = ?", (hash_password(new_password), user_id))
        if keep_token:   # sign every other device out
            c.run("DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?", (user_id, _hash_token(keep_token)))
        else:
            c.run("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    _cache_clear()


def reset_password(username, new_password):
    """Used by manage_users.py (there is no email, so the owner resets forgotten passwords from the command line)."""
    username = normalise_username(username)
    validate_password(new_password, username)
    with db() as c:
        if not c.run("UPDATE users SET pw_hash = ? WHERE username = ?", (hash_password(new_password), username)):
            raise AccountError("No such user.", 404)
        c.run("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (username,))
    _cache_clear()


def delete_user(user_id, password=None, check_password=True):
    with db() as c:
        row = c.one("SELECT * FROM users WHERE id = ?", (user_id,))
        if not row:
            raise AccountError("No such user.", 404)
        if check_password and not verify_password(str(password or ""), row["pw_hash"]):
            raise AccountError("Password is wrong.", 403)
        if row["is_admin"] and c.one("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1 AND id <> ?", (user_id,))["n"] == 0 and \
                c.one("SELECT COUNT(*) AS n FROM users WHERE id <> ? AND username <> ?", (user_id, REVIEW_USERNAME))["n"] > 0:
            raise AccountError("You are the only owner. Make another account the owner first (python manage_users.py make-admin NAME).", 409)
        c.run("DELETE FROM user_data WHERE user_id = ?", (user_id,))
        c.run("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        c.run("DELETE FROM users WHERE id = ?", (user_id,))
    _cache_clear()


def list_users():
    with db() as c:
        return c.all("SELECT id, username, is_admin, created_at, last_login_at FROM users ORDER BY id")


def make_admin(username, admin=True):
    with db() as c:
        if not c.run("UPDATE users SET is_admin = ? WHERE username = ?", (1 if admin else 0, normalise_username(username))):
            raise AccountError("No such user.", 404)
    _cache_clear()


def ensure_local_user():
    """AUTH_DISABLED (local development): one fixed account so saved data still works."""
    with db() as c:
        row = c.one("SELECT * FROM users WHERE username = ?", ("local",))
        if not row:
            uid = c.insert_returning_id("INSERT INTO users (username, display_name, pw_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                                        ("local", "local", "disabled", 1, time.time()))
            row = c.one("SELECT * FROM users WHERE id = ?", (uid,))
        return _public_user(row)


# ---------------------------------------------------------------- sessions
def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_CACHE = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 3600                  # v1.20.92: 60 min (single user). v1.20.83: 15 min (v1.20.82: 5 min, before 30 s) - an open app no longer keeps the database awake


def _cache_clear():
    with _CACHE_LOCK:
        _CACHE.clear()


def create_session(user_id, user_agent=""):
    token = secrets.token_urlsafe(32)
    now = time.time()
    with db() as c:
        c.run("INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent) VALUES (?, ?, ?, ?, ?)",
              (_hash_token(token), user_id, now, now + SESSION_DAYS * 86400, (user_agent or "")[:200]))
        c.run("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token


def session_user(token):
    """User for a session token, or None. Cached for CACHE_TTL so the 5-10 s polling does not hit the database each time."""
    if not token or len(token) > 100:
        return None
    h = _hash_token(token)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(h)
        if hit and now - hit[1] < CACHE_TTL:
            return hit[0]
    with db() as c:
        row = c.one("SELECT u.*, s.expires_at AS s_expires FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE s.token_hash = ? AND s.expires_at > ?", (h, now))
        if not row:
            return None
        if row["s_expires"] - now < SESSION_DAYS * 86400 / 2:      # sliding expiry: active users stay signed in
            c.run("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (now + SESSION_DAYS * 86400, h))
        user = _public_user(row)
    with _CACHE_LOCK:
        _CACHE[h] = (user, now)
        if len(_CACHE) > 2000:
            _CACHE.clear()
    return user


def end_session(token):
    if not token:
        return
    with db() as c:
        c.run("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
    _cache_clear()


# ---------------------------------------------------------------- saved data
class Conflict(Exception):
    def __init__(self, current):
        super().__init__("version conflict")
        self.current = current


def _record(row):
    return {"value": json.loads(row["value"]), "version": row["version"], "updated_at": row["updated_at"]}


def get_all_data(user_id):
    with db() as c:
        return {r["key"]: _record(r) for r in c.all("SELECT key, value, version, updated_at FROM user_data WHERE user_id = ?", (user_id,))}


def put_data(user_id, key, value, base_version=None, force=False):
    """Optimistic-concurrency save. base_version = the version this device last saw (None = never saw one).
    A stale base_version raises Conflict(current record) so a second device can merge instead of overwriting."""
    if not KEY_RE.match(str(key or "")):
        raise AccountError("Invalid data key.", 400)
    blob = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if len(blob.encode("utf-8")) > MAX_VALUE_BYTES:
        raise AccountError("Saved data for this item is too large.", 413)
    now = time.time()
    with db() as c:
        row = c.one("SELECT value, version, updated_at FROM user_data WHERE user_id = ? AND key = ?", (user_id, key))
        if row is None:
            if c.one("SELECT COUNT(*) AS n FROM user_data WHERE user_id = ?", (user_id,))["n"] >= MAX_KEYS_PER_USER:
                raise AccountError("Too many saved items.", 400)
            try:
                c.run("INSERT INTO user_data (user_id, key, value, version, updated_at) VALUES (?, ?, ?, 1, ?)", (user_id, key, blob, now))
            except INTEGRITY_ERRORS:
                raise Conflict(None)
            return {"version": 1, "updated_at": now}
        if not force and base_version != row["version"]:
            raise Conflict(_record(row))
        n = c.run("UPDATE user_data SET value = ?, version = version + 1, updated_at = ? WHERE user_id = ? AND key = ? AND version = ?",
                  (blob, now, user_id, key, row["version"]))
        if n == 0:
            raise Conflict(None)
        return {"version": row["version"] + 1, "updated_at": now}


def delete_data(user_id, key):
    with db() as c:
        c.run("DELETE FROM user_data WHERE user_id = ? AND key = ?", (user_id, key))


# ---------------------------------------------------------------- read-only review link
# REVIEW_KEY (at least 16 characters) enables https://YOUR-APP/review?key=<REVIEW_KEY>. Opening it sets a cookie that lets the
# visitor browse every page as the "review" account without signing in. Reviewers can only read: every POST/PUT/PATCH/DELETE
# is refused (except sign-out) and so is the Upstox login. Changing or removing REVIEW_KEY invalidates every review cookie.
# Scripts and checkers can send the key in an "X-Review-Key" header instead of using the cookie.
REVIEW_KEY = os.getenv("REVIEW_KEY", "").strip()
REVIEW_COOKIE = "oi_review"
REVIEW_DAYS = 7
REVIEW_USERNAME = "review"
REVIEW_BLOCKED_PATHS = {"/api/login", "/auth/callback"}          # GET routes that change shared state (Upstox token)
REVIEW_ALLOWED_WRITES = {"/api/auth/logout"}
_REVIEW_USER = {}


def review_enabled():
    return len(REVIEW_KEY) >= 16


def review_cookie_value():
    """Cookie value derived from the key, so the key itself is never stored in the browser."""
    return hmac.new(REVIEW_KEY.encode("utf-8"), b"oi-review-v1", hashlib.sha256).hexdigest()


def review_key_ok(key):
    return review_enabled() and hmac.compare_digest(str(key or "").encode("utf-8"), REVIEW_KEY.encode("utf-8"))


def review_cookie_ok(value):
    return review_enabled() and bool(value) and hmac.compare_digest(str(value).encode("utf-8"), review_cookie_value().encode("utf-8"))


def ensure_review_user():
    """The shared read-only account reviewers browse as (never an admin, cannot sign in with a password)."""
    if _REVIEW_USER.get("path") == (SQLITE_PATH, DATABASE_URL):
        return dict(_REVIEW_USER["user"])
    with db() as c:
        row = c.one("SELECT * FROM users WHERE username = ?", (REVIEW_USERNAME,))
        if not row:
            uid = c.insert_returning_id("INSERT INTO users (username, display_name, pw_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
                                        (REVIEW_USERNAME, "Reviewer (read-only)", "disabled", 0, time.time()))
            row = c.one("SELECT * FROM users WHERE id = ?", (uid,))
        user = _public_user(row)
    user["is_admin"] = False
    user["read_only"] = True
    _REVIEW_USER.update(path=(SQLITE_PATH, DATABASE_URL), user=user)
    return dict(user)


# ---------------------------------------------------------------- ASGI middleware
PUBLIC_PATHS = {"/health", "/login", "/api/auth/login", "/api/auth/signup", "/api/auth/config", "/api/tradingview/webhook",
                "/static/login.js", "/static/style.css", "/favicon.ico",
                # mobile app shell (v1.17): needed before sign-in so the login page can install / cache the app
                "/sw.js", "/static/manifest.webmanifest", "/static/offline.html", "/static/mobile.css", "/static/mobile.js",
                "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/icon-maskable-512.png",
                "/static/icons/apple-touch-icon.png", "/static/icons/favicon-32.png"}


def _cookie(headers, cookie_name=None):
    raw = dict(headers or []).get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        name, _, val = part.strip().partition("=")
        if name == (cookie_name or COOKIE_NAME):
            return val
    return ""


def _same_origin(headers):
    h = dict(headers or [])
    origin = h.get(b"origin", b"").decode("latin-1")
    if not origin:
        return True
    host = (h.get(b"x-forwarded-host") or h.get(b"host") or b"").decode("latin-1").split(",")[0].strip()
    return urlsplit(origin).netloc == host


def safe_next(path):
    """Only allow same-site relative redirects after login."""
    path = str(path or "/")
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


def _public(path, method="GET"):
    """v1.20.82: the fixed public paths, plus one-time download links (GET /dl/<random>/<name>)."""
    return path in PUBLIC_PATHS or (path.startswith("/dl/") and method in ("GET", "HEAD"))


class SessionAuthMiddleware:
    """Everything except the public paths needs a valid login cookie. Pages redirect to /login, APIs answer 401 JSON,
    WebSockets are closed with code 4401. Cross-site POST/PUT/DELETE requests are refused (Origin check)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if AUTH_DISABLED:
            scope["oi_user"] = await asyncio.to_thread(ensure_local_user)
            return await self.app(scope, receive, send)
        method = scope.get("method", "GET")
        if scope["type"] == "http" and method not in ("GET", "HEAD", "OPTIONS") and path != "/api/tradingview/webhook" \
                and not _same_origin(scope.get("headers")):
            return await self._json(send, 403, {"detail": "Cross-site request blocked."})
        if path == "/review" and scope["type"] == "http":
            return await self._review_entry(scope, send)
        token = _cookie(scope.get("headers"))
        user = None
        if token:
            try:
                user = await asyncio.to_thread(session_user, token)
            except Exception:       # database trouble must not look like "logged out" forever
                if _public(path, method):
                    user = None
                else:
                    return await self._json(send, 503, {"detail": "Account database is unavailable. Try again shortly."})
        if not user and not _public(path, method) and (review_cookie_ok(_cookie(scope.get("headers"), REVIEW_COOKIE))
                                                      or review_key_ok(dict(scope.get("headers") or []).get(b"x-review-key", b"").decode("latin-1"))):
            if (scope["type"] == "http" and method not in ("GET", "HEAD", "OPTIONS") and path not in REVIEW_ALLOWED_WRITES) \
                    or path in REVIEW_BLOCKED_PATHS:
                return await self._json(send, 403, {"detail": "Read-only review link: changes are disabled.", "read_only": True})
            try:
                user = await asyncio.to_thread(ensure_review_user)
            except Exception:
                return await self._json(send, 503, {"detail": "Account database is unavailable. Try again shortly."})
            token = ""
        scope["oi_user"], scope["oi_token"] = user, token
        if user or _public(path, method):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        if path.startswith("/api/") or method not in ("GET", "HEAD"):
            return await self._json(send, 401, {"detail": "Login required", "login_required": True})
        qs = scope.get("query_string", b"").decode("latin-1")
        nxt = quote(path + ("?" + qs if qs else ""), safe="/")
        await send({"type": "http.response.start", "status": 302, "headers": [(b"location", f"/login?next={nxt}".encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _review_entry(scope, send):
        """GET /review?key=... : check the key, swap it for a cookie and redirect, so the key leaves the address bar."""
        if not review_enabled():
            return await SessionAuthMiddleware._json(send, 404, {"detail": "Not Found"})
        from urllib.parse import parse_qs
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if not review_key_ok((qs.get("key") or [""])[0]):
            return await SessionAuthMiddleware._json(send, 403, {"detail": "Invalid review link."})
        h = dict(scope.get("headers") or [])
        proto = h.get(b"x-forwarded-proto", b"").decode("latin-1").split(",")[0].strip() or scope.get("scheme", "http")
        cookie = f"{REVIEW_COOKIE}={review_cookie_value()}; Max-Age={REVIEW_DAYS * 86400}; Path=/; HttpOnly; SameSite=Lax"
        if proto == "https":
            cookie += "; Secure"
        nxt = safe_next((qs.get("next") or ["/"])[0])
        await send({"type": "http.response.start", "status": 302, "headers": [
            (b"location", nxt.encode("latin-1", "ignore") or b"/"), (b"set-cookie", cookie.encode()),
            (b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]})
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _json(send, status, body):
        data = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": data})


# ---------------------------------------------------------------- push devices / notification history
def push_upsert_device(user_id, kind, endpoint, payload, created_at):
    with db() as c:
        c.run("INSERT INTO push_devices(user_id,kind,endpoint,payload,created_at) VALUES(?,?,?,?,?) "
              "ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id, payload=excluded.payload",
              (user_id, kind, endpoint, payload, created_at))


def push_devices(kind, user_id=0):
    """Registered devices of one kind. user_id 0 = every account (single-owner deployments that set no PUSH_OWNER_USER_ID)."""
    with db() as c:
        if user_id:
            return c.all("SELECT id,user_id,endpoint,payload FROM push_devices WHERE kind=? AND user_id=?", (kind, user_id))
        return c.all("SELECT id,user_id,endpoint,payload FROM push_devices WHERE kind=?", (kind,))


def push_delete_device(device_id):
    with db() as c:
        c.run("DELETE FROM push_devices WHERE id=?", (device_id,))


def push_recipient_ids(user_id=0):
    """Accounts that should get history rows: the owner, or every account with a registered device."""
    if user_id:
        return [user_id]
    with db() as c:
        return [r["user_id"] for r in c.all("SELECT DISTINCT user_id FROM push_devices")]


def push_add_history(user_id, event, symbol, side, title, body, created_at):
    with db() as c:
        c.run("INSERT INTO notification_history(user_id,event,symbol,side,title,body,created_at) VALUES(?,?,?,?,?,?,?)",
              (user_id, event, symbol, side, title, body, created_at))


def push_history(user_id, limit=100):
    with db() as c:
        return c.all("SELECT id,event,symbol,side,title,body,created_at FROM notification_history WHERE user_id=? ORDER BY id DESC LIMIT ?",
                     (user_id, max(1, min(int(limit), 200))))


# ---------------------------------------------------------------- server-wide meta (v1.18.6)
def meta_get(key):
    with db() as c:
        row = c.all("SELECT value FROM app_meta WHERE key=?", (key,))
        return row[0]["value"] if row else None


def meta_set(key, value):
    with db() as c:
        c.run("INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def meta_list(prefix):
    with db() as c:
        return c.all("SELECT key,value FROM app_meta WHERE key LIKE ? ORDER BY key", (prefix + "%",))


def meta_set_if_absent(key, value):
    """Store value only if the key is new; returns the value that is stored (so two workers agree on one key pair)."""
    with db() as c:
        c.run("INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO NOTHING", (key, value))
    return meta_get(key)


def vapid_keys():
    """(public, private) Web Push keys, base64url. Generated once and kept in the accounts database, so Web Push works
    with no manual key setup and survives restarts (PostgreSQL). Env vars VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY win if set."""
    pub, priv = os.getenv("VAPID_PUBLIC_KEY", "").strip(), os.getenv("VAPID_PRIVATE_KEY", "").strip()
    if pub and priv:
        return pub, priv
    stored = meta_get("vapid_keys")
    if not stored:
        import base64
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        key = ec.generate_private_key(ec.SECP256R1())
        b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
        pub_b = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        priv_b = key.private_numbers().private_value.to_bytes(32, "big")
        stored = meta_set_if_absent("vapid_keys", json.dumps({"public": b64(pub_b), "private": b64(priv_b)}))
    d = json.loads(stored)
    return d["public"], d["private"]


# ---------------------------------------------------------------- v1.20.92: move the database to another PostgreSQL
COPY_TABLES = ["users", "sessions", "user_data", "push_devices", "notification_history", "app_meta"]   # parents first
_SERIAL_TABLES = ("users", "push_devices", "notification_history")


def _pg_schema():
    pk, fl = "BIGSERIAL PRIMARY KEY", "DOUBLE PRECISION"
    return [s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", pk).replace(" REAL", " " + fl) if not IS_PG else s for s in SCHEMA]


def _target_connect(url):
    import psycopg as _pg
    from psycopg.rows import dict_row as _dr
    u = (url or "").strip()
    if not u.startswith(("postgres://", "postgresql://")):
        raise AccountError("The new database address must start with postgres:// or postgresql://")
    if DATABASE_URL and u == DATABASE_URL:
        raise AccountError("That is the database the app already uses.")
    return _pg.connect(u, connect_timeout=15, row_factory=_dr)


def target_test(url):
    """Connect to the new database, time a few round trips and say whether it already holds app data. Nothing is written."""
    t0 = time.time()
    with _target_connect(url) as c:
        connect_ms = round((time.time() - t0) * 1000)
        times = []
        for _ in range(3):
            t1 = time.time()
            c.execute("SELECT 1").fetchone()
            times.append((time.time() - t1) * 1000)
        ver = c.execute("SHOW server_version").fetchone()["server_version"]
        has = c.execute("SELECT to_regclass('public.users') AS t").fetchone()["t"]
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] if has else 0
    ms = round(sorted(times)[1])
    return {"ok": True, "ms": ms, "connect_ms": connect_ms, "server_version": ver, "has_app_data": bool(users), "users": users,
            "verdict": "fast" if ms <= 120 else "ok" if ms <= 300 else "slow - better stay on the current database"}


def copy_to(url, overwrite=False):
    """Copy every account table (users, logins, saved settings, push devices, alert history, app_meta: Upstox token,
    signal-log copies, paper trades ...) into the new PostgreSQL. The current database is only read, never changed.
    Refuses a target that already has users unless overwrite=True (then its tables are emptied first)."""
    with db() as src:
        data = {t: src.all(f"SELECT * FROM {t}") for t in COPY_TABLES}
    with _target_connect(url) as tc:
        for stmt in _pg_schema():
            tc.execute(stmt)
        n_users = tc.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if n_users and not overwrite:
            raise AccountError(f"The new database already holds app data ({n_users} user(s)). Tick 'replace' to overwrite it.")
        for t in reversed(COPY_TABLES):
            tc.execute(f"DELETE FROM {t}")
        for t in COPY_TABLES:
            rows = data[t]
            if not rows:
                continue
            cols = list(rows[0].keys())
            sql = f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
            with tc.cursor() as cur:
                cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
        for t in _SERIAL_TABLES:
            tc.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")
        check = []
        for t in COPY_TABLES:
            n_new = tc.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            check.append({"table": t, "source": len(data[t]), "copied": n_new, "ok": n_new == len(data[t])})
        tc.commit()
    return {"ok": all(c["ok"] for c in check), "tables": check,
            "bytes": sum(len(str(r)) for t in COPY_TABLES for r in data[t])}
