import os, re, base64, secrets, threading, time, asyncio, json, logging, importlib.util, sqlite3, csv, io
from datetime import date, timedelta, datetime, timezone, time as dtime
from pathlib import Path
from urllib.parse import urlencode
from typing import Any, Optional
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware

from indicators import IST, MODES, analyze, drop_forming, merge_volume, normalize_candles, parse_time
from instruments import FutureScanner, pick_front_future
from feed_parser import parse_feed_message
import accounts as acc
import dashboard_data as dd
import diagnostics as dgn
import signal_fusion as sf
import signal_exits as sx
import notifier
import option_plan as op
import signal_board as sb

# Resolve the project directory before any path-backed configuration is initialized.
BASE_DIR = Path(__file__).resolve().parent

# Push delivery is optional. Secrets stay server-side; unsupported/unconfigured channels fail closed.
# Device registrations and notification history are stored in the accounts database (PostgreSQL when DATABASE_URL is
# set), so they survive Render restarts/redeploys. All DB calls are blocking, so they run in a worker thread.
def _push_owner_id():
    try: return int(os.getenv("PUSH_OWNER_USER_ID", "0") or 0)
    except ValueError: return 0

def _push_user(request):
    u = request.scope.get("oi_user") or {}
    return int(u.get("id") if isinstance(u, dict) else getattr(u, "id", 0) or 0)

# ---- per-account notification type (v1.18.13) --------------------------------------------------------------
# "all"       = every signal change: setup detected (STARTED), CONFIRMED, FAILED, and any other change of the final signal (CHANGED)
# "confirmed" = only CONFIRMED buy signals. System notices (token expired, monitor offline, day summary, test) always arrive.
PUSH_PREF_MODES = ("all", "confirmed")
_GATED_EVENTS = ("STARTED", "FAILED", "CHANGED", "REJECTED")   # v1.19: REJECTED = confirmed setup that failed the profit check
_PUSH_PREF_CACHE = {}

def _event_allowed(pref, event):
    return pref == "all" or event not in _GATED_EVENTS

def _user_pref(uid, fresh=False):
    """Blocking (database). Cached 30 s so a burst of alerts does not hit the database per device."""
    hit = _PUSH_PREF_CACHE.get(uid)
    if hit and not fresh and time.time() - hit[0] < 30:
        return hit[1]
    try: val = acc.meta_get(f"push_pref:{uid}")
    except Exception: return hit[1] if hit else "all"
    val = val if val in PUSH_PREF_MODES else "all"
    _PUSH_PREF_CACHE[uid] = (time.time(), val)
    return val

async def _rows_for_event(rows, event):
    """Keep only the devices whose owner wants this kind of event."""
    if event not in _GATED_EVENTS or not rows:
        return rows
    uids = {r["user_id"] for r in rows}
    prefs = await asyncio.to_thread(lambda: {u: _user_pref(u) for u in uids})
    return [r for r in rows if _event_allowed(prefs.get(r["user_id"], "all"), event)]

async def _send_native_fcm(title, body, data=None):
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw: return {"configured": False, "sent": 0}
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
        try: app_firebase = firebase_admin.get_app()
        except ValueError: app_firebase = firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
        rows = await _rows_for_event(await asyncio.to_thread(acc.push_devices, "native", _push_owner_id()), (data or {}).get("event"))
        sent = 0
        for row in rows:
            try:
                from datetime import timedelta as _td
                msg = messaging.Message(notification=messaging.Notification(title=title, body=body),
                                        android=messaging.AndroidConfig(priority="high", ttl=_td(minutes=10)),
                                        data={k: str(v) for k, v in (data or {}).items()}, token=row["endpoint"])
                await asyncio.to_thread(messaging.send, msg, app=app_firebase); sent += 1
            except Exception as exc:
                if isinstance(exc, getattr(messaging, "UnregisteredError", ())) or 'not-registered' in str(exc).lower() or 'invalid-registration-token' in str(exc).lower() or 'requested entity was not found' in str(exc).lower():
                    await asyncio.to_thread(acc.push_delete_device, row["id"])
                else: logger.warning("FCM send failed: %s", str(exc)[:140])
        return {"configured": True, "sent": sent}
    except Exception as exc:
        logger.warning("FCM delivery unavailable: %s", str(exc)[:180]); return {"configured": True, "sent": 0, "error": str(exc)[:160]}

async def _send_web_push(title, body, data=None):
    """Web Push to every registered phone/browser. TTL + high urgency matter: with TTL 0 (the library default) the push service
    drops the message if the phone is asleep/in Doze at that instant, which looks exactly like 'alerts stop when the app is closed'."""
    subject = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com").strip()
    try: public, private = await asyncio.to_thread(acc.vapid_keys)
    except Exception as exc:
        logger.warning("Web push keys unavailable: %s", str(exc)[:140]); return {"configured": False, "sent": 0, "devices": 0, "error": "keys unavailable"}
    try:
        from pywebpush import webpush
    except Exception as exc:
        logger.warning("pywebpush not installed: %s", str(exc)[:140]); return {"configured": False, "sent": 0, "devices": 0, "error": "pywebpush not installed"}
    try:
        rows = await _rows_for_event(await asyncio.to_thread(acc.push_devices, "web", _push_owner_id()), (data or {}).get("event"))
    except Exception as exc:
        logger.warning("Push devices unreadable: %s", str(exc)[:140]); return {"configured": True, "sent": 0, "devices": 0, "error": "database unavailable"}
    payload = json.dumps({"title": title, "body": body, "url": "/", "data": data or {}})
    ttl = int(os.getenv("PUSH_TTL_SECONDS", "600") or 600)

    async def one(row):
        try:
            await asyncio.to_thread(webpush, subscription_info=json.loads(row["payload"]), data=payload, vapid_private_key=private,
                                    vapid_claims={"sub": subject}, timeout=10, ttl=ttl, headers={"Urgency": "high"})
            return ("ok", None)
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            if code in (401, 403, 404, 410):
                # 404/410: subscription gone. 401/403: the push service rejects our key for this subscription (keys changed, or
                # an old registration). A 400 is NOT treated as dead (v1.18.12): it can be a bad request, and deleting on it made phones lose their registration. Either way it can never work again - drop it so the phone re-registers itself on next open.
                await asyncio.to_thread(acc.push_delete_device, row["id"])
                return ("dropped", f"HTTP {code}")
            logger.warning("Web push failed: %s", str(exc)[:160]); return ("failed", f"{type(exc).__name__}: {str(exc)[:100]}")
    res = await asyncio.gather(*[one(r) for r in rows]) if rows else []
    out = {"configured": True, "devices": len(rows), "sent": sum(1 for s, _ in res if s == "ok"),
           "dropped": sum(1 for s, _ in res if s == "dropped"), "failed": sum(1 for s, _ in res if s == "failed"),
           "errors": [e for s, e in res if e][:3]}
    if rows and not out["sent"]:
        logger.warning("Web push reached 0 of %s device(s): %s", len(rows), out["errors"])
    return out

async def _dispatch_confirmed_push(title, body, sym, side, event):
    return await asyncio.gather(_send_native_fcm(title,body,{"symbol":sym,"side":side,"event":event}), _send_web_push(title,body,{"symbol":sym,"side":side,"event":event}), return_exceptions=True)

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

APP_VERSION = "1.19.9"


class NoCacheStaticFiles(StaticFiles):
    """Static files revalidate on every load (ETag -> cheap 304) so a redeploy is never masked by a stale cached app.js."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title=APP_TITLE, version=APP_VERSION)
app.add_middleware(acc.SessionAuthMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=700, compresslevel=3)   # v1.18.11: JSON/JS/CSS compress ~5-8x, a big win on mobile data   # login required everywhere except /health, /login and the TradingView webhook
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


# ---- v1.19.2: real trading calendar. Upstox publishes each day's exchange timings (empty on a holiday, special hours on e.g. Muhurat
# trading) at /v2/market/timings/{date} - no login needed. Cached per day; until it is known the regular Mon-Fri 09:15-15:30 schedule is used.
_TRADING_DAY = {}            # "YYYY-MM-DD" -> {"open_day": bool, "start": time, "end": time, "source": str, "note": str}
_TRADING_DAY_BUSY = set()


async def refresh_trading_day(d=None):
    d = d or ist_now().date()
    key = d.isoformat()
    if key in _TRADING_DAY or key in _TRADING_DAY_BUSY:
        return _TRADING_DAY.get(key)
    _TRADING_DAY_BUSY.add(key)
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(f"{API_BASE}/v2/market/timings/{key}", headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        rows = (r.json() or {}).get("data") or []
        nse = next((x for x in rows if str(x.get("exchange", "")).upper() == "NSE"), None) or \
              next((x for x in rows if str(x.get("exchange", "")).upper() == "NFO"), None)
        if nse and nse.get("start_time") and nse.get("end_time"):
            st = datetime.fromtimestamp(float(nse["start_time"]) / 1000, IST).time()
            en = datetime.fromtimestamp(float(nse["end_time"]) / 1000, IST).time()
            info = {"open_day": True, "start": st, "end": en, "source": "Upstox market timings",
                    "note": f"NSE open {st.strftime('%H:%M')}-{en.strftime('%H:%M')} IST today (Upstox market timings)"}
        else:
            info = {"open_day": False, "start": None, "end": None, "source": "Upstox market timings",
                    "note": "NSE is closed today (holiday / weekend per Upstox market timings)"}
        _TRADING_DAY[key] = info
        if len(_TRADING_DAY) > 20:
            for k in sorted(_TRADING_DAY)[:-10]:
                _TRADING_DAY.pop(k, None)
        return info
    except Exception as exc:
        logger.info("Market timings unavailable: %s", str(exc)[:120])
        return None
    finally:
        _TRADING_DAY_BUSY.discard(key)


def _session_for(dt):
    info = _TRADING_DAY.get(dt.date().isoformat())
    if info:
        return info
    if dt.weekday() >= 5:
        return {"open_day": False, "start": None, "end": None, "source": "weekday schedule", "note": "Weekend"}
    return {"open_day": True, "start": dtime(9, 15), "end": dtime(15, 30), "source": "weekday schedule",
            "note": "Regular NSE schedule (Mon-Fri 09:15-15:30 IST); today's holiday status not confirmed yet."}


def in_session(dt=None, grace_minutes=0):
    """True while NSE is open (plus `grace_minutes` after the close), using the real calendar when known."""
    dt = (dt or ist_now()).astimezone(IST)
    info = _session_for(dt)
    if not info["open_day"]:
        return False
    end = (datetime.combine(dt.date(), info["end"]) + timedelta(minutes=grace_minutes)).time()
    return info["start"] <= dt.time() <= end


def market_status():
    now = ist_now()
    info = _session_for(now)
    return {"open": in_session(now), "trading_day": info["open_day"], "ist_time": now.strftime("%Y-%m-%d %H:%M:%S"), "note": info["note"]}


def token_or_401():
    if not runtime_token:
        raise HTTPException(
            status_code=401,
            detail="Upstox access token not configured. Add UPSTOX_ACCESS_TOKEN in Render environment variables, or use Connect Upstox."
        )
    return runtime_token


_UPSTOX_CLIENT = None
_UPSTOX_SEM = asyncio.Semaphore(int(os.getenv("UPSTOX_MAX_CONCURRENT", "8") or 8))
_UPSTOX_INFLIGHT = {}


def _upstox_client():
    """One keep-alive connection pool for every Upstox call. A new client per request meant a fresh TLS handshake (hundreds of
    ms from a cloud region to India) on each of the 8+ calls behind a single refresh - the main reason data arrived slowly."""
    global _UPSTOX_CLIENT
    if _UPSTOX_CLIENT is None or _UPSTOX_CLIENT.is_closed:
        _UPSTOX_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=6.0),
                                           limits=httpx.Limits(max_connections=16, max_keepalive_connections=10, keepalive_expiry=60.0))
    return _UPSTOX_CLIENT


async def upstox_get(url, params=None):
    """GET from Upstox. Identical requests made at the same moment (page + analysis + dashboard + alert poller) share ONE upstream
    call, calls are limited to a few at a time, and a rate-limit (429) / gateway error / timeout is retried briefly."""
    import copy
    token = token_or_401()
    key = (url, tuple(sorted((params or {}).items())), token)
    task = _UPSTOX_INFLIGHT.get(key)
    if task is None:
        task = _UPSTOX_INFLIGHT[key] = asyncio.create_task(_upstox_get_once(url, params, token))
        task.add_done_callback(lambda t, k=key: (_UPSTOX_INFLIGHT.pop(k, None), t.exception() if not t.cancelled() else None))
    return copy.deepcopy(await asyncio.shield(task))


async def _upstox_get_once(url, params, token):
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    response = None
    for attempt in range(3):
        try:
            async with _UPSTOX_SEM:
                response = await _upstox_client().get(url, params=params, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt < 2:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            raise HTTPException(status_code=504, detail={"upstox_error": f"Upstox did not answer ({type(exc).__name__})",
                                                         "hint": "Network or Upstox is slow; the next refresh will retry."})
        if response.status_code in (429, 502, 503, 504) and attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1) + (hash(url) % 300) / 1000)
            continue
        break
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
        client = _upstox_client()
        r = await client.get(f"{API_BASE}/v2/user/profile", headers=headers, timeout=6.0)
        if r.status_code in (401, 403):
            # Read-only / analytics tokens cannot read the profile but work for market data,
            # so only call the token expired if a real market-data request is rejected too.
            r = await client.get(f"{API_BASE}/v2/market-quote/ltp", params={"instrument_key": INDEX_KEYS["NIFTY"]}, headers=headers, timeout=6.0)
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
def _apply_ltp(candles, ltp, interval):
    """Fold one price into the candle list: stretch the forming bar, or open the new bar(s) the REST candle endpoint has not published yet."""
    try:
        last = candles[-1]
        lt = parse_time(last["time"]).timestamp()
    except Exception:
        return candles
    now = time.time()
    step = interval * 60
    if parse_time(last["time"]).astimezone(IST).date() != ist_now().date():
        return candles
    k = int((now - lt) // step)
    out = list(candles)
    if k <= 0:
        out[-1] = {**last, "high": max(last["high"], ltp), "low": min(last["low"], ltp), "close": ltp}
    else:
        bar_start = datetime.fromtimestamp(lt + k * step, IST)
        out.append({"time": bar_start.isoformat(), "open": ltp, "high": ltp, "low": ltp, "close": ltp, "volume": 0, "oi": None})
    return out


def overlay_live_tick(candles, key, interval):
    """Fold the latest fresh WebSocket tick into the candle list so a REST refresh never shows older data than the
    stream already has. Upstox's intraday-candle endpoint lags the market by tens of seconds; the tick does not."""
    if not candles or not interval:
        return candles
    now = time.time()
    with STREAM_LOCK:
        view = _tick_view(key, now)
    if not view or not view.get("fresh") or not view.get("ltp") or not market_status()["open"]:
        return candles
    return _apply_ltp(candles, float(view["ltp"]), interval)


async def overlay_index_price(candles, key, sym, interval):
    """Index candles: prefer the WebSocket tick; when the stream is idle fall back to the REST quote (1.5 s cache) so the
    forming bar and the analysis never trail the market by the candle endpoint's lag. Returns (candles, source, bars_behind)."""
    if not candles or not interval or not market_status()["open"]:
        return candles, "CLOSED", 0
    behind = 0
    try:
        lt = parse_time(candles[-1]["time"]).timestamp()
        behind = max(0, int((time.time() - lt) // (interval * 60)))
    except Exception:
        pass
    with STREAM_LOCK:
        view = _tick_view(key, time.time())
    if view and view.get("fresh") and view.get("ltp"):
        return _apply_ltp(candles, float(view["ltp"]), interval), "WEBSOCKET", behind
    try:
        q = await index_ltp(symbol=sym)
        ltp = float(q.get("ltp"))
        return _apply_ltp(candles, ltp, interval), "REST_QUOTE", behind
    except Exception as exc:
        logger.info("REST price overlay skipped: %s", exc)
        return candles, "NONE", behind


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


PINNED_KEYS = {}     # symbol -> {instrument_key: last_pinned_ts}: contracts the user is looking at (selected option, dashboard ATM legs)
PIN_TTL = 6 * 3600
MAX_PINNED = 30


def _pin_keys(symbol, keys):
    """Remember contracts that must stay subscribed even when they fall outside the near-ATM set, and subscribe them now.
    Without this the option you pick on the chart was never in the WebSocket and only came from slow REST candles."""
    now = time.time()
    with STREAM_LOCK:
        pins = PINNED_KEYS.setdefault(symbol, {})
        for k in keys:
            if k:
                pins[k] = now
        for k in [k for k, ts in pins.items() if now - ts > PIN_TTL]:
            pins.pop(k, None)
        while len(pins) > MAX_PINNED:
            pins.pop(min(pins, key=pins.get), None)
        existing = STREAMS.get(symbol)
        have = list(existing.get("keys", [])) if existing else []
    if runtime_token:
        _start_stream(symbol, have or _stream_keys(symbol))


def _start_stream(symbol, instruments):
    """One Upstox V3 SDK stream per underlying, in a daemon thread. Keys follow the current expiry."""
    with STREAM_LOCK:
        pinned = list(PINNED_KEYS.get(symbol, {}))
    new_keys = list(dict.fromkeys([*instruments, *pinned]))
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
@app.get("/sw.js")
async def service_worker():
    """Service worker must be served from the site root so it can control every page. The version is stamped in so each
    release gets a fresh cache."""
    body = (BASE_DIR / "static" / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", APP_VERSION)
    return Response(body, media_type="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(BASE_DIR / "static" / "favicon.ico", media_type="image/x-icon", headers={"Cache-Control": "public, max-age=86400"})


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
                            cost: float = 4.0, target: int = 30, stop: int = 20, horizon: int = 12, score_min: int = 70):
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
    if score_min not in (0, 60, 65, 70):
        raise HTTPException(status_code=400, detail="score_min must be 0, 60, 65 or 70.")
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
                          volume_source=result.get("volume_source"), horizon=horizon, cost=cost, target=target, stop=stop, score_min=score_min,
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
    # Never make the page wait for Upstox: use the last known token state if the check is slow (it keeps running and updates the cache).
    try:
        state = await asyncio.wait_for(asyncio.shield(asyncio.ensure_future(check_token_state())), timeout=2.0)
    except asyncio.TimeoutError:
        state = TOKEN_CHECK.get("state") or "UNKNOWN"
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
    if instrument_key.strip():                       # option contract: pin it to the stream and show the live bar
        if symbol.upper() in INDEX_KEYS:
            _pin_keys(symbol.upper(), [key])
        data = overlay_live_tick(data, key, interval)
    volume_source = None
    feed = None
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
        # Last step, after the volume merge: fold in the freshest price (WebSocket tick, else REST quote).
        data, src, behind = await overlay_index_price(data, key, symbol.upper(), interval)
        feed = {"price_source": src, "bars_behind": behind, "server_time": utc_now_iso()}
    return {"symbol": symbol, "instrument_key": key, "interval": interval, "candles": data, "volume_source": volume_source, "feed": feed}


@app.get("/api/pin-option")
async def pin_option(symbol: str = "NIFTY", instrument_key: str = ""):
    """Subscribe one option contract to the live WebSocket (called when you select it on the chart)."""
    sym = symbol.upper()
    key = instrument_key.strip()
    if sym not in INDEX_KEYS or "|" not in key:
        raise HTTPException(status_code=400, detail="Valid symbol and option instrument_key required.")
    _pin_keys(sym, [key])
    return {"status": "PINNED", "instrument_key": key}


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


WS_TICK_SEC = float(os.getenv("WS_TICK_SEC", "0.1"))       # v1.19.7: index + option prices checked 10x a second, pushed when they move
WS_PUSH_SEC = float(os.getenv("WS_PUSH_SEC", "0.1"))       # v1.19.7: candles + analysis checked 10x a second, pushed when newer


def _ws_sub(msg, cur):
    """Validate a browser subscription {type:"sub", interval, mode, score_min, reconfirm}. Bad fields keep the current value."""
    out = dict(cur)
    try:
        iv = int(msg.get("interval", out.get("interval", 5)))
        if iv in (1, 2, 3, 5, 10, 15, 30):
            out["interval"] = iv
        if msg.get("mode") in MODES:
            out["mode"] = msg["mode"]
        sm = int(msg.get("score_min", out.get("score_min", 70)))
        if sm in (60, 65, 70):
            out["score_min"] = sm
        rc = int(msg.get("reconfirm", out.get("reconfirm", -1)))
        if rc == -1 or 0 <= rc <= 600:
            out["reconfirm"] = rc
        out["view"] = "option" if msg.get("view") == "option" else "index"
        if msg.get("rate") is not None:
            r = float(msg["rate"])
            if r == r:                                      # not NaN
                out["rate"] = min(2.0, max(0.1, r))         # v1.19.8: the page's "Auto refresh" choice, 0.1-2 s
    except (TypeError, ValueError):
        pass
    return out


@app.websocket("/ws/live")
async def live_socket(websocket: WebSocket, symbol: str = "NIFTY"):
    """Real-time feed for one page (v1.19.6).

    Before: ticks every 0.5 s here, while the chart (10 days of candles) and the analysis were re-downloaded by the page every
    2-4 s over REST - so the score, decision and buy plan always lagged and a phone spent its time on large JSON downloads.
    Now the page sends {"type":"sub", interval, mode, score_min, reconfirm} once and this socket pushes:
      * "index"    - the index price whenever it moves (every WS_TICK_SEC, default 0.25 s)
      * "ticks"    - all subscribed option ticks, once a second
      * "bars"     - the last 3 candles of the chart interval whenever they change (forming bar incl. volume)
      * "analysis" - the shared analysis result the moment a newer one is computed (score, decision, buy plan, reconfirm)
    REST stays as the fallback and for the first full load."""
    await websocket.accept()
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        await websocket.close(code=4400)
        return
    index_key = INDEX_KEYS[sym]
    send_lock = asyncio.Lock()
    sub = {"interval": None, "mode": "standard", "score_min": 70, "reconfirm": -1, "view": "index"}
    sent = {"an": None, "bars": None, "bars_src": None, "idx": None, "opt": {}, "ticks_at": 0.0, "push_at": 0.0}
    pump_task = None

    async def send(obj):
        async with send_lock:
            await websocket.send_json(obj)

    async def reader():
        while True:
            msg = await websocket.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "sub":
                new = _ws_sub(msg, sub)
                if new != sub:
                    sub.clear()
                    sub.update(new)
                    sent["an"] = sent["bars"] = None       # settings changed: push the matching result straight away
                    sent["push_at"] = 0.0

    async def pump():
        iv = sub.get("interval")
        if not iv or not runtime_token:
            return
        snap = dict(sub)
        try:
            # v1.19.7: checked every 0.1 s, so only do work when the server-side caches actually hold something newer.
            ck = CANDLE_CACHE.get((index_key, iv, 10))
            bars_due = sent["bars"] is None or not ck or ck[0] != sent["bars_src"] or time.time() - ck[0] >= CANDLE_CACHE_TTL
            if snap["view"] == "index" and bars_due:
                c = await candles(symbol=sym, interval=iv, days=10)
                ck = CANDLE_CACHE.get((index_key, iv, 10))
                sent["bars_src"] = ck[0] if ck else None
                tail = (c.get("candles") or [])[-3:]
                sig = json.dumps(tail, sort_keys=True, default=str)
                if tail and sig != sent["bars"] and snap == sub:
                    sent["bars"] = sig
                    await send({"type": "bars", "symbol": sym, "interval": iv, "candles": tail, "feed": c.get("feed"),
                                "volume_source": c.get("volume_source"), "timestamp": utc_now_iso()})
            akey = (sym, iv, snap["mode"], snap["score_min"], snap["reconfirm"])
            hit = _AN_CACHE.get(akey)
            if hit and hit[0] == sent["an"] and (time.time() - hit[0] < AN_FRESH or akey in _AN_TASKS):
                return                                      # nothing newer yet, and a recompute is not due / already running
            a = await analysis(symbol=sym, interval=iv, mode=snap["mode"], score_min=snap["score_min"], reconfirm=snap["reconfirm"])
            hit = _AN_CACHE.get(akey)
            stamp = hit[0] if hit else (a.get("timing") or {}).get("generated_at")
            if stamp != sent["an"] and snap == sub:
                sent["an"] = stamp
                await send({"type": "analysis", "symbol": sym, "interval": iv, "mode": snap["mode"], "score_min": snap["score_min"],
                            "reconfirm": snap["reconfirm"], "data": a, "timestamp": utc_now_iso()})
        except HTTPException as exc:
            logger.debug("ws push skipped: %s", str(exc.detail)[:120])
        except (WebSocketDisconnect, RuntimeError):
            raise
        except Exception as exc:
            logger.info("ws push failed: %s", exc)

    read_task = asyncio.create_task(reader())
    loops = 0
    try:
        while True:
            if read_task.done():
                break                                       # browser went away (or sent garbage)
            # (Re)start the stream with at least the index, even before the option chain is loaded (~every 10 s).
            if loops % max(1, int(10 / WS_TICK_SEC)) == 0 and runtime_token and not STREAMS.get(sym):
                _start_stream(sym, _stream_keys(sym))
            loops += 1
            now = time.time()
            with STREAM_LOCK:
                stream_error = STREAM_ERRORS.get(sym)
                state = STREAMS.get(sym)
                keys = [k for k in (state.get("keys", []) if state else []) if k != index_key]
                want_all = now - sent["ticks_at"] >= 1.0
                recent = [v for v in (_tick_view(k, now) for k in keys) if v]
                idx = _tick_view(index_key, now)
            index_tick = None
            if idx and idx.get("ltp") is not None:
                index_tick = {"ltp": idx["ltp"], "close_price": idx.get("close_price"), "age_seconds": idx["age_seconds"], "fresh": idx["fresh"]}
            market = market_status()
            if want_all:
                sent["ticks_at"] = now
                any_fresh = any(x["fresh"] for x in recent) or bool(index_tick and index_tick["fresh"])
                stream = "LIVE" if any_fresh else ("MARKET CLOSED" if not market["open"] and (recent or index_tick) else "STALE/CONNECTING")
                await send({"type": "ticks", "symbol": sym, "timestamp": utc_now_iso(), "stream": stream,
                            "error": stream_error, "index": index_tick, "ticks": recent})
                sent["idx"] = index_tick and index_tick["ltp"]
                sent["opt"] = {x.get("instrument_key"): x.get("ltp") for x in recent}
            else:
                if index_tick and index_tick["fresh"] and index_tick["ltp"] != sent["idx"]:
                    sent["idx"] = index_tick["ltp"]
                    await send({"type": "index", "symbol": sym, "index": index_tick})
                # v1.19.7: option premiums that moved since the last push (the selected contract's candle moves 10x a second)
                moved = [x for x in recent if x.get("fresh") and sent["opt"].get(x.get("instrument_key")) != x.get("ltp")]
                if moved:
                    for x in moved:
                        sent["opt"][x.get("instrument_key")] = x.get("ltp")
                    await send({"type": "opt", "symbol": sym, "ticks": moved})
            every = max(WS_PUSH_SEC, sub.get("rate") or 0) if market["open"] else 15.0
            if (pump_task is None or pump_task.done()) and now - sent["push_at"] >= every:
                if pump_task is not None and pump_task.done() and not pump_task.cancelled() and pump_task.exception():
                    break                                   # the send inside the push failed: the socket is gone
                sent["push_at"] = now
                pump_task = asyncio.create_task(pump())
            await asyncio.sleep(sub.get("rate") or WS_TICK_SEC)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.debug("Browser websocket closed: %s", exc)
    finally:
        for t in (read_task, pump_task):
            if t is not None and not t.done():
                t.cancel()


@app.get("/api/index-ltp")
async def index_ltp(symbol: str = "NIFTY"):
    """Latest index price: fresh WebSocket tick if available, otherwise a matched REST quote."""
    sym = symbol.upper()
    if sym not in INDEX_KEYS:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    key = INDEX_KEYS[sym]
    now = time.time()
    with STREAM_LOCK:
        view = _tick_view(key, now)
    if view and view["fresh"] and view.get("ltp") is not None:
        return {"status": "LIVE_TICK", "symbol": sym, "ltp": view["ltp"], "close_price": view.get("close_price"),
                "net_change": (view["ltp"] - view["close_price"]) if view.get("close_price") else None,
                "age_seconds": view["age_seconds"], "source": "Upstox V3 WebSocket"}
    hit = LTP_CACHE.get(key)
    if hit and now - hit[0] < LTP_CACHE_TTL:
        return hit[1]
    data = await upstox_get(f"{API_BASE}/v2/market-quote/quotes", {"instrument_key": key})
    qd = (data or {}).get("data") or {}
    # Upstox may key rows by an exchange symbol rather than the requested instrument_key.
    # Never silently use the first row from a multi-instrument response: that can pair NIFTY's
    # price with another instrument's previous close and display a false daily change.
    q = qd.get(key) if isinstance(qd, dict) else None
    if not isinstance(q, dict) and isinstance(qd, dict):
        q = next((v for v in qd.values() if isinstance(v, dict) and
                  str(v.get("instrument_token", "")) == key), None)
    if not isinstance(q, dict) and isinstance(qd, dict) and len(qd) == 1:
        only = next(iter(qd.values()))
        q = only if isinstance(only, dict) else None
    q = q or {}
    ltp = q.get("last_price", q.get("ltp"))
    ohlc = q.get("ohlc") or {}
    close_price = ohlc.get("close") or q.get("close_price") or q.get("prev_close")
    net_change = q.get("net_change")
    try:
        if net_change is None and ltp is not None and close_price is not None:
            net_change = float(ltp) - float(close_price)
    except (TypeError, ValueError):
        net_change = None
    out = {"status": "REST_QUOTE" if ltp is not None else "UNAVAILABLE", "symbol": sym, "ltp": ltp,
           "close_price": close_price, "net_change": net_change,
           "net_change_percent": q.get("net_change_percent", q.get("change_percent")),
           "age_seconds": 0, "source": "Upstox REST quote", "market_open": market_status()["open"]}
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
RECONFIRM = sf.ReconfirmTracker()      # v1.14: holds a fresh setup in PENDING until it has survived the reconfirmation window
LAST_ANALYSIS_AT = {}                  # tracker name -> time.time() of the last analysis (the background alert poller skips fresh ones)
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


SIGNAL_LOG_DB = Path(os.getenv("SIGNAL_LOG_DB", str(BASE_DIR / "signal_log.sqlite3")))
SIGNAL_LOG_LOCK = threading.Lock()
_SIGNAL_LOG_DIRTY = threading.Event()      # set whenever the log changes; the watcher mirrors it to the accounts database
SIGNAL_LOG_FIELDS = ["generated_at", "session_date", "symbol", "interval", "mode", "candle_time", "decision", "final_action", "side", "score", "regime", "pattern", "pattern_confirmed", "last_close", "entry", "stop_loss", "target", "target_name", "trade_status", "exit_price", "exit_time", "exit_reason", "gross_pnl_points", "net_pnl_points", "peak_points", "worst_points", "levels_backfilled", "atr_at_entry", "sl_atr_mult", "target_atr_mult", "target_basis", "rr", "option_name", "option_key", "option_strike", "option_expiry", "option_entry", "option_sl", "option_target", "option_delta", "option_exit", "option_exit_source", "option_pnl_points", "lot_size", "qty", "charges_rs", "net_pnl_rs", "exit_basis", "reason"]

def init_signal_log():
    SIGNAL_LOG_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SIGNAL_LOG_DB) as db:
        db.execute("CREATE TABLE IF NOT EXISTS signal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, generated_at TEXT NOT NULL, session_date TEXT NOT NULL, symbol TEXT NOT NULL, interval INTEGER NOT NULL, mode TEXT NOT NULL, candle_time TEXT NOT NULL, decision TEXT, final_action TEXT, side TEXT, score REAL, regime TEXT, pattern TEXT, pattern_confirmed INTEGER, last_close REAL, entry REAL, stop_loss REAL, target REAL, reason TEXT, UNIQUE(symbol, interval, mode, candle_time, final_action))")
        sx.ensure_columns(db)
        db.commit()

def record_signal_row(symbol, interval, mode, a):
    """Log a BUY (v1.19: only a CONFIRMED setup that also passed the profit check) with the index and option plan."""
    try:
        now_ist = datetime.now(IST)
        final = a.get("final") or {}
        side = str(final.get("side") or a.get("side") or "").upper()
        plan = final.get("buy") or {}
        # Keep only genuine BUY signals; WAIT/WATCH/CONFLICT/STALE and confirmed setups that failed the profit check are never logged.
        if str(final.get("level") or "").upper() != "CONFIRMED" or side not in ("CE", "PE") or not plan.get("ok") or not plan.get("index"):
            return
        candle_time = str(a.get("last_closed_bar") or now_ist.strftime("%Y-%m-%d %H:%M"))
        ix, opt = plan["index"], plan.get("option") or {}
        atr_v = _float_or_none(ix.get("atr") or a.get("atr"))
        pat = a.get("candle_confirmation") or {}
        cols = {"generated_at": now_ist.isoformat(timespec="seconds"), "session_date": now_ist.date().isoformat(), "symbol": symbol,
                "interval": int(interval), "mode": mode, "candle_time": candle_time, "decision": str(a.get("decision_technical") or a.get("decision") or "WAIT"),
                "final_action": f"BUY {side}", "side": side, "score": float(a.get("score") or 0),
                "regime": str(a.get("regime_label") or a.get("regime") or ""), "pattern": str(pat.get("pattern") or ""), "pattern_confirmed": int(bool(pat.get("confirmed"))),
                "last_close": _float_or_none(a.get("last_close")), "entry": ix.get("entry"), "stop_loss": ix.get("stop"), "target": ix.get("target"),
                "reason": str(plan.get("summary") or final.get("headline") or "")[:500], "target_name": f"{ix.get('target_atr')}xATR",
                "atr_at_entry": atr_v, "sl_atr_mult": ix.get("sl_atr"), "target_atr_mult": ix.get("target_atr"),
                "target_basis": ix.get("target_basis"), "rr": ix.get("rr"),
                "option_key": opt.get("instrument_key"), "option_name": opt.get("name"), "option_strike": opt.get("strike"), "option_expiry": opt.get("expiry"),
                "option_entry": opt.get("entry"), "option_sl": opt.get("stop"), "option_target": opt.get("target"), "option_delta": opt.get("delta"),
                "lot_size": opt.get("lot_size"), "qty": opt.get("qty"), "charges_rs": opt.get("charges_rs")}
        with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
            sx.ensure_columns(db)
            names = list(cols)
            cur = db.execute(f"INSERT OR IGNORE INTO signal_log ({','.join(names)}) VALUES ({','.join('?' * len(names))})", [cols[k] for k in names])
            _SIGNAL_LOG_DIRTY.set()
            db.commit()
        _MON["last_record"] = {"at": time.time(), "symbol": symbol, "interval": int(interval), "mode": mode, "side": side, "candle": candle_time,
                               "option": opt.get("name"), "new_row": bool(cur.rowcount)}
        _MON["last_record_error"] = None
        return bool(cur.rowcount)
    except Exception as exc:
        logger.warning("Signal log write failed: %s", str(exc)[:160])
        try: _MON["last_record_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        except Exception: pass

MAX_DAILY_LOSSES = int(os.getenv("MAX_DAILY_LOSSES", "2") or 0)      # 0 turns the daily loss limit off


def stopouts_today(symbol):
    """How many logged paper trades of `symbol` were closed at a loss today (STOP exits, or a time exit below zero)."""
    try:
        today = datetime.now(IST).date().isoformat()
        with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
            row = db.execute("SELECT COUNT(*) FROM signal_log WHERE session_date=? AND symbol=? AND trade_status IN ('STOP','SQUAREOFF') "
                             "AND COALESCE(net_pnl_points,0) < 0", (today, symbol)).fetchone()
        return int(row[0] or 0)
    except Exception as exc:
        logger.debug("stopouts_today failed: %s", str(exc)[:120])
        return None


def _float_or_none(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

init_signal_log()
sb.init(SIGNAL_LOG_DB)          # v1.19.5 signal board (every option signal, same file)


def _live_index_price(sym):
    """Fresh WebSocket index tick, or None."""
    key = INDEX_KEYS.get(sym)
    if not key:
        return None
    with STREAM_LOCK:
        view = _tick_view(key, time.time())
    return view["ltp"] if view and view.get("fresh") and view.get("ltp") is not None else None


def settle_signal_exits(sym, price, atr=None):
    """Auto-exit every OPEN logged trade of `sym` that hit stop / safe target / square-off at `price` (live price path)."""
    try:
        cfg = sx.config()
        backfill = None
        if atr:
            backfill = sx.backfill_points(
                lambda x: dd.trade_levels({"side": "CE"}, {}, atr=x, price=100.0), float(atr), cfg["target"])
        closed = sx.settle_open_signals(SIGNAL_LOG_DB, sym, price, datetime.now(IST), atr_backfill=backfill, lock=SIGNAL_LOG_LOCK)
        if closed:
            _SIGNAL_LOG_DIRTY.set()
        for c in closed:
            logger.info("Signal log auto-exit %s #%s: %s net %s pts", sym, c["id"], c["status"],
                        None if c.get("net") is None else round(c["net"], 2))
        return closed
    except Exception as exc:
        logger.warning("Signal auto-exit failed: %s", str(exc)[:160])
        return []


# ---- v1.19: exits from the 1-minute price PATH, not from whatever price the server happened to see after a pause ----------------
# The one logged trade of 4 Nov 2026 was a stop exit at -74 index points with a 0.75 ATR (~15-20 pt) stop: the live-price watcher only
# sees prices while the server is awake and polling, and the first price after a pause was used as the fill. Now every open trade is
# walked through the 1-minute index candles since its entry: the first bar that touched the stop / target decides, at that level.
_PATH_SETTLED_AT = {}      # symbol -> time of the last 1-minute-path settlement
_TICK_SEEN_AT = {}         # symbol -> time the exit watcher last looked at a live price
PATH_SETTLE_SECONDS = 20


def _open_trade_dates(sym):
    with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
        return [r[0] for r in db.execute("SELECT session_date FROM signal_log WHERE symbol=? AND COALESCE(trade_status,'OPEN')='OPEN'", (sym,)).fetchall()]


def _tick_settle_ok(sym):
    """A live price may close a trade only when the watcher has been looking continuously (no pause the path check has not covered)."""
    now = time.time()
    return now - _TICK_SEEN_AT.get(sym, 0) <= PATH_SETTLE_SECONDS and now - _PATH_SETTLED_AT.get(sym, 0) <= PATH_SETTLE_SECONDS * 3


async def settle_from_path(sym):
    """Settle open trades of `sym` from the 1-minute index candles since entry. Returns the trades closed now."""
    dates = await asyncio.to_thread(_open_trade_dates, sym)
    if not dates:
        _PATH_SETTLED_AT[sym] = time.time()
        return []
    today = ist_now().date()
    try:
        oldest = min(date.fromisoformat(str(d)[:10]) for d in dates)
    except ValueError:
        oldest = today
    days = max(1, min(10, (today - oldest).days + 1))
    raw = await fetch_candles(INDEX_KEYS[sym], 1, days)
    closed = await asyncio.to_thread(sx.settle_with_candles, SIGNAL_LOG_DB, sym, normalize_candles(raw), datetime.now(IST), SIGNAL_LOG_LOCK)
    _PATH_SETTLED_AT[sym] = time.time()
    if closed:
        _SIGNAL_LOG_DIRTY.set()
        for c in closed:
            logger.info("Signal log path exit %s #%s: %s at %s net %s pts", sym, c["id"], c["status"], c.get("exit_time"), round(c["net"], 2))
        await _after_exits(sym, closed, live=False)
    return closed


def _signal_row(rid):
    with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
        db.row_factory = sqlite3.Row
        r = db.execute("SELECT * FROM signal_log WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


async def _option_exit_price(row, live):
    """Premium at the exit: live quote when the exit is happening now, else the option's own 1-minute bar at the exit time
    (delta estimate clamped into that bar's range), else a delta estimate. Returns (price, source)."""
    key, oe, dl = row.get("option_key"), _float_or_none(row.get("option_entry")), _float_or_none(row.get("option_delta"))
    est = sx.estimate_option_exit(oe, dl, row.get("gross_pnl_points"))
    if not key:
        return None, None
    if live:
        try:
            q = await option_quote(key)
            if _float_or_none(q.get("ltp")):
                return float(q["ltp"]), "live quote"
        except Exception as exc:
            logger.debug("Option exit quote unavailable: %s", str(exc)[:120])
    try:
        ex = datetime.fromisoformat(str(row.get("exit_time")))
        days = max(1, min(10, (ist_now().date() - ex.astimezone(IST).date()).days + 1))
        bars = normalize_candles(await fetch_candles(key, 1, days))
        bar = next((b for b in bars if b["_dt"] <= ex < b["_dt"] + timedelta(minutes=1)), None)
        if bar:
            if est is None:
                return round(bar["close"], 2), "option 1-min bar close"
            return round(min(max(est, bar["low"]), bar["high"]), 2), "option 1-min bar (delta est. within bar range)"
    except Exception as exc:
        logger.debug("Option exit candles unavailable: %s", str(exc)[:120])
    return (est, "delta estimate") if est is not None else (None, None)


async def _after_exits(sym, closed, live):
    """For every trade just closed: record the option exit premium and money result, then push an EXIT notification."""
    for c in closed:
        if c.get("status") not in ("TARGET", "STOP", "SQUAREOFF"):
            continue
        try:
            row = await asyncio.to_thread(_signal_row, c["id"])
            if not row:
                continue
            price, src = await _option_exit_price(row, live)
            rs = None
            if price is not None:
                rs = await asyncio.to_thread(sx.set_option_exit, SIGNAL_LOG_DB, c["id"], price, src, SIGNAL_LOG_LOCK)
                _SIGNAL_LOG_DIRTY.set()
            # v1.19.2: no phone alerts while the market is closed. An exit worked out later (server was asleep) goes to the
            # notification history and the Signals page only.
            _spawn(_send_exit_alert(sym, row, c, price, rs, push=in_session(grace_minutes=15)))
        except Exception as exc:
            logger.warning("Exit bookkeeping failed: %s", str(exc)[:160])


async def _send_exit_alert(sym, row, c, opt_exit, rs, push=True):
    try:
        name = row.get("option_name") or f"{sym} {row.get('side')}"
        word = {"TARGET": "TARGET hit", "STOP": "STOP-LOSS hit", "SQUAREOFF": "time exit"}.get(c["status"], c["status"])
        title = f"{name} - {word}: {c['net']:+.1f} pts" + (f" / ₹{rs:+,.0f}" if rs is not None else "")
        when = str(c.get("exit_time") or "")[11:19] or datetime.now(IST).strftime("%H:%M:%S")
        oe = _float_or_none(row.get("option_entry"))
        body = (f"Index {float(row.get('entry') or 0):,.2f} → {float(c['exit_price']):,.2f} at {when} IST ({c['gross']:+.1f} gross, {c['net']:+.1f} net pts).\n"
                + (f"Premium {oe:,.2f} → {opt_exit:,.2f}" + (f" × {row.get('qty')} − charges ₹{float(row.get('charges_rs') or 0):,.0f} = ₹{rs:+,.0f}" if rs is not None else "") + ".\n" if oe and opt_exit else "")
                + "Paper result recorded in the Daily Signal Log.")
        if not push:
            body += "\n(Settled after the market closed - recorded without a phone alert.)"
        for uid in await asyncio.to_thread(acc.push_recipient_ids, _push_owner_id()):
            await asyncio.to_thread(acc.push_add_history, uid, "EXIT", sym, row.get("side") or "", title, body, utc_now_iso())
        if not push:
            return
        await notifier.send_alert(title, body, "default")
        await _dispatch_confirmed_push(title, body, sym, row.get("side") or "", "EXIT")
    except Exception as exc:
        logger.warning("Exit alert failed: %s", str(exc)[:160])


# ---- durable copy of the signal log (v1.18.11) -------------------------------------------------------------
# The SQLite file is erased whenever a Render free instance restarts, which would wipe the day/month history. So every change
# is mirrored, one JSON blob per month, into the accounts database (PostgreSQL when DATABASE_URL is set) and restored at start.
_SL_MIRROR_HASH = {}
SIGNAL_LOG_COLS = None


def _signal_rows_all():
    with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM signal_log ORDER BY id").fetchall()]


def mirror_signal_log():
    import hashlib
    for prefix, rows_all in (("signal_log:", _signal_rows_all()), ("signal_board:", _board_rows_all())):
        months = {}
        for r in rows_all:
            r.pop("id", None)
            months.setdefault(str(r.get("session_date") or "")[:7], []).append(r)
        for month, rows in months.items():
            blob = json.dumps(rows, separators=(",", ":"), default=str)
            h = hashlib.sha1(blob.encode()).hexdigest()
            if _SL_MIRROR_HASH.get(prefix + month) != h:
                acc.meta_set(prefix + month, blob)
                _SL_MIRROR_HASH[prefix + month] = h


def _board_rows_all():
    with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM option_signals ORDER BY id").fetchall()]


def restore_signal_log():
    """Re-insert rows missing from the local file (after a restart wiped it). Existing rows are never overwritten."""
    restored = 0
    import hashlib
    for prefix, table in (("signal_log:", "signal_log"), ("signal_board:", "option_signals")):
        for item in acc.meta_list(prefix):
            try:
                rows = json.loads(item["value"])
            except ValueError:
                continue
            with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
                have = {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
                for r in rows:
                    cols = [c for c in r if c in have]
                    cur = db.execute(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols])
                    restored += cur.rowcount or 0
                db.commit()
            _SL_MIRROR_HASH[prefix + item["key"].split(":", 1)[1]] = hashlib.sha1(item["value"].encode()).hexdigest()
    return restored


@app.on_event("startup")
async def _restore_signal_log_and_config():
    try:
        cfg = await asyncio.to_thread(acc.meta_get, "signal_log_config")
        if cfg:
            sx.set_overrides(json.loads(cfg))
            _set_option_overrides(json.loads(cfg))
        n = await asyncio.to_thread(restore_signal_log)
        _SIGNAL_LOG_DIRTY.set()          # first mirror of anything already in the local file
        if n:
            logger.info("Signal log: restored %s row(s) from the accounts database", n)
    except Exception as exc:
        logger.warning("Signal log restore failed (check DATABASE_URL): %s", str(exc)[:160])


_EOD_SENT = None


async def _end_of_day_summary():
    """Once per trading day, shortly after the close, push the day's strategy result to the phone."""
    global _EOD_SENT
    now = datetime.now(IST)
    info = _session_for(now)
    if not info["open_day"] or _EOD_SENT == now.date():
        return
    close_dt = datetime.combine(now.date(), info["end"], tzinfo=IST)
    if not (close_dt + timedelta(minutes=5) <= now <= close_dt + timedelta(minutes=60)):
        return                                      # v1.19.2: only 5-60 min after the close, never late at night
    _EOD_SENT = now.date()
    day = now.date().isoformat()
    data = await get_signal_log(day, day, "", 10000)
    s = sx.summarize(data["rows"], "day")["total"]
    if not s["signals"]:
        return
    title = f"Option Intelligence - day summary {day}"
    body = (f"{s['signals']} buy signals ({s['ce']} CE / {s['pe']} PE): {s['target_exits']} target, {s['stop_exits']} stop, {s['time_exits']} time exit, {s['open']} still open.\n"
            f"Net {s['net_points']:+.1f} index pts after costs (gross {s['gross_points']:+.1f})"
            + (f", net ₹{s['net_rs']:+,.0f} on the options after charges" if s.get("net_rs") is not None else "")
            + f", win rate {s['win_rate'] if s['win_rate'] is not None else '-'}%.\nOpen the Signals page for the day and month breakdown.")
    await notifier.send_alert(title, body, "default", force=True)
    for uid in await asyncio.to_thread(acc.push_recipient_ids, _push_owner_id()):
        await asyncio.to_thread(acc.push_add_history, uid, "SUMMARY", "ALL", "-", title, body, utc_now_iso())
    await _dispatch_confirmed_push(title, body, "SUMMARY", "WAIT", "SUMMARY")


_EXIT_WATCHER_TASK = None


_BOARD_AT = [0.0]


async def board_outcomes(limit=8):
    """Fill in what each board signal's option (and the index) did in the 60 minutes after it. Runs every 2 minutes."""
    rows = await asyncio.to_thread(sb.pending, SIGNAL_LOG_DB, datetime.now(IST), limit, SIGNAL_LOG_LOCK)
    if not rows:
        return 0
    idx_cache, done = {}, 0
    today = ist_now().date()
    for r in rows:
        try:
            d = date.fromisoformat(str(r["session_date"])[:10])
        except ValueError:
            d = today
        days = max(1, min(10, (today - d).days + 1))
        try:
            opt = normalize_candles(await fetch_candles(r["option_key"], 1, days)) if r.get("option_key") else []
            key = (r["symbol"], days)
            if key not in idx_cache and r["symbol"] in INDEX_KEYS:
                idx_cache[key] = normalize_candles(await fetch_candles(INDEX_KEYS[r["symbol"]], 1, days))
            fields = sb.outcome(r, opt, idx_cache.get(key, []), IST, sx._first_exit)
        except HTTPException as exc:
            if exc.status_code == 401:
                raise
            if (today - d).days < 5:
                continue                     # try again later
            fields = {"outcome_at": datetime.now(IST).isoformat(timespec="seconds"), "outcome_note": f"candles unavailable: {str(exc.detail)[:80]}"}
        await asyncio.to_thread(sb.save_outcome, SIGNAL_LOG_DB, r["id"], fields, SIGNAL_LOG_LOCK)
        done += 1
    if done:
        _SIGNAL_LOG_DIRTY.set()
    return done


async def _signal_exit_watcher():
    """Background loop: checks open logged trades against the live index price every 3 s while the market is open
    (every 60 s otherwise, so trades from today are squared off at the close and old ones are marked expired).
    Does nothing, and calls no API, when there are no open trades."""
    while True:
        delay = 3.0
        try:
            if ist_now().date().isoformat() not in _TRADING_DAY:
                _spawn(refresh_trading_day())
            open_by_symbol = await asyncio.to_thread(sx.open_trade_count, SIGNAL_LOG_DB, SIGNAL_LOG_LOCK)
            is_open = market_status()["open"]
            delay = 3.0 if is_open else 60.0
            if _SIGNAL_LOG_DIRTY.is_set():
                _SIGNAL_LOG_DIRTY.clear()
                try: await asyncio.to_thread(mirror_signal_log)
                except Exception as exc: logger.warning("Signal log mirror failed: %s", str(exc)[:140]); _SIGNAL_LOG_DIRTY.set()
            try: await _end_of_day_summary()
            except Exception as exc: logger.debug("EOD summary: %s", str(exc)[:140])
            if runtime_token and time.time() - _BOARD_AT[0] > 120:
                _BOARD_AT[0] = time.time()
                try:
                    await board_outcomes()
                except Exception as exc:
                    logger.info("Signal board outcomes: %s", str(getattr(exc, "detail", exc))[:140])
            if open_by_symbol and runtime_token:
                for sym in list(open_by_symbol):
                    if sym not in INDEX_KEYS:
                        continue
                    # 1) the 1-minute path since entry: always after a pause, otherwise every PATH_SETTLE_SECONDS (v1.19)
                    paused = time.time() - _TICK_SEEN_AT.get(sym, 0) > PATH_SETTLE_SECONDS
                    path_ok = True
                    if paused or time.time() - _PATH_SETTLED_AT.get(sym, 0) > PATH_SETTLE_SECONDS:
                        try:
                            await settle_from_path(sym)
                        except Exception as exc:
                            path_ok = False
                            logger.info("Path settlement unavailable for %s: %s", sym, str(getattr(exc, "detail", exc))[:140])
                    # 2) the live price, for the seconds since the last closed 1-minute bar
                    price = _live_index_price(sym)
                    if price is None:
                        try:
                            q = await index_ltp(sym)
                            price = _float_or_none(q.get("ltp"))
                        except Exception as exc:
                            logger.debug("Exit watcher quote unavailable: %s", str(exc)[:120])
                    if price is not None and (path_ok or not paused or not is_open):
                        closed = await asyncio.to_thread(settle_signal_exits, sym, price)
                        if closed:
                            await _after_exits(sym, closed, live=True)
                    _TICK_SEEN_AT[sym] = time.time()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Exit watcher error: %s", str(exc)[:160])
        await asyncio.sleep(delay)


@app.on_event("startup")
async def _init_push_storage():
    # Creating the schema is part of the first accounts-database connection; do it before background tasks run.
    try: await asyncio.to_thread(acc.push_devices, "web", 1)
    except Exception as exc: logger.warning("Push storage init failed (check DATABASE_URL): %s", str(exc)[:160])


@app.on_event("startup")
async def _start_signal_exit_watcher():
    global _EXIT_WATCHER_TASK
    if _EXIT_WATCHER_TASK is None or _EXIT_WATCHER_TASK.done():
        _EXIT_WATCHER_TASK = asyncio.create_task(_signal_exit_watcher())


_BG_TASKS = set()


def _spawn(coro):
    """create_task keeps only a weak reference; hold a strong one so an alert is never garbage-collected mid-send."""
    t = asyncio.create_task(coro); _BG_TASKS.add(t); t.add_done_callback(_BG_TASKS.discard); return t


_LAST_FINAL = {}      # (symbol, interval, mode) -> (level, side) seen on the previous analysis
_CHANGE_SENT = {}     # (symbol, interval, mode, level, side) -> time of the last CHANGED push (flap guard)

def _signal_change_event(key, final, market_open, now=None):
    """'CHANGED' when the final signal moves to a different (level, side) that has no event of its own. PENDING/CONFIRMED/FAILED already
    send STARTED/CONFIRMED/FAILED, WAIT is not worth a push, and the first reading after a restart is never a change."""
    now = now or time.time()
    cur = (final.get("level"), final.get("side"))
    prev = _LAST_FINAL.get(key); _LAST_FINAL[key] = cur
    if not market_open or prev is None or prev == cur or cur[0] in ("WAIT", "PENDING", "CONFIRMED") or prev[0] == "PENDING":
        return None
    k = key + cur
    if now - _CHANGE_SENT.get(k, 0) < 120:
        return None
    _CHANGE_SENT[k] = now
    return "CHANGED"


async def _send_signal_alert(event, sym, interval, mode, a, fused):
    """Fire-and-forget: Telegram / ntfy / email message + phone push for a signal event. Never raises."""
    try:
        f = fused.get("final") or {}
        side = f.get("side") or a.get("side") or ""
        now_t = datetime.now(IST).strftime("%H:%M:%S IST")
        pick = a.get("option_pick") or {}
        res = []
        if event == "CONFIRMED" and f.get("buy"):                 # v1.19: the BUY, with option and index levels
            plan = f["buy"]; o, ix = plan["option"], plan["index"]
            title = f"BUY {o['name']} @ {o['entry']:,.2f}"
            body = (f"Option: buy {o['entry']:,.2f} · stop {o['stop']:,.2f} · target {o['target']:,.2f}"
                    + (f" · qty {o['qty']} → ≈ ₹{o['exp_profit_rs']:+,.0f} at target / −₹{o['exp_loss_rs']:,.0f} at stop (after charges)" if o.get("qty") else "") + "\n"
                    f"{sym}: entry {ix['entry']:,.2f} · SL {ix['stop']:,.2f} · target {ix['target']:,.2f} ({ix['target_basis']}) · R:R {ix['rr']:.1f}\n"
                    f"{interval}m {mode} · score {a.get('score')}/100 · {now_t}. Paper signal - not an order.")
            res = await notifier.send_alert(title, body, "high")
        elif event == "REJECTED":
            title = f"{sym} {side} confirmed - NO BUY (profit check)"
            body = f"{(a.get('buy_plan') or {}).get('summary') or f.get('headline') or ''}\n{sym} {interval}m {mode} · index {a.get('last_close'):,.2f} · {now_t}"
        elif event == "CHANGED":          # any other change of the final signal: phone push + history only (external channels follow ALERT_EVENTS)
            title = f"{sym} signal changed: {f.get('action') or '-'}"
            extra = ""
            if f.get("level") == "WATCH" and pick.get("instrument_key"):
                title = f"{sym} WATCH {side} - {pick.get('name') or ''}"
                extra = (f"\nChart switched to {pick.get('name')} ({pick.get('label')}) @ {float(pick.get('ltp') or 0):,.2f}. "
                         "Waiting for confirmation + profit check before any BUY.")
            body = (f"{f.get('headline') or ''}{extra}\n{sym} {interval}m | {mode} | index {a.get('last_close'):,.2f} | score {a.get('score')}/100 | "
                    f"{now_t}\nAnalysis only - not an order.")
        else:
            entry, stop, target, _ = sx.pick_levels(a.get("atr_levels"), a.get("last_close"))
            title, body, prio = notifier.format_event(event, sym, interval, mode, side, a, fused.get("reconfirm") or {},
                                                      {"entry": entry, "stop_loss": stop, "target": target}, now_t)
            if event == "STARTED" and pick.get("name"):
                body += f"\nOption lined up: {pick['name']} ({pick.get('label')}) @ {float(pick.get('ltp') or 0):,.2f}."
            res = await notifier.send_alert(title, body, prio)
        try:
            for uid in await asyncio.to_thread(acc.push_recipient_ids, _push_owner_id()):
                if _event_allowed(await asyncio.to_thread(_user_pref, uid), event):
                    await asyncio.to_thread(acc.push_add_history, uid, event, sym, side, title, body, utc_now_iso())
        except Exception as exc:
            logger.warning("Notification history not saved: %s", str(exc)[:140])
        push_res = await _dispatch_confirmed_push(title, body, sym, side, event)
        _MON["last_alert"] = {"event": event, "symbol": sym, "side": side, "at": time.time(), "title": title, "channels": res, "push": push_res}
        logger.info("Signal alert %s %s %s -> channels=%s push=%s", event, sym, side, res, push_res)
    except Exception as exc:
        logger.warning("Signal alert failed: %s", str(exc)[:160])


async def _run_push_db(fn, *args):
    try: return await asyncio.to_thread(fn, *args)
    except Exception as exc:
        logger.warning("Push storage error: %s", str(exc)[:160])
        raise HTTPException(503, "Account database is unavailable. Check DATABASE_URL.")

class WebPushSubscription(BaseModel):
    subscription: dict

class NativePushToken(BaseModel):
    token: str

def PUSH_DELIVERY_ENABLED():
    """True when at least one server-side notification transport is configured."""
    native = bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip())
    return True if _web_push_available() else (notifier.any_configured() or native)   # Web Push keys are auto-generated (v1.18.11)


def _web_push_available():
    try:
        import pywebpush  # noqa: F401
        return True
    except Exception:
        return False


@app.get("/api/push/config")
async def push_config():
    pub = ""
    if _web_push_available():
        try: pub = (await asyncio.to_thread(acc.vapid_keys))[0]
        except Exception as exc: logger.warning("VAPID keys unavailable: %s", str(exc)[:140])
    return {"web_push": bool(pub),
            "vapid_public_key": pub,
            "native_push": bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()),
            "background_poller": ALERT_BACKGROUND_ON(),
            "owner_user_id_configured": bool(os.getenv("PUSH_OWNER_USER_ID", "").strip()),
            "delivery_configured": PUSH_DELIVERY_ENABLED()}

@app.post("/api/push/web/subscribe")
async def push_web_subscribe(payload: WebPushSubscription, request: Request):
    uid=_push_user(request); sub=payload.subscription
    if not uid: raise HTTPException(401,"Login required")
    if not isinstance(sub,dict) or not sub.get("endpoint") or not isinstance(sub.get("keys"),dict): raise HTTPException(400,"Invalid Web Push subscription")
    await _run_push_db(acc.push_upsert_device, uid, "web", sub["endpoint"], json.dumps(sub), utc_now_iso())
    return {"ok":True}

@app.post("/api/push/native/register")
async def push_native_register(payload: NativePushToken, request: Request):
    uid=_push_user(request); token=payload.token.strip()
    if not uid: raise HTTPException(401,"Login required")
    if len(token)<30 or len(token)>4096: raise HTTPException(400,"Invalid push token")
    await _run_push_db(acc.push_upsert_device, uid, "native", token, token, utc_now_iso())
    return {"ok":True}

class PushPref(BaseModel):
    mode: str

@app.get("/api/push/prefs")
async def push_prefs_get(request: Request):
    uid = _push_user(request)
    if not uid: raise HTTPException(401, "Login required")
    return {"mode": await asyncio.to_thread(_user_pref, uid, True), "modes": {"all": "All signal changes", "confirmed": "Confirmed buy signals only"}}

@app.post("/api/push/prefs")
async def push_prefs_set(payload: PushPref, request: Request):
    uid = _push_user(request)
    if not uid: raise HTTPException(401, "Login required")
    if payload.mode not in PUSH_PREF_MODES: raise HTTPException(400, "mode must be one of: " + ", ".join(PUSH_PREF_MODES))
    await _run_push_db(acc.meta_set, f"push_pref:{uid}", payload.mode)
    _PUSH_PREF_CACHE[uid] = (time.time(), payload.mode)
    return {"ok": True, "mode": payload.mode}

@app.get("/api/notifications")
async def notification_history(request: Request, limit: int=100):
    uid=_push_user(request)
    if not uid: raise HTTPException(401,"Login required")
    rows = await _run_push_db(acc.push_history, uid, limit)
    return {"rows":rows,"count":len(rows)}

@app.get("/notifications")
async def notifications_page():
    return FileResponse(BASE_DIR / "static" / "notifications.html", headers={"Cache-Control":"no-cache"})

@app.get("/api/push/status")
async def push_status(request: Request):
    """Everything needed to see WHY a phone is (not) getting alerts: server awake? monitor running? token? device registered?"""
    uid = _push_user(request)
    if not uid:
        raise HTTPException(401, "Login required")
    syms, interval, mode, score_min, delay = _poller_cfg()
    now = time.time()
    try:
        mine = await asyncio.to_thread(acc.push_devices, "web", uid)
        mine_native = await asyncio.to_thread(acc.push_devices, "native", uid)
        owner_web = await asyncio.to_thread(acc.push_devices, "web", _push_owner_id())
        owner_native = await asyncio.to_thread(acc.push_devices, "native", _push_owner_id())
        all_ids = sorted({r["user_id"] for r in (await asyncio.to_thread(acc.push_devices, "web", 0)) + (await asyncio.to_thread(acc.push_devices, "native", 0))}) if _push_owner_id() else []
        db_ok = True
    except Exception as exc:
        mine = mine_native = owner_web = owner_native = []
        all_ids = []
        db_ok = False
    host = lambda r: (lambda e: "Google (Chrome/Android)" if "fcm" in e or "googleapis" in e else "Mozilla" if "mozilla" in e else "Apple" if "apple" in e else "Microsoft" if "windows" in e else "other")(r.get("endpoint", ""))
    st = acc.storage_info()
    mkt = market_status()["open"]
    problems = []
    if not db_ok: problems.append("The account database is unreachable - registrations and history cannot be saved. Check DATABASE_URL.")
    if not st.get("persistent"): problems.append(st.get("warning") or "Storage is not persistent: phones must re-register after every restart. Set DATABASE_URL.")
    if not _web_push_available(): problems.append("The pywebpush package is not installed on the server, so browser push cannot be sent. Redeploy so requirements.txt installs.")
    if not (mine or mine_native): problems.append("This phone/browser is not registered for push. Open the app on it and tap 'Enable push alerts', then allow notifications.")
    if _push_owner_id() and _push_owner_id() != uid: problems.append(f"PUSH_OWNER_USER_ID is {_push_owner_id()} but you are signed in as user {uid}: alerts go only to user {_push_owner_id()}'s devices. Change or clear that setting.")
    if _push_owner_id() and not (owner_web or owner_native):
        problems.append(f"No phone/browser is registered for the configured owner account (user {_push_owner_id()})." +
                        (f" Devices are registered under user id(s) {all_ids}: set PUSH_OWNER_USER_ID to one of them, or clear it to notify every account." if all_ids
                         else " Open the app on the phone while signed in as that account; it now registers itself."))
    if _MON.get("last_record_error"): problems.append("Signal log write failed: " + _MON["last_record_error"])
    if not ALERT_BACKGROUND_ON(): problems.append("The background monitor is switched off (ALERT_BACKGROUND=0 or no delivery channel).")
    if not runtime_token: problems.append("No Upstox token: signals cannot be computed. Connect Upstox.")
    elif TOKEN_CHECK.get("state") == "EXPIRED": problems.append("The Upstox token has expired - reconnect Upstox (More > Connect Upstox). Until then nothing is watched.")
    if mkt and _MON["last_tick"] and now - _MON["last_tick"] > 60: problems.append("The monitor loop has not run for over a minute.")
    if mkt and _MON["last_error"]: problems.append("Last monitor error: " + _MON["last_error"])
    if now - _MON["started_at"] < 900: problems.append(f"The server started only {round((now - _MON['started_at']) / 60)} min ago (it had been asleep or was redeployed). It sleeps whenever nobody opens the app - add a free pinger on /health every 5 minutes.")
    return {"time": utc_now_iso(), "market_open": mkt, "server_uptime_s": round(now - _MON["started_at"]), "monitor": {
                "running": bool(_MON["last_tick"]) and now - _MON["last_tick"] < 90, "last_check_ago_s": round(now - _MON["last_tick"]) if _MON["last_tick"] else None,
                "last_ok_ago_s": round(now - _MON["last_ok"]) if _MON["last_ok"] else None, "last_error": _MON["last_error"], "symbols": _MON["symbols"],
                "watching": {"symbols": syms, "interval": interval, "mode": mode, "score_min": score_min, "poll_s": delay}, "events": sorted(notifier.events())},
            "last_alert": _MON["last_alert"], "gap": _MON["gap"], "signal_log": {"last_record": _MON.get("last_record"), "error": _MON.get("last_record_error")},
            "devices": {"web": len(mine), "native": len(mine_native), "kinds": [host(r) for r in mine], "owner_configured": bool(_push_owner_id())},
            "token_state": TOKEN_CHECK.get("state"), "storage": st, "ttl_s": int(os.getenv("PUSH_TTL_SECONDS", "600") or 600), "problems": problems}


@app.get("/api/alerts/config")
async def alerts_config():
    return {"channels": {**notifier.channels(), "web_push": _web_push_available(),
                         "native_push": bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))},
            "events": sorted(notifier.events()), "reconfirm_default_seconds": sf.RECONFIRM_SECONDS,
            "max_adverse_atr": sf.RECONFIRM_MAX_ADVERSE_ATR, "background_poller": ALERT_BACKGROUND_ON(),
            "owner_user_id_configured": bool(os.getenv("PUSH_OWNER_USER_ID", "").strip()),
            "note": "The server must stay awake. The app pings itself every 10 min, but a Render Free service that has already gone to sleep can only be woken by an external pinger (UptimeRobot / cron-job.org -> /health every 5 min) or a paid instance. Device registrations/history are stored in the accounts database (set DATABASE_URL to PostgreSQL)."}


def _push_summary(results, push_results):
    parts = []
    for r in (results or []):
        parts.append(f"{r.get('channel')}: " + ("sent" if r.get("ok") else f"FAILED ({r.get('error')})"))
    for r in (push_results or []):
        if isinstance(r, dict) and r.get("configured") is not None:
            if "devices" in r:
                if r.get("devices", 0) == 0: parts.append("phone push: NO DEVICE REGISTERED - tap 'Enable push alerts'")
                elif r.get("sent"): parts.append(f"phone push: delivered to {r['sent']} of {r['devices']} device(s)")
                else: parts.append(f"phone push: FAILED for {r['devices']} device(s) ({'; '.join(r.get('errors') or ['rejected'])}); {r.get('dropped', 0)} stale registration(s) removed - reopen the app to re-register")
            elif r.get("configured") and "sent" in r:
                parts.append(f"Android app push: sent to {r['sent']}")
        elif isinstance(r, Exception):
            parts.append("push error: " + str(r)[:80])
    return "; ".join(parts) or "No channel configured"


@app.post("/api/alerts/test")
async def alerts_test(request: Request):
    user = _need_user(request)
    owner_id = _push_owner_id()
    if owner_id and int(user.get("id", 0)) != owner_id:
        raise HTTPException(403, "Only the configured push owner can send a test notification")
    title = "Option Intelligence - test alert"
    body = f"Test message sent {datetime.now(IST).strftime('%H:%M:%S IST')}. If you can read this on your phone, push notifications are working."
    results = await notifier.send_alert(title, body, force=True)
    push_results = await _dispatch_confirmed_push(title, body, "TEST", "WAIT", "TEST")
    configured = PUSH_DELIVERY_ENABLED()
    return {"configured": {**notifier.channels(), "web_push": _web_push_available(),
                            "native_push": bool(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))},
            "results": results, "push_results": push_results,
            "message": "No server notification channel is configured. Configure Telegram/ntfy/email, VAPID Web Push, or Firebase FCM." if not configured else "Test dispatched; check the channel results and your device.",
            "summary": _push_summary(results, push_results)}


def ALERT_BACKGROUND_ON():
    return PUSH_DELIVERY_ENABLED() and os.getenv("ALERT_BACKGROUND", "1").strip() != "0"


_ALERT_POLLER_TASK = None


_USER_COMBOS = {}      # (symbol, interval, mode, score_min) -> last time a browser asked for it (v1.18.12)
_MON = {"started_at": time.time(), "last_tick": 0.0, "last_ok": 0.0, "last_error": None, "symbols": {}, "last_alert": None, "gap": None, "ticks": 0}


def _poller_cfg():
    syms = [x.strip().upper() for x in os.getenv("ALERT_SYMBOLS", "NIFTY,BANKNIFTY").split(",") if x.strip().upper() in INDEX_KEYS] or ["NIFTY", "BANKNIFTY"]
    try: interval = int(os.getenv("ALERT_INTERVAL", "5"))
    except ValueError: interval = 5
    mode = os.getenv("ALERT_MODE", "scalp") if os.getenv("ALERT_MODE", "scalp") in MODES else "scalp"
    try: score_min = int(os.getenv("ALERT_SCORE_MIN", "70"))
    except ValueError: score_min = 70
    try: delay = max(5, int(os.getenv("ALERT_POLL_SECONDS", "10") or 10))
    except ValueError: delay = 10
    return syms, interval, mode, score_min, delay


def _watch_list(syms, interval, mode, score_min):
    """What the background monitor analyses: the ALERT_* settings, plus (v1.18.12) up to 3 other settings a browser used in the last
    6 hours - otherwise a signal shown on a 15m / standard terminal was only logged while that page happened to be open."""
    base = [(s, interval, mode, score_min) for s in syms]
    if os.getenv("ALERT_FOLLOW_TERMINAL", "1").strip() == "0":
        return base
    cutoff = time.time() - 6 * 3600
    extra = [k for k, t in sorted(_USER_COMBOS.items(), key=lambda kv: -kv[1]) if t >= cutoff and k not in base][:3]
    return base + extra


def _mon_note(res, at):
    f = res.get("final") or {}
    rc = res.get("reconfirm") or {}
    return {"at": at, "price": res.get("last_close"), "score": res.get("score"), "side": f.get("side") or res.get("side"),
            "level": f.get("level"), "action": f.get("action") or res.get("decision"), "reconfirm": rc.get("state"), "reason": rc.get("reason")}


def _mon_refresh(syms, interval, mode, score_min):
    for sym in syms:
        best = None
        for k, (ts, res) in list(_AN_CACHE.items()):
            if len(k) == 5 and k[0] == sym and k[1] == interval and k[2] == mode and k[3] == score_min and isinstance(res, dict) and res.get("status") == "ok":
                if best is None or ts > best[0]:
                    best = (ts, res)
        if best:
            _MON["symbols"][sym] = _mon_note(best[1], best[0])


def _market_open_at(dt):
    return in_session(dt)


async def _alert_poller():
    """Keeps the reconfirmation clock running and sends alerts even when no browser tab is open. It only analyses a
    symbol when no page has done so in the last 10 s, so an open terminal is not doubled up. Needs the server awake."""
    syms, interval, mode, score_min, delay = _poller_cfg()
    last_beat = 0.0
    while True:
        try:
            _MON["last_tick"] = time.time(); _MON["ticks"] += 1
            if time.time() - last_beat > 60:             # heartbeat in the database: lets the next start tell how long the monitor was asleep
                last_beat = time.time()
                try: await asyncio.to_thread(acc.meta_set, "monitor_heartbeat", str(last_beat))
                except Exception as exc: logger.debug("Heartbeat not saved: %s", str(exc)[:100])
            if market_status()["open"] and runtime_token and ALERT_BACKGROUND_ON():
                for (sym, itv, md, smin) in _watch_list(syms, interval, mode, score_min):
                    if time.time() - LAST_ANALYSIS_AT.get(f"{sym}|{itv}|{md}|{smin}", 0) > 10:
                        try:
                            await shared_analysis((sym, itv, md, smin, -1), lambda s=sym, i=itv, m=md, sm=smin: _analysis_compute(symbol=s, interval=i, mode=m, score_min=sm, reconfirm=-1), user_request=False)
                        except HTTPException as exc:
                            if exc.status_code == 401:
                                raise
                            _MON["last_error"] = f"{sym} {itv}m {md}: HTTP {exc.status_code}: {str(exc.detail)[:100]}"
                        except Exception as exc:
                            _MON["last_error"] = f"{sym} {itv}m {md}: {type(exc).__name__}: {str(exc)[:100]}"
                _mon_refresh(syms, interval, mode, score_min)
                _MON["last_ok"] = time.time(); _MON["last_error"] = None
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            _MON["last_error"] = f"HTTP {exc.status_code}: {str(exc.detail)[:120]}"
            if exc.status_code == 401:
                await _token_expired_alert()
            logger.debug("Alert poller: %s", str(exc.detail)[:160])
        except Exception as exc:
            _MON["last_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            logger.debug("Alert poller: %s", str(exc)[:160])
        await asyncio.sleep(delay)


async def _report_monitor_gap():
    """At start-up: if the server was down during market hours (Render Free sleeps when nobody opens the app), say so - those
    signals were never watched, which is exactly why a phone gets no alert."""
    try:
        prev = await asyncio.to_thread(acc.meta_get, "monitor_heartbeat")
        if not prev:
            return
        prev_dt, now = datetime.fromtimestamp(float(prev), IST), datetime.now(IST)
        gap = (now - prev_dt).total_seconds()
        if gap < 420 or not (_market_open_at(prev_dt) or _market_open_at(now)):
            return
        _MON["gap"] = {"from": prev_dt.strftime("%d %b %H:%M"), "to": now.strftime("%H:%M"), "minutes": round(gap / 60)}
        await refresh_trading_day()
        if not in_session(now):             # v1.19.2: shown on the monitor line, but no phone alert while the market is closed
            return
        title = "Option Intelligence - monitor was offline"
        body = (f"The server was asleep or restarted from {prev_dt.strftime('%H:%M')} to {now.strftime('%H:%M')} IST ({round(gap / 60)} min). "
                "Signals in that window were not watched. Add a free pinger on /health every 5 min so it stays awake (see README).")
        await notifier.send_alert(title, body, "default", force=True)
        for uid in await asyncio.to_thread(acc.push_recipient_ids, _push_owner_id()):
            await asyncio.to_thread(acc.push_add_history, uid, "SYSTEM", "ALL", "-", title, body, utc_now_iso())
        await _dispatch_confirmed_push(title, body, "SYSTEM", "WAIT", "GAP")
    except Exception as exc:
        logger.debug("Monitor gap report: %s", str(exc)[:140])


@app.on_event("startup")
async def _load_trading_calendar():
    asyncio.create_task(refresh_trading_day())


@app.on_event("startup")
async def _start_gap_report():
    asyncio.create_task(_report_monitor_gap())


_TOKEN_ALERT_DAY = None


async def _token_expired_alert():
    """Upstox tokens expire daily. Without a fresh one no signals can be computed, so tell the owner once per day."""
    global _TOKEN_ALERT_DAY
    now = datetime.now(IST)
    today = now.date()
    if _TOKEN_ALERT_DAY == today:
        return
    info = _session_for(now)
    if not info["open_day"] or not (datetime.combine(today, info["start"], tzinfo=IST) - timedelta(minutes=45) <= now
                                    <= datetime.combine(today, info["end"], tzinfo=IST)):
        return                              # v1.19.2: no "reconnect" alert at night / on holidays
    _TOKEN_ALERT_DAY = today
    title, body = "Option Intelligence - reconnect Upstox", "The Upstox token has expired, so signals and alerts are paused. Open the app > More > Connect Upstox to refresh it."
    await notifier.send_alert(title, body, "high", force=True)
    await _dispatch_confirmed_push(title, body, "SYSTEM", "WAIT", "TOKEN")


_WARMER_TASK = None


async def _warmer():
    """Keeps whatever people looked at recently (terminal analysis, Data Check) computed, so opening the app on a phone is an
    instant read of a result that is only seconds old. Market open: every 3 s. Closed: every minute, for the last 6 hours."""
    while True:
        open_ = False
        try:
            open_ = market_status()["open"]
            if runtime_token:
                now = time.time()
                horizon = 1800 if open_ else 6 * 3600
                keys = [k for k, t in sorted(_AN_RECENT.items(), key=lambda kv: -kv[1]) if now - t < horizon][:4]
                for k in keys:
                    hit = _AN_CACHE.get(k)
                    age = now - hit[0] if hit else 1e9
                    idle = now - _AN_RECENT.get(k, 0)            # a page that is polling keeps its own key fresh; the warmer covers the gaps
                    due = (age >= 10 and idle >= 8) if open_ else age >= 600
                    if due and k not in _AN_TASKS and k in _AN_FN and now >= _AN_FAIL.get(k, 0):
                        try:
                            await shared_analysis(k, _AN_FN[k], user_request=False)
                        except Exception as exc:
                            logger.debug("Warmer %s: %s", k, str(exc)[:120])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Warmer error: %s", str(exc)[:120])
        await asyncio.sleep(5 if open_ else 60)


@app.on_event("startup")
async def _start_warmer():
    global _WARMER_TASK
    if os.getenv("WARMER", "1").strip() != "0" and (_WARMER_TASK is None or _WARMER_TASK.done()):
        _WARMER_TASK = asyncio.create_task(_warmer())


_KEEPALIVE_TASK = None


async def _keepalive():
    """Render's free web service sleeps after ~15 min without inbound traffic. Requesting our own public URL counts as
    traffic, so the background poller keeps running while the service is awake. It cannot wake a service that is already
    asleep - point a free external pinger (UptimeRobot / cron-job.org, every 5 min) at /health for that."""
    url = (os.getenv("KEEPALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if not url or os.getenv("KEEPALIVE", "1").strip() == "0":
        return
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                await c.get(url + "/health")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Keep-alive ping failed: %s", str(exc)[:120])
        await asyncio.sleep(600)


@app.on_event("startup")
async def _start_keepalive():
    global _KEEPALIVE_TASK
    if _KEEPALIVE_TASK is None or _KEEPALIVE_TASK.done():
        _KEEPALIVE_TASK = asyncio.create_task(_keepalive())


@app.on_event("startup")
async def _start_alert_poller():
    global _ALERT_POLLER_TASK
    if ALERT_BACKGROUND_ON() and (_ALERT_POLLER_TASK is None or _ALERT_POLLER_TASK.done()):
        _ALERT_POLLER_TASK = asyncio.create_task(_alert_poller())


@app.post("/api/signals/settle")
async def settle_signals_now(symbol: str = ""):
    """'Check exits now' button: evaluates open trades against the latest index price immediately."""
    syms = [symbol.upper()] if symbol.upper() in INDEX_KEYS else list(INDEX_KEYS)
    closed = []
    for sym in syms:
        try:
            closed += await settle_from_path(sym)          # v1.19: the 1-minute path first, so a pause can never be filled at a later price
        except Exception as exc:
            logger.info("Path settlement unavailable for %s: %s", sym, str(getattr(exc, "detail", exc))[:140])
        price = _live_index_price(sym)
        if price is None:
            try:
                price = _float_or_none((await index_ltp(sym)).get("ltp"))
            except HTTPException:
                price = None
        if price is not None:
            got = await asyncio.to_thread(settle_signal_exits, sym, price)
            if got:
                await _after_exits(sym, got, live=True)
            closed += got
    return {"closed": len(closed), "details": closed, "target": sx.config()["target"], "cost_points": sx.config()["cost"]}


OPTION_KEYS = ("steps", "moneyness", "lots", "charges_rs", "min_rr", "exit_plan")


def _set_option_overrides(d):
    OPTION_OVERRIDES.clear()
    for k in OPTION_KEYS:
        v = (d or {}).get("opt_" + k)
        if v not in (None, ""):
            OPTION_OVERRIDES[k] = v
    _PICK_CACHE.clear()


@app.get("/api/signals/config")
async def signal_log_config():
    c = sx.config()
    t = dd.THRESHOLDS
    oc = _opt_cfg()
    return {"target": c["target"], "cost_points": c["cost"], "squareoff": c["squareoff"].strftime("%H:%M"),
            "sl_atr": c["sl_atr"], "target_atr": c["target_atr"],
            "default_sl_atr": t.get("atr_sl"), "default_target_atr": t.get("atr_t2"), "min_sl_points": t.get("atr_min_sl_pts"),
            "custom": bool(c["sl_atr"] and c["target_atr"]),
            "exit_plan": OPTION_OVERRIDES.get("exit_plan") if OPTION_OVERRIDES.get("exit_plan") in op.EXIT_PLANS else op.DEFAULT_EXIT_PLAN,
            "min_sl_atr": op.MIN_SL_ATR, "entry_windows": sf.entry_windows_text(),
            "exit_plans": {k: v["label"] for k, v in op.EXIT_PLANS.items()},
            "option": {"steps": oc["steps"], "moneyness": oc["moneyness"], "lots": oc["lots"], "charges_rs": oc["charges_rs"], "min_rr": oc["min_rr"],
                       "custom": bool(OPTION_OVERRIDES)}}


class SignalLogConfig(BaseModel):
    sl_atr: Optional[float] = None          # stop-loss = sl_atr x ATR at the signal (None = back to the default plan)
    target_atr: Optional[float] = None      # target    = target_atr x ATR at the signal
    cost_points: Optional[float] = None
    opt_steps: Optional[int] = None         # v1.19: strikes from ATM for the option that is bought (0-5)
    opt_moneyness: Optional[str] = None     # ITM (default) | OTM
    opt_lots: Optional[int] = None
    opt_charges_rs: Optional[float] = None  # round-trip charges per trade (Rs)
    opt_min_rr: Optional[float] = None      # minimum index reward : risk for a BUY
    opt_exit_plan: Optional[str] = None     # v1.19.1: balanced | winrate


@app.post("/api/signals/config")
async def signal_log_config_set(payload: SignalLogConfig, request: Request):
    _require_admin(request)
    new = {}
    if payload.sl_atr is not None or payload.target_atr is not None:
        if payload.sl_atr is None or payload.target_atr is None:
            raise HTTPException(400, "Give both the stop and the target multiple (or neither, to use the default plan)")
        if not op.MIN_SL_ATR <= payload.sl_atr <= 5 or not 0.3 <= payload.target_atr <= 10:
            raise HTTPException(400, f"Stop must be {op.MIN_SL_ATR:g}-5 ATR (minimum rule) and target 0.3-10 ATR")
        new.update(sl_atr=payload.sl_atr, target_atr=payload.target_atr)
    if payload.cost_points is not None:
        if not 0 <= payload.cost_points <= 200:
            raise HTTPException(400, "Cost allowance must be 0-200 index points")
        new["cost"] = payload.cost_points
    if payload.opt_steps is not None:
        if not 0 <= payload.opt_steps <= 5:
            raise HTTPException(400, "Strike offset must be 0-5 strikes")
        new["opt_steps"] = int(payload.opt_steps)
    if payload.opt_moneyness:
        if payload.opt_moneyness.upper() not in ("OTM", "ITM"):
            raise HTTPException(400, "Moneyness must be OTM or ITM")
        new["opt_moneyness"] = payload.opt_moneyness.upper()
    if payload.opt_lots is not None:
        if not 1 <= payload.opt_lots <= 50:
            raise HTTPException(400, "Lots must be 1-50")
        new["opt_lots"] = int(payload.opt_lots)
    if payload.opt_charges_rs is not None:
        if not 0 <= payload.opt_charges_rs <= 5000:
            raise HTTPException(400, "Charges must be 0-5000 Rs")
        new["opt_charges_rs"] = float(payload.opt_charges_rs)
    if payload.opt_min_rr is not None:
        if not 0.5 <= payload.opt_min_rr <= 5:
            raise HTTPException(400, "Minimum reward:risk must be 0.5-5")
        new["opt_min_rr"] = float(payload.opt_min_rr)
    if payload.opt_exit_plan:
        if payload.opt_exit_plan not in op.EXIT_PLANS:
            raise HTTPException(400, "Exit plan must be one of: " + ", ".join(op.EXIT_PLANS))
        new["opt_exit_plan"] = payload.opt_exit_plan
    sx.set_overrides(new)
    _set_option_overrides(new)
    try:
        await asyncio.to_thread(acc.meta_set, "signal_log_config", json.dumps(new))
    except Exception as exc:
        logger.warning("Signal config not saved: %s", str(exc)[:140])
    return await signal_log_config()


@app.get("/api/signals/summary")
async def signal_summary(group: str = "day", start: str = "", end: str = "", symbol: str = ""):
    """Strategy net profit / loss per day or per month, from the automatically logged and auto-exited trades."""
    if group not in ("day", "month"):
        raise HTTPException(400, "group must be day or month")
    data = await get_signal_log(start, end, symbol, 10000)
    out = sx.summarize(data["rows"], group)
    out["cost_points_per_trade"] = sx.config()["cost"]
    out["note"] = "Index points, after the per-trade cost allowance. Open trades are excluded until they exit; EXPIRED trades (server asleep) carry no P/L."
    return out

@app.get("/api/signal-board")
async def signal_board(start: str = "", end: str = "", symbol: str = "", stage: str = "", limit: int = 5000):
    """v1.19.5: every option signal (WATCH, SETUP, BUY, NO BUY, FAILED) with the option's result over the following hour."""
    if stage and stage.upper() not in sb.STAGES:
        raise HTTPException(400, "stage must be one of: " + ", ".join(sb.STAGES))
    rows = await asyncio.to_thread(sb.query, SIGNAL_LOG_DB, start, end, symbol if symbol.upper() in INDEX_KEYS else "", stage, limit, SIGNAL_LOG_LOCK)
    return {"rows": rows, "count": len(rows), "summary": sb.summary(rows),
            "note": "Recorded automatically during market hours. 60-minute results appear about an hour after each signal (or after the close)."}


@app.get("/api/signal-board/export.csv")
async def signal_board_csv(start: str = "", end: str = "", symbol: str = "", stage: str = ""):
    data = await signal_board(start, end, symbol, stage, 20000)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=sb.FIELDS, extrasaction="ignore")
    writer.writeheader(); writer.writerows(data["rows"])
    payload = io.BytesIO(output.getvalue().encode("utf-8-sig")); payload.seek(0)
    return StreamingResponse(payload, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=option_intelligence_signal_board.csv"})


@app.get("/signals")
async def signal_log_page():
    return FileResponse(BASE_DIR / "static" / "signals.html", headers={"Cache-Control": "no-cache"})

@app.get("/api/signals")
async def get_signal_log(start: str = "", end: str = "", symbol: str = "", limit: int = 2000):
    clauses, params = [], []
    if start: clauses.append("session_date >= ?"); params.append(start)
    if end: clauses.append("session_date <= ?"); params.append(end)
    if symbol and symbol.upper() in ("NIFTY", "BANKNIFTY"): clauses.append("symbol = ?"); params.append(symbol.upper())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with SIGNAL_LOG_LOCK, sqlite3.connect(SIGNAL_LOG_DB, timeout=5) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM signal_log" + where + " ORDER BY generated_at DESC LIMIT ?", params + [max(1, min(limit, 10000))]).fetchall()]
    return {"rows": rows, "count": len(rows), "note": "Logs are created from analysis refreshes; duplicate decisions for the same candle are suppressed."}

@app.get("/api/signals/export.csv")
async def export_signal_log_csv(start: str = "", end: str = "", symbol: str = ""):
    data = await get_signal_log(start, end, symbol, 10000)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SIGNAL_LOG_FIELDS, extrasaction="ignore")
    writer.writeheader(); writer.writerows(data["rows"])
    payload = io.BytesIO(output.getvalue().encode("utf-8-sig")); payload.seek(0)
    return StreamingResponse(payload, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=option_intelligence_signals.csv"})

@app.get("/api/signals/export.xlsx")
async def export_signal_log_xlsx(start: str = "", end: str = "", symbol: str = ""):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        raise HTTPException(status_code=503, detail="Excel export dependency missing; redeploy after installing requirements.txt")
    data = await get_signal_log(start, end, symbol, 10000)
    wb = Workbook(); ws = wb.active; ws.title = "Signal Log"
    ws.append(SIGNAL_LOG_FIELDS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="17365D"); cell.alignment = Alignment(wrap_text=True)
    for row in data["rows"]: ws.append([row.get(k) for k in SIGNAL_LOG_FIELDS])
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        letter = col[0].column_letter
        ws.column_dimensions[letter].width = min(42, max(12, max(len(str(c.value or "")) for c in col[:100]) + 2))
    heads = ["Period", "Signals", "BUY CE", "BUY PE", "Open", "Closed", "Target exits", "Stop exits", "Time exits", "Win rate %", "Gross P/L (pts)",
             "Costs (pts)", "Net P/L (pts)", "Avg win", "Avg loss", "Profit factor", "Expectancy / trade", "Best", "Worst", "Max drawdown"]
    keys = ["period", "signals", "ce", "pe", "open", "closed", "target_exits", "stop_exits", "time_exits", "win_rate", "gross_points",
            "cost_points", "net_points", "avg_win", "avg_loss", "profit_factor", "expectancy", "best", "worst", "max_drawdown"]
    for title, grp in (("Daily Summary", "day"), ("Monthly Summary", "month")):
        sheet = wb.create_sheet(title); sheet.append(heads)
        res = sx.summarize(data["rows"], grp)
        for p in sorted(res["periods"], key=lambda x: x["period"]): sheet.append([p.get(k) for k in keys])
        sheet.append(["TOTAL"] + [res["total"].get(k) for k in keys[1:]])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="17365D"); cell.alignment = Alignment(wrap_text=True)
        for cell in sheet[sheet.max_row]: cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    board = await asyncio.to_thread(sb.query, SIGNAL_LOG_DB, start, end, symbol if symbol.upper() in INDEX_KEYS else "", "", 20000, SIGNAL_LOG_LOCK)
    bs = wb.create_sheet("Signal Board"); bs.append(sb.FIELDS)
    for row in board: bs.append([row.get(k) for k in sb.FIELDS])
    for cell in bs[1]:
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="17365D"); cell.alignment = Alignment(wrap_text=True)
    bs.freeze_panes = "A2"
    bio=io.BytesIO(); wb.save(bio); bio.seek(0)
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition":"attachment; filename=option_intelligence_signals.xlsx"})

# ---- shared, pre-warmed analysis (v1.18.11) -------------------------------------------------------------------
# One analysis is computed at most once per ~2 s and shared by every phone / tab / the alert poller. A result up to 15 s old is
# served instantly while a fresh one is computed in the background, so opening the app never waits for a full recompute.
_AN_CACHE = {}          # args -> (time, result)
_AN_TASKS = {}          # args -> running task
_AN_RECENT = {}         # args -> last time a person asked for it (drives the warmer)
_AN_FN = {}             # args -> coroutine factory that recomputes it
_AN_FAIL = {}           # args -> time before which the warmer will not retry (back-off after an error)
AN_FRESH, AN_STALE, AN_STALE_CLOSED = 2.0, 30.0, 900.0     # market closed: nothing changes, so a 15-minute-old result is instant and fine


_AN_DELIVERED = {}      # args -> timestamp of the result whose reconfirmation event a page has already received


def _an_copy(res, age, strip_event=False):
    import copy
    out = copy.deepcopy(res)
    out["cache_age_s"] = round(age, 1)
    rc = out.get("reconfirm")
    if strip_event and isinstance(rc, dict):
        rc["event"] = None            # an event is shown to a page once; the alert itself was already sent when it was computed
    return out


async def _an_compute(key, fn):
    try:
        try:
            res = await fn()
        except Exception:
            _AN_FAIL[key] = time.time() + 30          # e.g. Upstox rate limit / token expired: do not hammer it
            raise
        _AN_FAIL.pop(key, None)
        _AN_CACHE[key] = (time.time(), res)
        if len(_AN_CACHE) > 40:
            for old in sorted(_AN_CACHE, key=lambda k: _AN_CACHE[k][0])[:15]:
                _AN_CACHE.pop(old, None)
        return res
    finally:
        _AN_TASKS.pop(key, None)


async def shared_analysis(key, fn, user_request=True):
    now = time.time()
    if user_request:
        _AN_RECENT[key] = now
        _AN_FN[key] = fn
    hit = _AN_CACHE.get(key)
    task = _AN_TASKS.get(key)
    age = now - hit[0] if hit else None

    def deliver(stamp, res, a):
        strip = False
        if user_request:
            strip = _AN_DELIVERED.get(key) == stamp
            _AN_DELIVERED[key] = stamp
        return _an_copy(res, a, strip)

    if hit and age < AN_FRESH:
        return deliver(hit[0], hit[1], age)
    if task is None:
        task = _AN_TASKS[key] = asyncio.create_task(_an_compute(key, fn))
    if hit and age < (AN_STALE if market_status()["open"] else AN_STALE_CLOSED):
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)    # never "exception was never retrieved"
        return deliver(hit[0], hit[1], age)
    res = await asyncio.shield(task)
    return deliver(_AN_CACHE[key][0] if key in _AN_CACHE else now, res, 0.0)


@app.get("/api/option-analysis")
async def option_analysis(symbol: str = "NIFTY", instrument_key: str = "", interval: int = 5, mode: str = "standard", score_min: int = 70):
    """Trend / score read of the SELECTED option contract's own premium candles (v1.18.11), so the terminal's price, score, trend
    and decision follow the chart view. Index-only parts (dashboard fusion, S/R room, reconfirmation) stay on /api/analysis."""
    sym = symbol.upper()
    key_ = instrument_key.strip()
    if sym not in INDEX_KEYS or "|" not in key_:
        raise HTTPException(status_code=400, detail="Valid symbol and option instrument_key required.")
    if mode not in MODES:
        raise HTTPException(status_code=400, detail="mode must be one of: " + ", ".join(MODES))
    if score_min not in (60, 65, 70):
        raise HTTPException(status_code=400, detail="score_min must be 60, 65, or 70")

    async def compute():
        result = await candles(symbol=sym, interval=interval, days=5, instrument_key=key_)
        a = await asyncio.to_thread(analyze, result["candles"], f"{sym} option", interval, None, None, None, None, mode, score_min)
        a["instrument_key"] = key_
        return a
    return await shared_analysis(("opt", sym, key_, interval, mode, score_min), compute)


@app.get("/api/analysis")
async def analysis(symbol: str = "NIFTY", interval: int = 5, mode: str = "standard", score_min: int = 70, reconfirm: int = -1):
    if symbol.upper() in INDEX_KEYS and mode in MODES and score_min in (60, 65, 70):
        _USER_COMBOS[(symbol.upper(), interval, mode, score_min)] = time.time()
    key = (symbol.upper(), interval, mode, score_min, reconfirm)
    return await shared_analysis(key, lambda: _analysis_compute(symbol, interval, mode, score_min, reconfirm))


async def _analysis_compute(symbol: str = "NIFTY", interval: int = 5, mode: str = "standard", score_min: int = 70, reconfirm: int = -1):
    """Transparent confirmation engine from index candles + a higher-timeframe trend read, fused with the Data Check
    dashboard signal and stamped with the time the signal was generated (v1.9).
    Not a guarantee and not an order instruction."""
    sym = symbol.upper()
    if mode not in MODES:
        raise HTTPException(status_code=400, detail="mode must be one of: " + ", ".join(MODES))
    if score_min not in (60, 65, 70):
        raise HTTPException(status_code=400, detail="score_min must be 60, 65, or 70")
    if reconfirm == -1:
        reconfirm = sf.RECONFIRM_SECONDS          # -1 = server default (RECONFIRM_SECONDS, 60 s); 0 = off
    if not 0 <= reconfirm <= 600:
        raise HTTPException(status_code=400, detail="reconfirm must be 0 (off) to 600 seconds")
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
                    volume_source=result.get("volume_source"), mode=mode, score_min=score_min)  # forming candle ignored inside analyze()
        dash = await dash_task if dash_task else None
    except BaseException:
        if dash_task:
            dash_task.cancel()
        raise
    if a.get("status") != "ok":
        return a
    try:
        a["atr_levels"] = dd.trade_levels({"side": a.get("side"), "level": a.get("signal")}, {}, atr=a.get("atr"), price=a.get("last_close"))
    except Exception:
        a["atr_levels"] = None
    try:
        market = market_status()
        replay = sf.replay_signal_start(result["candles"], interval, a, htf_raw=htf_raw, htf_interval=htf_interval,
                                        volume_source=result.get("volume_source"), mode=mode)
        track = SIGNAL_TRACKER.update(sym, sf.dash_key(dash), a.get("last_close"), time.time())
        rc_name = f"{sym}|{interval}|{mode}|{score_min}"
        LAST_ANALYSIS_AT[rc_name] = time.time()
        live_px = _live_index_price(sym) or (_float_or_none(result["candles"][-1].get("close")) if result.get("candles") else None)
        fused = sf.fuse(a, dash, replay, track, datetime.now(IST), market["open"], interval, mode=mode,
                        reconfirm={"tracker": RECONFIRM, "name": rc_name, "window": reconfirm, "price": live_px, "ts": time.time()} if sym in INDEX_KEYS else None,
                        daily_losses=stopouts_today(sym) if MAX_DAILY_LOSSES else None, max_daily_losses=MAX_DAILY_LOSSES)
        a["reconfirm"] = fused.get("reconfirm")
        final = fused["final"]
        # v1.19: WATCH / PENDING / CONFIRMED -> the option 2 strikes from ATM on the trade side (the terminal switches its chart to it)
        pick = None
        if sym in INDEX_KEYS and final.get("side") in ("CE", "PE") and final.get("level") in ("WATCH", "PENDING", "CONFIRMED", "STALE"):
            try:
                pick = await asyncio.wait_for(_option_pick(sym, final["side"]), timeout=6)
            except Exception as exc:
                logger.info("Option pick unavailable for %s: %s", sym, str(getattr(exc, "detail", exc))[:140])
        a["option_pick"] = pick
        # v1.19: a CONFIRMED setup becomes a BUY only after the profit check (target before the next S/R, R:R, premium after charges)
        if final.get("level") == "CONFIRMED" and sym in INDEX_KEYS:
            try:
                plan = await _buy_plan(sym, interval, mode, score_min, a, result.get("candles") or [], pick)
            except Exception as exc:            # never let a failed check turn into a BUY, and never take the analysis down
                logger.warning("Profit check failed to run: %s", str(exc)[:160])
                plan = {"ok": False, "index": None, "option": None, "summary": f"No buy — profit check could not run ({type(exc).__name__})",
                        "checks": [{"label": "Profit check ran", "pass": False, "detail": f"{type(exc).__name__}: {str(exc)[:120]}"}]}
            a["buy_plan"] = plan
            for ch in plan["checks"]:
                fused["gates"].append({"label": "Profit check: " + ch["label"], "pass": ch["pass"], "detail": ch["detail"]})
            if plan["ok"]:
                o, ix = plan["option"], plan["index"]
                final.update(buy=plan, action=f"BUY {o['name']}",
                             headline=plan["summary"] + f" · target basis: {ix['target_basis']}")
            else:
                final.update(level="WATCH", action=f"WATCH {final['side']} — no buy (profit check)", headline=plan["summary"], profit_rejected=True)
                final.setdefault("blockers", []).extend(f"Profit check: {ch['label']}: {ch['detail']}" for ch in plan["checks"] if not ch["pass"])
        try:
            await _route_signal_alerts(sym, interval, mode, a, fused, market["open"])
        except Exception as exc:
            logger.warning("Alert routing failed: %s", str(exc)[:160])
        if sym in INDEX_KEYS and in_session():           # v1.19.5: every option signal goes on the signal board automatically
            try:
                brow = sb.make_row(datetime.now(IST), sym, interval, mode, a, final, fused.get("reconfirm"), pick, fused.get("timing"))
                if brow and await asyncio.to_thread(sb.insert, SIGNAL_LOG_DB, brow, SIGNAL_LOG_LOCK):
                    _SIGNAL_LOG_DIRTY.set()
            except Exception as exc:
                logger.warning("Signal board write failed: %s", str(exc)[:160])
        a["gates"] = (a.get("gates") or []) + fused["gates"]
        a.update({"final": fused["final"], "timing": fused["timing"], "dashboard": fused["dashboard"],
                  "signal_technical": a.get("signal"), "decision_technical": a.get("decision"),
                  "decision": fused["final"]["action"]})
    except Exception as exc:          # fusion must never take the whole analysis down
        logger.warning("Signal fusion failed: %s", exc)
        a["fusion_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    # Auto-exit first (live tick, only while the exit watcher has been watching without a pause), then log any new BUY.
    if sym in INDEX_KEYS and _tick_settle_ok(sym) and _live_index_price(sym) is not None:
        got = settle_signal_exits(sym, _live_index_price(sym), atr=a.get("atr"))
        if got:
            _spawn(_after_exits(sym, got, live=True))
    record_signal_row(sym, interval, mode, a)
    return a


# ---- v1.19: option pick, profit check, alert routing ----------------------------------------------------------------------------
OPTION_OVERRIDES = {}      # owner settings from the Signals page: steps, moneyness, lots, charges_rs, min_rr
_PICK_CACHE = {}
_BUY_SENT = {}             # (symbol, interval, mode, reconfirm start) -> time the BUY alert went out (one BUY per setup)
_REJECT_SENT = {}


def _opt_cfg():
    c = dict(op.DEFAULTS)
    c.update({k: v for k, v in OPTION_OVERRIDES.items() if v not in (None, "")})
    return c


async def _option_pick(sym, side):
    """The contract `steps` strikes from ATM on the trade side, with the freshest premium / book available. Cached 5 s."""
    hit = _PICK_CACHE.get((sym, side))
    if hit and time.time() - hit[0] < 5:
        return hit[1]
    cfg = _opt_cfg()
    expiry = await resolve_expiry(sym, "current_week")
    chain = await get_chain(sym, expiry)
    lot = await lot_size_for(sym, expiry)
    spot = _live_index_price(sym)
    pick = op.pick_contract(chain["rows"], spot, side, sym, cfg["steps"], cfg["moneyness"], expiry, lot)
    if pick and pick.get("instrument_key"):
        _pin_keys(sym, [pick["instrument_key"]])            # keep it on the live WebSocket from now on
        with STREAM_LOCK:
            v = _tick_view(pick["instrument_key"], time.time())
        if v and v.get("fresh") and v.get("ltp"):
            pick["ltp"], pick["price_source"] = float(v["ltp"]), "live tick"
            if (v.get("bid") or 0) > 0 and (v.get("ask") or 0) > 0:
                pick["bid"], pick["ask"] = float(v["bid"]), float(v["ask"])
        else:
            pick["price_source"] = "option-chain snapshot"
        pick["name"] = f"{sym} {pick['strike']:g} {side}"
    _PICK_CACHE[(sym, side)] = (time.time(), pick)
    return pick


async def _buy_plan(sym, interval, mode, score_min, a, raw_candles, pick):
    side = (a.get("final") or {}).get("side") or a.get("side")
    t, sc = dd.THRESHOLDS, sx.config()
    # v1.19.5: stop never closer than 1.5 ATR (min 20 pts); target from the exit plan (default 1.5 ATR) or the custom multiple
    sl_atr, min_sl, tgt_atr, plan_key = op.plan_levels(OPTION_OVERRIDES.get("exit_plan"), sc["sl_atr"], sc["target_atr"])
    ocfg = _opt_cfg()
    ocfg["min_target_atr"] = min(ocfg["min_target_atr"], tgt_atr)
    entry = _live_index_price(sym) or _float_or_none(a.get("last_close"))
    try:
        sr = op.sr_levels(normalize_candles(raw_candles), a.get("atr"), interval, sym)
    except Exception as exc:
        logger.info("S/R for the profit check failed: %s", str(exc)[:120])
        sr = None
    ptrend = None
    if pick and pick.get("instrument_key"):
        try:
            ptrend = await asyncio.wait_for(option_analysis(symbol=sym, instrument_key=pick["instrument_key"], interval=interval, mode=mode,
                                                            score_min=score_min if score_min in (60, 65, 70) else 70), timeout=6)
        except Exception as exc:
            logger.info("Premium trend unavailable: %s", str(getattr(exc, "detail", exc))[:120])
    plan = op.profit_plan(side, entry, a.get("atr"), sr, pick, ptrend, sl_atr, min_sl, tgt_atr, ocfg)
    plan["exit_plan"] = plan_key
    plan["sr"] = {"resistances": (sr or {}).get("resistances", [])[:3], "supports": (sr or {}).get("supports", [])[:3]}
    return plan


async def _route_signal_alerts(sym, interval, mode, a, fused, market_open):
    """One alert per transition. BUY (event CONFIRMED) only when the profit check passed - once per setup; REJECTED when a confirmed
    setup failed it; STARTED / FAILED from the reconfirmation; CHANGED for any other change (e.g. a new WATCH with the chosen option)."""
    if sym not in INDEX_KEYS:
        return
    final = fused["final"]
    rc = fused.get("reconfirm") or {}
    ev = rc.get("event")
    # one setup = one reconfirmation attempt (or, with reconfirmation off, one signal start time)
    akey = (sym, interval, mode, rc.get("since") or (fused.get("timing") or {}).get("generated_at"))
    send = None
    if final.get("level") == "CONFIRMED" and final.get("buy"):
        if akey not in _BUY_SENT:
            _BUY_SENT[akey] = time.time()
            send = "CONFIRMED"
    elif final.get("profit_rejected") and akey not in _REJECT_SENT:
        _REJECT_SENT[akey] = time.time()
        send = "REJECTED"
    elif ev in ("STARTED", "FAILED"):
        send = ev
    changed = _signal_change_event((sym, interval, mode), final, market_open)     # always called: it tracks the previous state
    if send is None and not ev and changed:
        send = "CHANGED"
    for d in (_BUY_SENT, _REJECT_SENT):
        if len(d) > 300:
            for k in sorted(d, key=d.get)[:100]:
                d.pop(k, None)
    if not send or not market_open or not in_session() or not PUSH_DELIVERY_ENABLED():     # re-checked at send time (v1.19.2)
        return
    if send in ("STARTED", "FAILED", "CONFIRMED") and send not in notifier.events():
        return
    _spawn(_send_signal_alert(send, sym, interval, mode, a, fused))



# ================================================================ Data Check dashboard (v1.8)
QUOTE_CACHE = {}
QUOTE_TTL = 2          # breadth / sector quotes: REST, kept short so the dashboard follows the market
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
    spot = rows[0].get("underlying_spot_price")
    with STREAM_LOCK:
        iv = _tick_view(INDEX_KEYS[sym], time.time())
    if iv and iv.get("fresh") and iv.get("ltp"):
        spot = iv["ltp"]                         # live index tick decides the ATM strike, not the 4 s old REST snapshot
    out = dd.build_option_premium(rows, spot, dict(_fresh_option_ticks()), steps=2)
    try:                                         # keep the ATM +/-2 contracts on the live stream
        keys = [(t[side] or {}).get("key") for t in out.get("table", []) for side in ("ce", "pe")]
        if any(keys):
            _pin_keys(sym, keys)
    except Exception:
        pass
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
    closed, _ = drop_forming(cs, 5)
    atr = dd.atr_from_candles(closed, dd.THRESHOLDS["atr_period"])
    return dd.index_zone(cs, price, label, atr=atr)


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
    _check_graph_args(sym, interval, offset)       # reject bad input before anything is cached
    key = ("dash", sym, universe, expiry, interval, offset)
    return await shared_analysis(key, lambda: _dashboard_summary_compute(symbol, universe, expiry, interval, offset))


async def _dashboard_summary_compute(symbol: str = "NIFTY", universe: str = "", expiry: str = "current_week", interval: int = 5, offset: int = 0):
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
    final["levels"] = dd.trade_levels(final, blocks)
    _stamp_dashboard_times(sym, blocks, final)
    return {"symbol": sym, "universe": universe, "generated_at": utc_now_iso(), "market": market, **blocks,
            "overall": dd.overall_bias(sigs), "final": final,
            "note": "Signals are rule-based reads of live data, not trade instructions. Index price, ATM option premiums and the premium graph follow the live WebSocket; breadth and sector quotes are REST (about 2 s old)."
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
    _pin_keys(sym, [ce_key, pe_key])
    fold = lambda cs, key: normalize_candles(overlay_live_tick([{k: v for k, v in c.items() if k != "_dt"} for c in cs], key, interval)) if cs else cs
    ce_c, pe_c = fold(ce_c, ce_key), fold(pe_c, pe_key)
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
