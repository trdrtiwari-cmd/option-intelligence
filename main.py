import os, re, base64, secrets, threading, time, asyncio, json, logging, importlib.util
from datetime import date, timedelta, datetime, timezone, time as dtime
from pathlib import Path
from urllib.parse import urlencode
from typing import Any, Optional
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from indicators import IST, MODES, analyze, merge_volume, normalize_candles, parse_time
from instruments import FutureScanner, pick_front_future
from feed_parser import parse_feed_message
import accounts as acc
import dashboard_data as dd
import diagnostics as dgn
import signal_fusion as sf

BASE_DIR = Path(__file__).resolve().parent
APP_TITLE = "Option Intelligence"
UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "").strip()
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET", "").strip()
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "").strip()
# Quick-start access token (Upstox tokens expire daily; use Connect Upstox to refresh).
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
# Optional: protect the whole dashboard with a password (HTTP Basic, any username).
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
API_BASE = "https://api.upstox.com"
API_V3 = "https://api.upstox.com/v3"

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("option_intelligence")

APP_VERSION = "1.11.4"


class NoCacheStaticFiles(StaticFiles):
    """Static files revalidate on every load (ETag -> cheap 304) so a redeploy is never masked by a stale cached app.js."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title=APP_TITLE, version=APP_VERSION)
app.add_middleware(acc.SessionAuthMiddleware)   # login required everywhere except /health, /login and the TradingView webhook
app.mount("/static", NoCacheStaticFiles(directory=str(BASE_DIR / "static")), name="static")

oauth_states = set()
runtime_token = UPSTOX_ACCESS_TOKEN
# In-memory caches only; secrets never leave the backend.
LIVE_TICKS = {}
STREAMS = {}
STREAM_META = {}
STREAM_ERRORS = {}
CHAIN_CACHE = {}
CONTRACT_CACHE = {}
CANDLE_CACHE = {}
TOKEN_CHECK = {"at": 0.0, "token": None, "state": "UNKNOWN"}
FUTURES = {"day": None, "rows": [], "idx": [], "eq": [], "failed_at": 0.0, "error": None}
FUT_LOCK = asyncio.Lock()
INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
CHAIN_CACHE_TTL = 4
CONTRACT_CACHE_TTL = 300
CANDLE_CACHE_TTL = 3          # merged candles (history + today) - short, so refreshes are near-live
HIST_CACHE_TTL = 600          # completed past sessions never change intraday: cache them
HIST_CACHE = {}
LTP_CACHE = {}
LTP_CACHE_TTL = 1.5
FRESH_SECONDS = 15
STREAM_LOCK = threading.RLock()

INDEX_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
}
RELATIVE_EXPIRIES = {"current_week", "next_week", "far_week", "current_month", "next_month", "far_month"}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------- helpers
def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ist_now():
    return datetime.now(IST)


def market_status():
    now = ist_now()
    is_open = now.weekday() < 5 and dtime(9, 15) <= now.time() <= dtime(15, 30)
    return {"open": is_open, "ist_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "Regular NSE schedule (Mon-Fri 09:15-15:30 IST); exchange holidays are not checked."}


def token_or_401():
    if not runtime_token:
        raise HTTPException(
            status_code=401,
            detail="Upstox access token not configured. Add UPSTOX_ACCESS_TOKEN in Render environment variables, or use Connect Upstox."
        )
    return runtime_token


async def upstox_get(url, params=None):
    token = token_or_401()
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, params=params, headers=headers)
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]
        if response.status_code == 401:
            detail = {"upstox_error": detail, "hint": "Access token expired or invalid. Upstox tokens expire daily - click Connect Upstox."}
        elif response.status_code == 429:
            detail = {"upstox_error": detail, "hint": "Upstox rate limit hit. Wait a few seconds and retry."}
        else:
            detail = {"upstox_error": detail}
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return response.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Unexpected response from Upstox")


async def check_token_state():
    """VALID / EXPIRED / MISSING / UNKNOWN. Cached for 60s so /api/status stays cheap."""
    if not runtime_token:
        return "MISSING"
    c = TOKEN_CHECK
    if c["token"] == runtime_token and time.time() - c["at"] < 60:
        return c["state"]
    state = "UNKNOWN"
    try:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {runtime_token}"}
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(f"{API_BASE}/v2/user/profile", headers=headers)
            if r.status_code in (401, 403):
                # Read-only / analytics tokens cannot read the profile but work for market data,
                # so only call the token expired if a real market-data request is rejected too.
                r = await client.get(f"{API_BASE}/v2/market-quote/ltp", params={"instrument_key": INDEX_KEYS["NIFTY"]}, headers=headers)
        state = "VALID" if r.status_code == 200 else "EXPIRED" if r.status_code == 401 else "UNKNOWN"
    except Exception:
        state = "UNKNOWN"
    TOKEN_CHECK.update({"at": time.time(), "token": runtime_token, "state": state})
    return state


def contract_expiry(row):
    """Return YYYY-MM-DD for a contract row (handles string dates and epoch-ms values)."""
    val = row.get("expiry") or row.get("expiry_date") or row.get("expiryDate")
    if val is None:
        return None
    if isinstance(val, (int, float)):
        seconds = val / 1000 if val > 1e12 else val
        return datetime.fromtimestamp(seconds, tz=IST).strftime("%Y-%m-%d")
    text = str(val)[:10]
    return text if DATE_RE.fullmatch(text) else None


# ---------------------------------------------------------------- candles
def _rows_to_candles(rows):
    out = []
    for row in rows or []:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        out.append({"time": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4],
                    "volume": row[5] if len(row) > 5 else 0, "oi": row[6] if len(row) > 6 else None})
    return out


async def fetch_candles(key, interval, days):
    """Historical + today's intraday candles, merged, ascending, de-duplicated.

    The multi-day history is cached for 10 minutes (past sessions cannot change); only the small
    intraday call is repeated, so a refresh is fast and can run every few seconds."""
    ck = (key, interval, days)
    hit = CANDLE_CACHE.get(ck)
    if hit and time.time() - hit[0] < CANDLE_CACHE_TTL:
        return hit[1]
    end = ist_now().date()
    start = end - timedelta(days=days)
    hk = (key, interval, days, end.isoformat())
    hh = HIST_CACHE.get(hk)
    if hh and time.time() - hh[0] < HIST_CACHE_TTL:
        hist_rows = hh[1]
    else:
        hist = await upstox_get(f"{API_V3}/historical-candle/{key}/minutes/{interval}/{end.isoformat()}/{start.isoformat()}")
        hist_rows = _rows_to_candles((hist.get("data") or {}).get("candles"))
        HIST_CACHE[hk] = (time.time(), hist_rows)
        if len(HIST_CACHE) > 40:
            for old in sorted(HIST_CACHE, key=lambda k: HIST_CACHE[k][0])[:15]:
                HIST_CACHE.pop(old, None)
    rows = list(hist_rows)
    try:
        intra = await upstox_get(f"{API_V3}/historical-candle/intraday/{key}/minutes/{interval}")
        rows += _rows_to_candles((intra.get("data") or {}).get("candles"))
    except HTTPException as exc:
        if exc.status_code == 401:
            raise
        logger.info("Intraday candles unavailable for %s: %s", key, str(exc.detail)[:160])
    candles = [{k: v for k, v in c.items() if k != "_dt"} for c in normalize_candles(rows)]
    CANDLE_CACHE[ck] = (time.time(), candles)
    if len(CANDLE_CACHE) > 60:
        for old in sorted(CANDLE_CACHE, key=lambda k: CANDLE_CACHE[k][0])[:20]:
            CANDLE_CACHE.pop(old, None)
    return candles


# ---------------------------------------------------------------- index-futures volume proxy
async def load_futures_rows():
    """Stream Upstox's public NSE instrument file and keep only FUT records (low memory)."""
    scanner = FutureScanner(eq_symbols=dd.FALLBACK_NIFTY50)
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        async with client.stream("GET", INSTRUMENT_URL) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"instrument file HTTP {resp.status_code}")
            async for chunk in resp.aiter_bytes(65536):
                scanner.feed(chunk)
                await asyncio.sleep(0)  # keep the event loop responsive
    if not scanner.rows:
        raise RuntimeError("no futures records found" + (" (unexpected file format)" if scanner.overflowed else ""))
    logger.info("Instrument file scanned: %d records, %d futures, %d indices", scanner.scanned, len(scanner.rows), len(scanner.indices))
    return scanner


async def ensure_instruments():
    """Instrument file (futures + index rows), downloaded at most once per IST day (retry after 15 min on failure)."""
    today = ist_now().date()
    async with FUT_LOCK:
        if FUTURES["day"] != today and time.time() - FUTURES["failed_at"] > 900:
            try:
                sc = await load_futures_rows()
                FUTURES.update(rows=sc.rows, idx=sc.indices, eq=sc.equities, day=today, error=None)
            except Exception as exc:
                FUTURES.update(failed_at=time.time(), error=str(exc)[:200])
                logger.warning("Instrument file unavailable: %s", exc)
        return FUTURES["rows"]


async def get_front_future(sym):
    """Front-month index future for volume."""
    rows = await ensure_instruments()
    return pick_front_future(rows, sym, INDEX_KEYS[sym], ist_now().date()) if rows else None


# ---------------------------------------------------------------- contracts / expiries / chain
async def get_contracts(sym, expiry=""):
    ck = (sym, expiry)
    hit = CONTRACT_CACHE.get(ck)
    if hit and time.time() - hit[0] < CONTRACT_CACHE_TTL:
        return hit[1]
    params = {"instrument_key": INDEX_KEYS[sym]}
    if expiry:
        params["expiry_date"] = expiry
    response = await upstox_get(f"{API_BASE}/v2/option/contract", params)
    rows = response.get("data", []) if isinstance(response, dict) else []
    if isinstance(rows, dict):  # tolerate proxy wrappers
        rows = rows.get("data", rows.get("contracts", []))
    rows = rows if isinstance(rows, list) else []
    CONTRACT_CACHE[ck] = (time.time(), rows)
    return rows


async def get_all_contracts(sym):
    """Unfiltered contracts; if the account returns nothing, merge relative-expiry lookups."""
    rows = await get_contracts(sym)
    if rows:
        return rows
    collected, errors = {}, []
    for keyword in ("current_week", "current_month", "next_week", "next_month", "far_week", "far_month"):
        try:
            for row in await get_contracts(sym, keyword):
                ident = row.get("instrument_key") or (row.get("expiry"), row.get("trading_symbol"))
                if ident:
                    collected[ident] = row
        except HTTPException as exc:
            errors.append(f"{keyword}: {str(exc.detail)[:160]}")
    if not collected and errors:
        logger.warning("Upstox contract lookup returned no rows for %s: %s", sym, " | ".join(errors))
    return list(collected.values())


async def resolve_expiry(sym, requested):
    """Turn current_week / current_month / '' into an actual YYYY-MM-DD expiry.
    The option-chain endpoint only accepts real dates, so keywords must never reach it."""
    if requested and DATE_RE.fullmatch(requested):
        return requested
    rows = await get_all_contracts(sym)
    today = ist_now().strftime("%Y-%m-%d")
    dates = sorted({d for d in (contract_expiry(r) for r in rows) if d and d >= today})
    if not dates:
        raise HTTPException(status_code=404, detail="No upcoming expiry dates returned by Upstox.")
    if requested in ("current_month", "next_month", "far_month"):
        month_ends = sorted({max(x for x in dates if x[:7] == ym) for ym in {d[:7] for d in dates}})
        idx = {"current_month": 0, "next_month": 1, "far_month": 2}[requested]
        return month_ends[min(idx, len(month_ends) - 1)]
    idx = {"next_week": 1, "far_week": 2}.get(requested, 0)
    return dates[min(idx, len(dates) - 1)]


async def get_chain(sym, expiry):
    ck = (sym, expiry)
    hit = CHAIN_CACHE.get(ck)
    if hit and time.time() - hit["cached_at"] < CHAIN_CACHE_TTL:
        return hit
    data = await upstox_get(f"{API_BASE}/v2/option/chain", {"instrument_key": INDEX_KEYS[sym], "expiry_date": expiry})
    rows = (data or {}).get("data") or []
    entry = {"cached_at": time.time(), "fetched_at": utc_now_iso(), "rows": rows}
    CHAIN_CACHE[ck] = entry
    return entry


async def lot_size_for(sym, expiry):
    try:
        for row in await get_contracts(sym, expiry):
            if row.get("lot_size"):
                return int(row["lot_size"])
    except Exception:
        pass
    return None


def _pick_near_atm_contracts(contracts, spot, max_strikes=12):
    options = [c for c in contracts if c.get("instrument_key") and c.get("instrument_type") in ("CE", "PE")]
    strikes = sorted({float(c["strike_price"]) for c in options if c.get("strike_price") is not None})
    if not strikes:
        return []
    atm = min(strikes, key=lambda x: abs(x - float(spot))) if spot else strikes[len(strikes) // 2]
    keep = set(sorted(strikes, key=lambda x: abs(x - atm))[:max_strikes])
    return [c for c in options if float(c.get("strike_price", -1)) in keep]


# ---------------------------------------------------------------- WebSocket (Upstox V3 SDK)
def _on_stream_message(message):
    """Store the latest decoded SDK event per instrument (see feed_parser for the shapes)."""
    try:
        if isinstance(message, str):
            message = json.loads(message)
        current_ts, parsed = parse_feed_message(message)
        if not parsed:
            return
        now = time.time()
        with STREAM_LOCK:
            for key, p in parsed.items():
                old = LIVE_TICKS.get(key, {})
                LIVE_TICKS[key] = {
                    "instrument_key": key,
                    "ltp": p["ltp"] if p["ltp"] is not None else old.get("ltp"),
                    "oi": p["oi"] if p["oi"] is not None else old.get("oi"),
                    "iv": p["iv"] if p["iv"] is not None else old.get("iv"),
                    "close_price": p.get("close_price") if p.get("close_price") is not None else old.get("close_price"),
                    # level-1 order book: keep the last seen values, but remember WHEN the book was last updated
                    "bid": p.get("bid") if p.get("bid") is not None else old.get("bid"),
                    "ask": p.get("ask") if p.get("ask") is not None else old.get("ask"),
                    "bid_qty": p.get("bid_qty") if p.get("bid_qty") is not None else old.get("bid_qty"),
                    "ask_qty": p.get("ask_qty") if p.get("ask_qty") is not None else old.get("ask_qty"),
                    "vtt": p.get("vtt") if p.get("vtt") is not None else old.get("vtt"),
                    "book_ts": now if (p.get("bid") is not None and p.get("ask") is not None) else old.get("book_ts"),
                    "last_trade_time": p["ltt"] or old.get("last_trade_time"),
                    "received_at": utc_now_iso(),
                    "received_ts": now,
                    "feed_timestamp": current_ts,
                }
    except Exception as exc:
        logger.debug("Could not parse market stream event: %s", exc)


def _reset_streams():
    """Disconnect all streams (used after a new OAuth token so streams re-open with it)."""
    with STREAM_LOCK:
        streams = list(STREAMS.values())
        STREAMS.clear()
        STREAM_ERRORS.clear()
    for s in streams:
        try:
            s["streamer"].disconnect()
        except Exception:
            pass


def _start_stream(symbol, instruments):
    """One Upstox V3 SDK stream per underlying, in a daemon thread. Keys follow the current expiry."""
    new_keys = list(dict.fromkeys(instruments))
    with STREAM_LOCK:
        existing = STREAMS.get(symbol)
        if existing and existing.get("streamer"):
            have = set(existing.get("keys", []))
            add = [k for k in new_keys if k not in have]
            drop = [k for k in have if k not in set(new_keys)]
            try:
                if drop:
                    existing["streamer"].unsubscribe(drop)
                if add:
                    existing["streamer"].subscribe(add, "full")
                existing["keys"] = [k for k in existing["keys"] if k not in drop] + add
            except Exception as exc:
                STREAM_ERRORS[symbol] = f"Subscription update failed: {exc}"
            return
    try:
        import upstox_client
        cfg = upstox_client.Configuration()
        cfg.access_token = token_or_401()
        api_client = upstox_client.ApiClient(cfg)
        streamer = upstox_client.MarketDataStreamerV3(api_client, new_keys, "full")
        streamer.on("message", _on_stream_message)

        def on_error(err):
            with STREAM_LOCK:
                STREAM_ERRORS[symbol] = str(err)[:300]
            logger.warning("Upstox stream error (%s): %s", symbol, err)

        def on_open(*args):
            with STREAM_LOCK:
                STREAM_ERRORS.pop(symbol, None)
            logger.info("Upstox V3 stream opened for %s (%d keys)", symbol, len(new_keys))

        streamer.on("error", on_error)
        streamer.on("open", on_open)
        try:
            streamer.auto_reconnect(True, 5, 10)
        except Exception:
            pass

        def runner():
            try:
                streamer.connect()
            except Exception as exc:
                with STREAM_LOCK:
                    STREAM_ERRORS[symbol] = str(exc)[:300]
                    STREAMS.pop(symbol, None)  # allow a clean restart on the next poll
                logger.exception("Upstox stream stopped for %s", symbol)

        thread = threading.Thread(target=runner, daemon=True, name=f"upstox-{symbol.lower()}")
        with STREAM_LOCK:
            STREAMS[symbol] = {"streamer": streamer, "keys": new_keys, "thread": thread, "started_at": utc_now_iso()}
            STREAM_ERRORS.pop(symbol, None)
        thread.start()
    except Exception as exc:
        with STREAM_LOCK:
            STREAM_ERRORS[symbol] = f"WebSocket unavailable: {exc}. Install/update upstox-python-sdk and verify token/API access."
        logger.warning("Could not start Upstox stream for %s: %s", symbol, exc)


def _stream_keys(sym, option_keys=()):
    """The underlying index is ALWAYS part of the stream so the page gets a real-time index price."""
    return list(dict.fromkeys([INDEX_KEYS[sym], *option_keys]))


def _tick_view(key, now):
    """Tick + contract metadata + per-row age, or None if nothing has been received."""
    tick = LIVE_TICKS.get(key)
    if not tick:
        return None
    age = max(0.0, now - tick.get("received_ts", now))
    meta = STREAM_META.get(key, {})
    return {**meta, **tick, "age_seconds": round(age, 1), "fresh": age <= FRESH_SECONDS}


# ---------------------------------------------------------------- routes
@app.get("/")
async def home():
    return FileResponse(BASE_DIR / "static" / "index.html", headers={"Cache-Control": "no-cache"})



# ================================================================ accounts, login and saved data (v1.10)
class CredsBody(BaseModel):
    username: str = ""
    password: str = ""
    code: str = ""


class PasswordBody(BaseModel):
    old_password: str = ""
    new_password: str = ""


class DeleteBody(BaseModel):
    password: str = ""


class DataBody(BaseModel):
    value: Any = None
    base_version: Optional[int] = None
    force: bool = False


def _current_user(request: Request):
    return request.scope.get("oi_user")


def _need_user(request: Request):
    user = _current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail={"detail": "Login required", "login_required": True})
    return user


def _require_admin(request: Request):
    user = _need_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only the owner account (the first one created) can connect or reconnect Upstox.")


def _client_ip(request: Request):
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else "") or (request.client.host if request.client else "?")


async def _acc(fn, *args, **kwargs):
    """Run a (blocking) account call in a worker thread and turn AccountError into an HTTP error."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except acc.AccountError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


def _set_session_cookie(request: Request, response, token):
    https = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https" or request.url.scheme == "https"
    response.set_cookie(acc.COOKIE_NAME, token, max_age=acc.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=https, path="/")


@app.get("/login")
async def login_page():
    return FileResponse(BASE_DIR / "static" / "login.html", headers={"Cache-Control": "no-store"})


@app.get("/account")
async def account_page():
    return FileResponse(BASE_DIR / "static" / "account.html", headers={"Cache-Control": "no-store"})


@app.get("/api/auth/config")
async def auth_config(request: Request):
    try:
        signup = await asyncio.to_thread(acc.signup_status)
    except Exception as exc:
        logger.warning("Account database unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Account database is unavailable. Check DATABASE_URL.")
    return {"signup": signup, "user": _current_user(request), "auth_disabled": acc.AUTH_DISABLED, "storage": acc.storage_info()}


@app.post("/api/auth/signup")
async def auth_signup(body: CredsBody, request: Request):
    ip = _client_ip(request)
    await _acc(acc.throttle_check, "signup:" + ip, 8, 3600)
    acc.throttle_fail("signup:" + ip)
    user = await _acc(acc.create_user, body.username, body.password, body.code)
    token = await _acc(acc.create_session, user["id"], request.headers.get("user-agent", ""))
    response = JSONResponse({"user": user})
    _set_session_cookie(request, response, token)
    return response


@app.post("/api/auth/login")
async def auth_login(body: CredsBody, request: Request):
    ip, uname = _client_ip(request), acc.normalise_username(body.username)
    for key, limit in (("ipu:" + ip + "|" + uname, 5), ("user:" + uname, 10), ("ip:" + ip, 30)):
        await _acc(acc.throttle_check, key, limit, 600)
    try:
        user = await _acc(acc.authenticate, body.username, body.password)
    except HTTPException:
        for key in ("ipu:" + ip + "|" + uname, "user:" + uname, "ip:" + ip):
            acc.throttle_fail(key)
        raise
    acc.throttle_clear("ipu:" + ip + "|" + uname)
    token = await _acc(acc.create_session, user["id"], request.headers.get("user-agent", ""))
    response = JSONResponse({"user": user})
    _set_session_cookie(request, response, token)
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    await _acc(acc.end_session, request.scope.get("oi_token"))
    response = JSONResponse({"ok": True})
    response.delete_cookie(acc.COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    return {"user": _need_user(request), "storage": acc.storage_info()}


@app.post("/api/auth/password")
async def auth_password(body: PasswordBody, request: Request):
    user = _need_user(request)
    await _acc(acc.change_password, user["id"], body.old_password, body.new_password, request.scope.get("oi_token"))
    return {"ok": True, "note": "Other devices were signed out."}


@app.post("/api/auth/delete")
async def auth_delete(body: DeleteBody, request: Request):
    user = _need_user(request)
    await _acc(acc.delete_user, user["id"], body.password)
    response = JSONResponse({"ok": True})
    response.delete_cookie(acc.COOKIE_NAME, path="/")
    return response


@app.get("/api/userdata")
async def userdata_all(request: Request):
    user = _need_user(request)
    return {"user": user, "data": await _acc(acc.get_all_data, user["id"]), "server_time": time.time()}


@app.put("/api/userdata/{key}")
async def userdata_put(key: str, body: DataBody, request: Request):
    user = _need_user(request)
    try:
        saved = await asyncio.to_thread(acc.put_data, user["id"], key, body.value, body.base_version, body.force)
    except acc.AccountError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    except acc.Conflict as exc:
        return JSONResponse({"detail": "Saved data changed on another device.", "conflict": True, "current": exc.current}, status_code=409)
    return {"ok": True, **saved}


@app.delete("/api/userdata/{key}")
async def userdata_delete(key: str, request: Request):
    user = _need_user(request)
    await asyncio.to_thread(acc.delete_data, user["id"], key)
    return {"ok": True}



# ================================================================ signal diagnostics (v1.11)
DIAG_JOBS = {}
DIAG_LOCK = threading.Lock()


def _diag_prune():
    now = time.time()
    for jid in [j for j, v in DIAG_JOBS.items() if now - v["created"] > 1800 and v["status"] != "running"]:
        DIAG_JOBS.pop(jid, None)


@app.get("/diagnostics")
async def diagnostics_page():
    return FileResponse(BASE_DIR / "static" / "diagnostics.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/diagnostics/start")
async def diagnostics_start(request: Request, symbol: str = "NIFTY", interval: int = 5, days: int = 15, mode: str = "scalp",
                            cost: float = 4.0, target: int = 60, stop: int = 20, horizon: int = 12):
    """Replays the technical engine over the last `days` of candles and measures what happened after every signal.
    Runs in a background thread (a 20-day replay takes several seconds locally and longer on a free instance)."""
    _need_user(request)
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    if mode not in MODES:
        raise HTTPException(status_code=400, detail="mode must be one of: " + ", ".join(MODES))
    if interval not in (1, 3, 5, 15):
        raise HTTPException(status_code=400, detail="interval must be 1, 3, 5 or 15.")
    max_days = max(2, int(2500 / (375 / interval)))
    if not 2 <= days <= min(30, max_days):
        raise HTTPException(status_code=400, detail=f"days must be between 2 and {min(30, max_days)} for a {interval}-minute chart.")
    if not (0 <= cost <= 30 and 5 <= target <= 100 and 5 <= stop <= 100 and 3 <= horizon <= 48):
        raise HTTPException(status_code=400, detail="cost 0-30, target/stop 5-100 points, horizon 3-48 candles.")
    with DIAG_LOCK:
        _diag_prune()
        running = next((j for j, v in DIAG_JOBS.items() if v["status"] == "running"), None)
        if running:
            return {"job_id": running, "already_running": True}
        job_id = secrets.token_urlsafe(8)
        DIAG_JOBS[job_id] = {"status": "running", "created": time.time(), "progress": 0.0, "report": None, "error": None, "stage": "Fetching candles"}
    result = None
    try:
        result = await candles(symbol=sym, interval=interval, days=days)
        htf_interval = 15 if interval < 15 else None
        htf_raw = None
        if htf_interval:
            try:
                htf_raw = (await candles(symbol=sym, interval=htf_interval, days=days))["candles"]
            except HTTPException as exc:
                if exc.status_code == 401:
                    raise
    except HTTPException as exc:
        DIAG_JOBS[job_id].update(status="error", error=_err_text(exc))
        return {"job_id": job_id}
    except Exception as exc:
        DIAG_JOBS[job_id].update(status="error", error=f"{type(exc).__name__}: {str(exc)[:150]}")
        return {"job_id": job_id}

    def work():
        job = DIAG_JOBS[job_id]
        job["stage"] = "Replaying signals bar by bar"
        try:
            rep = dgn.run(result["candles"], symbol=sym, interval=interval, mode=mode, htf_raw=htf_raw, htf_interval=htf_interval,
                          volume_source=result.get("volume_source"), horizon=horizon, cost=cost, target=target, stop=stop,
                          progress=lambda a, b: job.update(progress=round(a / max(1, b), 3)))
            rep["params"]["volume_source"] = result.get("volume_source")
            job.update(status="done", report=rep, progress=1.0, stage="Done")
        except Exception as exc:
            logger.warning("Diagnostics failed: %s", exc)
            job.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:150]}")

    threading.Thread(target=work, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/diagnostics/result")
async def diagnostics_result(request: Request, job_id: str = ""):
    _need_user(request)
    job = DIAG_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown or expired job. Start the analysis again.")
    return {"status": job["status"], "progress": job["progress"], "stage": job["stage"], "error": job["error"], "report": job["report"]}


@app.get("/api/status")
async def status():
    state = await check_token_state()
    return {
        "app": APP_TITLE, "version": APP_VERSION, "provider": "Upstox",
        "token_configured": bool(runtime_token), "token_state": state,
        "oauth_configured": bool(UPSTOX_API_KEY and UPSTOX_API_SECRET and UPSTOX_REDIRECT_URI),
        "mode": "Upstox REST + optional V3 WebSocket; analysis-only; no orders are placed",
        "websocket_sdk_available": importlib.util.find_spec("upstox_client") is not None,
        "tradingview_webhook_configured": bool(os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()),
        "password_protected": True,
        "futures_volume": {"ready": bool(FUTURES["rows"]), "error": FUTURES["error"]},
        "market": market_status(),
    }


@app.get("/api/login")
async def login(request: Request):
    _require_admin(request)
    if not (UPSTOX_API_KEY and UPSTOX_REDIRECT_URI):
        raise HTTPException(status_code=400, detail="Set UPSTOX_API_KEY and UPSTOX_REDIRECT_URI first.")
    state = secrets.token_urlsafe(24)
    oauth_states.add(state)
    params = {"response_type": "code", "client_id": UPSTOX_API_KEY, "redirect_uri": UPSTOX_REDIRECT_URI, "state": state}
    return RedirectResponse("https://api.upstox.com/v2/login/authorization/dialog?" + urlencode(params))


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    global runtime_token
    _require_admin(request)
    if error:
        raise HTTPException(status_code=400, detail=f"Upstox login failed: {error}")
    if not code or not state or state not in oauth_states:
        raise HTTPException(status_code=400, detail="Missing or invalid OAuth state. Start login again.")
    oauth_states.discard(state)
    if not (UPSTOX_API_KEY and UPSTOX_API_SECRET and UPSTOX_REDIRECT_URI):
        raise HTTPException(status_code=400, detail="OAuth environment variables are incomplete.")
    payload = {"code": code, "client_id": UPSTOX_API_KEY, "client_secret": UPSTOX_API_SECRET,
               "redirect_uri": UPSTOX_REDIRECT_URI, "grant_type": "authorization_code"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post("https://api.upstox.com/v2/login/authorization/token", data=payload,
                                 headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"})
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail={"upstox_error": resp.text[:500]})
    new_token = resp.json().get("access_token", "")
    if not new_token:
        raise HTTPException(status_code=502, detail="No access token returned by Upstox.")
    runtime_token = new_token
    TOKEN_CHECK.update({"at": 0.0, "token": None, "state": "UNKNOWN"})
    _reset_streams()  # running streams still hold the old token
    return RedirectResponse("/?connected=1")


@app.get("/api/candles")
async def candles(symbol: str = "NIFTY", interval: int = 5, days: int = 5, instrument_key: str = ""):
    if interval not in [1, 2, 3, 5, 10, 15, 30]:
        raise HTTPException(status_code=400, detail="Supported intervals: 1, 2, 3, 5, 10, 15, 30 minutes.")
    if not 1 <= days <= 30:
        raise HTTPException(status_code=400, detail="days must be between 1 and 30.")
    key = instrument_key.strip() or INDEX_KEYS.get(symbol.upper())
    if not key:
        raise HTTPException(status_code=400, detail="Unknown symbol or instrument key.")
    data = await fetch_candles(key, interval, days)
    volume_source = None
    if not instrument_key.strip() and symbol.upper() in INDEX_KEYS:
        # Indices carry no volume: borrow the front-month future's volume so VWAP / volume checks work.
        try:
            fut = await get_front_future(symbol.upper())
            if fut:
                merged = merge_volume(data, await fetch_candles(fut["instrument_key"], interval, days))
                if merged:
                    data, volume_source = merged, fut["trading_symbol"]
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            logger.info("Futures candles unavailable: %s", str(exc.detail)[:160])
        except Exception as exc:
            logger.info("Futures volume merge skipped: %s", exc)
    return {"symbol": symbol, "instrument_key": key, "interval": interval, "candles": data, "volume_source": volume_source}


@app.get("/api/option-contracts")
async def option_contracts(symbol: str = "NIFTY", expiry_date: str = ""):
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    if expiry_date:
        if not (DATE_RE.fullmatch(expiry_date) or expiry_date in RELATIVE_EXPIRIES):
            raise HTTPException(status_code=400, detail="expiry_date must be YYYY-MM-DD or a supported Upstox expiry keyword.")
        rows = await get_contracts(sym, expiry_date)
        return {"status": "success", "data": rows, "requested_expiry": expiry_date, "source": "Upstox option contract API"}
    rows = await get_all_contracts(sym)
    return {"status": "success", "data": rows, "source": "Upstox option contract API"}


@app.get("/api/option-chain")
async def option_chain(symbol: str = "NIFTY", expiry_date: str = ""):
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    if not DATE_RE.fullmatch(expiry_date or ""):
        raise HTTPException(status_code=400, detail="Choose an actual expiry date. Click Load expiries, then Load chain.")
    entry = await get_chain(sym, expiry_date)
    lot = await lot_size_for(sym, expiry_date)
    return {"status": "success", "expiry": expiry_date, "lot_size": lot, "fetched_at": entry["fetched_at"], "data": entry["rows"]}


def _unavailable(reason, sym, expiry):
    return {"status": "UNAVAILABLE", "reason": str(reason)[:300], "symbol": sym, "expiry": expiry,
            "timestamp": utc_now_iso(), "oi_status": "UNAVAILABLE", "pcr": "UNAVAILABLE",
            "iv_average": "UNAVAILABLE", "websocket": "UNAVAILABLE", "market": market_status()}


@app.get("/api/live-derivatives")
async def live_derivatives(symbol: str = "NIFTY", expiry_date: str = "current_week"):
    """REST snapshot for PCR + chain OI/IV; V3 WebSocket updates near-ATM contracts of the SAME expiry."""
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    key = INDEX_KEYS[sym]
    try:
        expiry = await resolve_expiry(sym, expiry_date)
        entry = await get_chain(sym, expiry)
    except HTTPException as exc:
        return _unavailable(exc.detail, sym, expiry_date)
    rows = entry["rows"]
    if not rows:
        return _unavailable(f"Upstox returned no option-chain rows for expiry {expiry}.", sym, expiry)
    now = time.time()
    spot = rows[0].get("underlying_spot_price")
    try:
        contracts = await get_contracts(sym, expiry)
    except HTTPException as exc:
        contracts = []
        with STREAM_LOCK:
            STREAM_ERRORS[sym] = f"Option contracts unavailable: {str(exc.detail)[:200]}"
    selected = _pick_near_atm_contracts(contracts, spot, max_strikes=12)
    if selected:
        with STREAM_LOCK:
            for c in selected:
                STREAM_META[c["instrument_key"]] = {
                    "trading_symbol": c.get("trading_symbol"), "strike": c.get("strike_price"),
                    "type": c.get("instrument_type"), "expiry": expiry}
        _start_stream(sym, _stream_keys(sym, [c["instrument_key"] for c in selected]))

    total_call_oi = total_put_oi = 0.0
    usable_call = usable_put = 0
    iv_values, chain_out = [], []
    strikes = [float(r["strike_price"]) for r in rows if r.get("strike_price") is not None]
    atm_strike = min(strikes, key=lambda x: abs(x - float(spot))) if strikes and spot else None
    atm_ivs = []
    for row in rows:
        co, po = row.get("call_options") or {}, row.get("put_options") or {}
        cm, pm = co.get("market_data") or {}, po.get("market_data") or {}
        cg, pg = co.get("option_greeks") or {}, po.get("option_greeks") or {}
        if cm.get("oi") is not None:
            total_call_oi += float(cm["oi"]); usable_call += 1
        if pm.get("oi") is not None:
            total_put_oi += float(pm["oi"]); usable_put += 1
        civ, piv = cg.get("iv", cm.get("iv")), pg.get("iv", pm.get("iv"))
        for val in (civ, piv):
            try:
                if val is not None and float(val) > 0:
                    iv_values.append(float(val))
                    if atm_strike is not None and float(row.get("strike_price", -1)) == atm_strike:
                        atm_ivs.append(float(val))
            except (TypeError, ValueError):
                pass
        chain_out.append({
            "strike": row.get("strike_price"),
            "call": {"instrument_key": co.get("instrument_key"), "ltp": cm.get("ltp"), "oi": cm.get("oi"), "prev_oi": cm.get("prev_oi"), "iv": civ},
            "put": {"instrument_key": po.get("instrument_key"), "ltp": pm.get("ltp"), "oi": pm.get("oi"), "prev_oi": pm.get("prev_oi"), "iv": piv},
            "pcr_row": row.get("pcr"),
        })
    aggregate_pcr = total_put_oi / total_call_oi if total_call_oi > 0 and usable_call and usable_put else None

    live_near_atm, ages = [], []
    with STREAM_LOCK:
        stream_error = STREAM_ERRORS.get(sym)
        stream_state = STREAMS.get(sym)
        for c in selected:
            view = _tick_view(c["instrument_key"], now)
            if view:
                live_near_atm.append(view)
                ages.append(view["age_seconds"])
    stream_age = min(ages) if ages else None
    stream_ok = bool(live_near_atm) and stream_age is not None and stream_age <= FRESH_SECONDS
    market = market_status()
    if stream_ok:
        ws_state = "LIVE"
    elif stream_state and not stream_error:
        ws_state = "MARKET_CLOSED" if not market["open"] else "CONNECTING"
    else:
        ws_state = "UNAVAILABLE"
    note = "PCR and chain OI are REST snapshots. Only contract rows in live_near_atm are confirmed WebSocket ticks. Values older than 15 seconds are not labelled live."
    if not market["open"]:
        note += " Market is closed (IST schedule): stale ticks and last-session prices are expected."
    return {
        "status": "LIVE" if stream_ok else "REST_SNAPSHOT",
        "symbol": sym, "underlying_key": key, "expiry": expiry, "expiry_mode_requested": expiry_date,
        "timestamp": utc_now_iso(), "rest_snapshot_timestamp": entry["fetched_at"],
        "chain_snapshot_age_seconds": round(now - entry["cached_at"], 1),
        "spot": spot, "atm_strike": atm_strike,
        "pcr": round(aggregate_pcr, 4) if aggregate_pcr is not None else "UNAVAILABLE",
        "total_call_oi": total_call_oi if usable_call else "UNAVAILABLE",
        "total_put_oi": total_put_oi if usable_put else "UNAVAILABLE",
        "oi_status": "REST_SNAPSHOT" if usable_call and usable_put else "UNAVAILABLE",
        "iv_average": round(sum(iv_values) / len(iv_values), 2) if iv_values else "UNAVAILABLE",
        "atm_iv": round(sum(atm_ivs) / len(atm_ivs), 2) if atm_ivs else None,
        "iv_status": "REST_SNAPSHOT" if iv_values else "UNAVAILABLE",
        "websocket": ws_state, "websocket_error": stream_error,
        "websocket_last_tick_age_seconds": round(stream_age, 1) if stream_age is not None else None,
        "selected_contracts": len(selected), "live_near_atm": live_near_atm,
        "chain": chain_out, "freshness_policy_seconds": FRESH_SECONDS,
        "market": market, "note": note,
    }


@app.websocket("/ws/live")
async def live_socket(websocket: WebSocket, symbol: str = "NIFTY"):
    await websocket.accept()
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        await websocket.close(code=4400)
        return
    index_key = INDEX_KEYS[sym]
    loops = 0
    try:
        while True:
            # (Re)start the stream with at least the index, even before the option chain is loaded.
            if loops % 20 == 0 and runtime_token and not STREAMS.get(sym):
                _start_stream(sym, _stream_keys(sym))
            loops += 1
            now = time.time()
            with STREAM_LOCK:
                stream_error = STREAM_ERRORS.get(sym)
                state = STREAMS.get(sym)
                keys = [k for k in (state.get("keys", []) if state else []) if k != index_key]
                recent = [v for v in (_tick_view(k, now) for k in keys) if v]
                idx = _tick_view(index_key, now)
            index_tick = None
            if idx and idx.get("ltp") is not None:
                index_tick = {"ltp": idx["ltp"], "close_price": idx.get("close_price"), "age_seconds": idx["age_seconds"], "fresh": idx["fresh"]}
            any_fresh = any(x["fresh"] for x in recent) or bool(index_tick and index_tick["fresh"])
            market = market_status()
            stream = "LIVE" if any_fresh else ("MARKET CLOSED" if not market["open"] and (recent or index_tick) else "STALE/CONNECTING")
            await websocket.send_json({"type": "ticks", "symbol": sym, "timestamp": utc_now_iso(), "stream": stream,
                                       "error": stream_error, "index": index_tick, "ticks": recent})
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.debug("Browser websocket closed: %s", exc)


@app.get("/api/index-ltp")
async def index_ltp(symbol: str = "NIFTY"):
    """Latest index price: fresh WebSocket tick if there is one, else a REST LTP (cached 1.5 s).
    The page polls this every few seconds only when the WebSocket is not delivering."""
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    key = INDEX_KEYS[sym]
    now = time.time()
    with STREAM_LOCK:
        view = _tick_view(key, now)
    if view and view["fresh"] and view.get("ltp") is not None:
        return {"status": "LIVE_TICK", "symbol": sym, "ltp": view["ltp"], "close_price": view.get("close_price"),
                "age_seconds": view["age_seconds"], "source": "Upstox V3 WebSocket"}
    hit = LTP_CACHE.get(key)
    if hit and now - hit[0] < LTP_CACHE_TTL:
        return hit[1]
    data = await upstox_get(f"{API_BASE}/v2/market-quote/quotes", {"instrument_key": key})
    qd = (data or {}).get("data") or {}
    q = next((v for v in qd.values() if isinstance(v, dict)), None) or {}
    ltp = q.get("last_price")
    ohlc = q.get("ohlc") or {}
    out = {"status": "REST_QUOTE" if ltp is not None else "UNAVAILABLE", "symbol": sym, "ltp": ltp,
           "close_price": ohlc.get("close") if ohlc.get("close") else None, "age_seconds": 0, "source": "Upstox REST quote",
           "market_open": market_status()["open"]}
    LTP_CACHE[key] = (now, out)
    return out


@app.post("/api/tradingview/webhook")
async def tradingview_webhook(request: Request):
    """Optional TradingView alert receiver. It cannot send backend data back into Pine."""
    expected = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="TradingView webhook is disabled until TRADINGVIEW_WEBHOOK_SECRET is configured.")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook body must be JSON.")
    if not isinstance(payload, dict) or not secrets.compare_digest(str(payload.get("secret", "")), expected):
        raise HTTPException(status_code=401, detail="Invalid webhook secret.")
    app.state.last_tv_alert = {"received_at": utc_now_iso(), "payload": {k: v for k, v in payload.items() if k != "secret"}}
    return {"ok": True, "received_at": app.state.last_tv_alert["received_at"],
            "note": "Alert received only; this does not inject data into Pine or place orders."}


@app.get("/api/tradingview/last-alert")
async def tradingview_last_alert():
    return getattr(app.state, "last_tv_alert", {"status": "UNAVAILABLE", "message": "No TradingView alert received in this running instance."})


@app.get("/api/option-quote")
async def option_quote(instrument_key: str):
    """Latest quote for one option: a fresh WebSocket tick if available, else a labelled REST snapshot."""
    key = instrument_key.strip()
    if not key or "|" not in key:
        raise HTTPException(status_code=400, detail="A valid Upstox option instrument_key is required.")
    now = time.time()
    with STREAM_LOCK:
        view = _tick_view(key, now)
    if view and view["fresh"] and view.get("ltp") is not None:
        book_age = round(now - view["book_ts"], 1) if view.get("book_ts") else None
        book_ok = book_age is not None and book_age <= 15 and (view.get("bid") or 0) > 0 and (view.get("ask") or 0) > 0
        return {"status": "LIVE_TICK", "instrument_key": key, "ltp": view["ltp"], "timestamp": view.get("received_at"),
                "age_seconds": view["age_seconds"], "source": "Upstox V3 WebSocket",
                "bid": view.get("bid") if book_ok else None, "ask": view.get("ask") if book_ok else None,
                "book_age_seconds": book_age}
    data = await upstox_get(f"{API_BASE}/v2/market-quote/ltp", {"instrument_key": key})
    quote_data = (data or {}).get("data") or {}
    quote = quote_data.get(key)
    if quote is None:
        # Upstox keys the response by "EXCHANGE:SYMBOL"; match on instrument_token, else take the only row.
        quote = next((q for q in quote_data.values() if isinstance(q, dict) and q.get("instrument_token") == key), None)
        if quote is None and len(quote_data) == 1:
            quote = next(iter(quote_data.values()))
    ltp = quote.get("last_price", quote.get("ltp")) if isinstance(quote, dict) else None
    if ltp is None:
        return {"status": "UNAVAILABLE", "instrument_key": key, "ltp": None, "timestamp": utc_now_iso(),
                "source": "REST quote", "reason": "No LTP returned for selected contract."}
    return {"status": "REST_SNAPSHOT", "instrument_key": key, "ltp": float(ltp), "timestamp": utc_now_iso(),
            "age_seconds": 0, "source": "Upstox REST quote", "market_open": market_status()["open"],
            "note": "REST snapshot; not a confirmed WebSocket tick."}


SIGNAL_TRACKER = sf.SignalTracker()
FUSION_CACHE = {}
FUSION_TTL = 8          # seconds the dashboard read is reused between terminal refreshes


async def _dashboard_final_for(sym, interval):
    """Data Check dashboard final signal for the main terminal. Cached briefly; never raises (None = unavailable)."""
    hit = FUSION_CACHE.get(sym)
    if hit and time.time() - hit["at"] < FUSION_TTL:
        return hit["final"]
    try:
        blocks = await asyncio.wait_for(
            _dashboard_blocks(sym, "", "current_week", interval if interval in (1, 3, 5, 15) else 5, 0), timeout=12)
        final = dd.final_signal(blocks, market_status()["open"])
        _stamp_dashboard_times(sym, blocks, final)
        FUSION_CACHE[sym] = {"at": time.time(), "final": final}
        return final
    except Exception as exc:
        logger.info("Dashboard signal unavailable for the terminal: %s", str(exc)[:160])
        return hit["final"] if hit and time.time() - hit["at"] < 60 else None


@app.get("/api/analysis")
async def analysis(symbol: str = "NIFTY", interval: int = 5, mode: str = "standard"):
    """Transparent confirmation engine from index candles + a higher-timeframe trend read, fused with the Data Check
    dashboard signal and stamped with the time the signal was generated (v1.9).
    Not a guarantee and not an order instruction."""
    sym = symbol.upper()
    if mode not in MODES:
        raise HTTPException(status_code=400, detail="mode must be one of: " + ", ".join(MODES))
    dash_task = asyncio.create_task(_dashboard_final_for(sym, interval)) if sym in INDEX_KEYS else None
    try:
        result = await candles(symbol=symbol, interval=interval, days=10)
        htf_interval = 15 if interval < 15 else 30 if interval < 30 else None
        htf_raw = None
        if htf_interval:
            try:
                htf_raw = (await candles(symbol=symbol, interval=htf_interval, days=10))["candles"]
            except HTTPException as exc:
                if exc.status_code == 401:
                    raise
                logger.info("Higher-timeframe candles unavailable: %s", str(exc.detail)[:160])
        a = analyze(result["candles"], symbol=symbol, interval=interval, htf_raw=htf_raw, htf_interval=htf_interval,
                    volume_source=result.get("volume_source"), mode=mode)  # the still-forming candle is ignored inside analyze()
        dash = await dash_task if dash_task else None
    except BaseException:
        if dash_task:
            dash_task.cancel()
        raise
    if a.get("status") != "ok":
        return a
    try:
        market = market_status()
        replay = sf.replay_signal_start(result["candles"], interval, a, htf_raw=htf_raw, htf_interval=htf_interval,
                                        volume_source=result.get("volume_source"), mode=mode)
        track = SIGNAL_TRACKER.update(sym, sf.dash_key(dash), a.get("last_close"), time.time())
        fused = sf.fuse(a, dash, replay, track, datetime.now(IST), market["open"], interval, mode=mode)
        a["gates"] = (a.get("gates") or []) + fused["gates"]
        a.update({"final": fused["final"], "timing": fused["timing"], "dashboard": fused["dashboard"],
                  "signal_technical": a.get("signal"), "decision_technical": a.get("decision"),
                  "decision": fused["final"]["action"]})
    except Exception as exc:          # fusion must never take the whole analysis down
        logger.warning("Signal fusion failed: %s", exc)
        a["fusion_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return a



# ================================================================ Data Check dashboard (v1.8)
QUOTE_CACHE = {}
QUOTE_TTL = 5
UNIVERSE_CACHE = {}
UNIVERSE_TTL = 12 * 3600
CSV_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
               "Accept": "text/csv,*/*;q=0.8", "Referer": "https://www.niftyindices.com/"}


@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(BASE_DIR / "static" / "dashboard.html", headers={"Cache-Control": "no-cache"})


async def _fetch_constituents(universe):
    """Constituent list from NSE's own CSV (always current after a rebalance). Nifty 50 falls back to a built-in list."""
    hit = UNIVERSE_CACHE.get(universe)
    if hit and time.time() - hit["at"] < UNIVERSE_TTL:
        return hit
    cfg = dd.UNIVERSES[universe]
    errors = []
    for base in ("https://nsearchives.nseindia.com/content/indices/", "https://www.niftyindices.com/IndexConstituent/"):
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=CSV_HEADERS) as client:
                r = await client.get(base + cfg["file"])
            if r.status_code == 200:
                rows = dd.parse_constituents_csv(r.text)
                if len(rows) >= 10:
                    entry = {"at": time.time(), "rows": rows, "source": "NSE constituent list (live CSV)"}
                    UNIVERSE_CACHE[universe] = entry
                    return entry
                errors.append(f"{base}: only {len(rows)} rows parsed")
            else:
                errors.append(f"{base}: HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"{base}: {str(exc)[:80]}")
    if hit:  # stale list is better than none
        return {**hit, "source": hit["source"] + " (stale - refresh failed)"}
    if universe == "nifty50":
        await ensure_instruments()
        by_sym = {r.get("trading_symbol"): r for r in FUTURES["eq"]}
        rows = [{"symbol": sym, "name": sym, "industry": "", "isin": "", "key": by_sym[sym]["instrument_key"]}
                for sym in dd.FALLBACK_NIFTY50 if sym in by_sym]
        if len(rows) >= 30:
            entry = {"at": time.time() - UNIVERSE_TTL + 900, "rows": rows,
                     "source": f"Built-in fallback list ({len(rows)} of 50 resolved) - may be outdated"}
            UNIVERSE_CACHE[universe] = entry
            return entry
    raise HTTPException(status_code=502, detail="Could not load " + cfg["label"] + " constituents: " + " | ".join(errors))


async def _quotes(keys, ttl=QUOTE_TTL):
    """Upstox full quotes for many instruments (chunks of 100, run concurrently). Cached for a few seconds."""
    keys = list(dict.fromkeys(k for k in keys if k))
    ck = hash(tuple(keys))
    hit = QUOTE_CACHE.get(ck)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    chunks = [keys[i:i + 100] for i in range(0, len(keys), 100)]
    sem = asyncio.Semaphore(4)

    async def one(chunk):
        async with sem:
            return await upstox_get(f"{API_BASE}/v2/market-quote/quotes", {"instrument_key": ",".join(chunk)})

    parts = await asyncio.gather(*(one(c) for c in chunks), return_exceptions=True)
    out, first_err = {}, None
    for p in parts:
        if isinstance(p, Exception):
            first_err = first_err or p
            continue
        out.update(dd.quotes_by_key(p))
    if not out and first_err:
        raise first_err
    QUOTE_CACHE[ck] = (time.time(), out)
    if len(QUOTE_CACHE) > 30:
        for old in sorted(QUOTE_CACHE, key=lambda k: QUOTE_CACHE[k][0])[:10]:
            QUOTE_CACHE.pop(old, None)
    return out


def _err_text(exc):
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        hint = detail.get("hint")
        return hint or json.dumps(detail.get("upstox_error", detail))[:200]
    return str(detail or exc)[:200]


async def _dash_breadth(universe):
    uni = await _fetch_constituents(universe)
    quotes = await _quotes([c["key"] for c in uni["rows"]])
    return dd.advance_decline(uni["rows"], quotes, dd.UNIVERSES[universe]["label"], uni["source"])


async def _dash_sectors():
    await ensure_instruments()
    keys, missing = dd.resolve_sector_keys(FUTURES["idx"]) if FUTURES["idx"] else ({}, [])
    if not keys:  # instrument file unavailable: try the usual names; wrong ones just come back empty
        keys, missing = dict(dd.SECTOR_FALLBACK_KEYS), []
    quotes = await _quotes(list(keys.values()))
    return dd.sector_heatmap(keys, quotes, missing)


def _fresh_option_ticks():
    now = time.time()
    out = {}
    with STREAM_LOCK:
        for key, t in LIVE_TICKS.items():
            if now - t.get("received_ts", 0) <= FRESH_SECONDS and t.get("ltp"):
                out[key] = (t["ltp"], t.get("close_price"))
    return out


async def _dash_option_premium(sym, expiry_req):
    expiry = await resolve_expiry(sym, expiry_req)
    entry = await get_chain(sym, expiry)
    rows = entry["rows"]
    if not rows:
        return dd.unavailable(f"No option-chain rows for {expiry}")
    out = dd.build_option_premium(rows, rows[0].get("underlying_spot_price"), _fresh_option_ticks(), steps=2)
    out["expiry"] = expiry
    out["snapshot_age_seconds"] = round(time.time() - entry["cached_at"], 1)
    return out


async def _dash_zone(sym):
    result = await candles(symbol=sym, interval=5, days=6)
    cs = normalize_candles(result["candles"])
    price = None
    with STREAM_LOCK:
        view = _tick_view(INDEX_KEYS[sym], time.time())
    if view and view["fresh"] and view.get("ltp") is not None:
        price = view["ltp"]
    else:
        try:
            q = await index_ltp(symbol=sym)
            price = q.get("ltp")
        except Exception:
            price = None
    label = "Nifty" if sym == "NIFTY" else "Bank Nifty"
    return dd.index_zone(cs, price, label)


async def _safe_block(coro):
    try:
        return await coro
    except HTTPException as exc:
        return dd.unavailable(_err_text(exc))
    except Exception as exc:
        logger.warning("Dashboard block failed: %s", exc)
        return dd.unavailable(f"{type(exc).__name__}: {str(exc)[:150]}")


async def _dashboard_blocks(sym, universe, expiry, interval, offset):
    """The five Data Check blocks (each degrades to UNAVAILABLE on its own). Shared by /dashboard and the main terminal."""
    universe = universe or ("niftybank" if sym == "BANKNIFTY" else "nifty50")
    breadth, sectors, premium, zone, graph = await asyncio.gather(
        _safe_block(_dash_breadth(universe)), _safe_block(_dash_sectors()), _safe_block(_dash_option_premium(sym, expiry)),
        _safe_block(_dash_zone(sym)), _safe_block(_dash_graph(sym, expiry, interval, offset)))
    return {"breadth": breadth, "sectors": sectors, "premium": premium, "zone": zone, "graph": graph}


DASH_TRACKER = sf.SignalTracker()


def _stamp_dashboard_times(sym, blocks, final):
    sf.stamp_dashboard_times(DASH_TRACKER, sym, blocks, final, time.time())


@app.get("/api/dashboard/summary")
async def dashboard_summary(symbol: str = "NIFTY", universe: str = "", expiry: str = "current_week", interval: int = 5, offset: int = 0):
    sym = symbol.upper()
    _check_graph_args(sym, interval, offset)
    universe = universe or ("niftybank" if sym == "BANKNIFTY" else "nifty50")
    if universe not in dd.UNIVERSES:
        raise HTTPException(status_code=400, detail="universe must be one of: " + ", ".join(dd.UNIVERSES))
    if expiry not in RELATIVE_EXPIRIES and not DATE_RE.fullmatch(expiry):
        raise HTTPException(status_code=400, detail="Invalid expiry.")

    blocks = await _dashboard_blocks(sym, universe, expiry, interval, offset)
    sigs = {k: v.get("signal") for k, v in blocks.items()}
    market = market_status()
    final = dd.final_signal(blocks, market["open"])
    _stamp_dashboard_times(sym, blocks, final)
    return {"symbol": sym, "universe": universe, "generated_at": utc_now_iso(), "market": market, **blocks,
            "overall": dd.overall_bias(sigs), "final": final,
            "note": "Signals are rule-based reads of live data, not trade instructions. Quotes are REST snapshots (a few seconds old)."
                    + ("" if market["open"] else " Market is closed: values show the last session.")}


async def _dash_graph(sym, expiry, interval, offset):
    """CE and PE premium candles for the latest session. offset = strikes away from ATM (+ = OTM, - = ITM)."""
    exp = await resolve_expiry(sym, expiry)
    entry = await get_chain(sym, exp)
    rows = sorted((r for r in entry["rows"] if r.get("strike_price") is not None), key=lambda r: float(r["strike_price"]))
    if not rows:
        return dd.unavailable(f"No option-chain rows for {exp}")
    spot = float(rows[0].get("underlying_spot_price") or 0)
    atm_i = min(range(len(rows)), key=lambda i: abs(float(rows[i]["strike_price"]) - spot))
    ce_row = rows[min(len(rows) - 1, max(0, atm_i + offset))]
    pe_row = rows[min(len(rows) - 1, max(0, atm_i - offset))]
    ce_key = (ce_row.get("call_options") or {}).get("instrument_key")
    pe_key = (pe_row.get("put_options") or {}).get("instrument_key")
    if not ce_key or not pe_key:
        return dd.unavailable("Selected strike has no CE/PE instrument key")

    async def get(key):
        try:
            return normalize_candles(await fetch_candles(key, interval, 4))
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            return []

    ce_c, pe_c = await asyncio.gather(get(ce_key), get(pe_key))
    out = dd.build_premium_graph(
        ce_c, pe_c,
        {"strike": float(ce_row["strike_price"]), "key": ce_key, "prev_close": ((ce_row.get("call_options") or {}).get("market_data") or {}).get("close_price")},
        {"strike": float(pe_row["strike_price"]), "key": pe_key, "prev_close": ((pe_row.get("put_options") or {}).get("market_data") or {}).get("close_price")})
    out.update({"expiry": exp, "interval": interval, "offset": offset, "atm_strike": float(rows[atm_i]["strike_price"])})
    return out


def _check_graph_args(sym, interval, offset):
    if sym not in INDEX_KEYS:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    if interval not in (1, 3, 5, 15):
        raise HTTPException(status_code=400, detail="interval must be 1, 3, 5 or 15.")
    if not -3 <= offset <= 3:
        raise HTTPException(status_code=400, detail="offset must be between -3 and 3.")


@app.get("/api/dashboard/premium-graph")
async def dashboard_premium_graph(symbol: str = "NIFTY", expiry: str = "current_week", interval: int = 5, offset: int = 0):
    sym = symbol.upper()
    _check_graph_args(sym, interval, offset)
    try:
        return await _dash_graph(sym, expiry, interval, offset)
    except HTTPException as exc:
        return dd.unavailable(_err_text(exc))


@app.get("/health")
async def health():
    return {"ok": True}
