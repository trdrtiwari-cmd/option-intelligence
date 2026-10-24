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
        return c.one("SELECT COUNT(*) AS n FROM users")["n"]


def signup_status():
    """What the sign-up form should ask for."""
    n = user_count()
    if SIGNUP_CODE:
        return {"open": n < MAX_USERS, "needs_code": True, "first_user": n == 0}
    return {"open": n == 0, "needs_code": False, "first_user": n == 0,
            "closed_reason": None if n == 0 else "Sign-ups are closed. Set SIGNUP_CODE on the server to invite more people."}


def create_user(username, password, code=""):
    username = normalise_username(username)
    validate_username(username)
    validate_password(password, username)
    with db() as c:
        n = c.one("SELECT COUNT(*) AS n FROM users")["n"]
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
                c.one("SELECT COUNT(*) AS n FROM users WHERE id <> ?", (user_id,))["n"] > 0:
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
CACHE_TTL = 30


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
    """User for a session token, or None. Cached for 30 s so the 5-10 s polling does not hit the database each time."""
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


# ---------------------------------------------------------------- ASGI middleware
PUBLIC_PATHS = {"/health", "/login", "/api/auth/login", "/api/auth/signup", "/api/auth/config", "/api/tradingview/webhook",
                "/static/login.js", "/static/style.css", "/favicon.ico"}


def _cookie(headers):
    raw = dict(headers or []).get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        name, _, val = part.strip().partition("=")
        if name == COOKIE_NAME:
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
        token = _cookie(scope.get("headers"))
        user = None
        if token:
            try:
                user = await asyncio.to_thread(session_user, token)
            except Exception:       # database trouble must not look like "logged out" forever
                if path in PUBLIC_PATHS:
                    user = None
                else:
                    return await self._json(send, 503, {"detail": "Account database is unavailable. Try again shortly."})
        scope["oi_user"], scope["oi_token"] = user, token
        if user or path in PUBLIC_PATHS:
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
    async def _json(send, status, body):
        data = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": data})
