import os, re, secrets, threading, time, asyncio, json, logging
from datetime import date, timedelta, datetime, timezone
from urllib.parse import urlencode
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

APP_TITLE = "Option Intelligence"
UPSTOX_API_KEY = os.getenv("UPSTOX_API_KEY", "").strip()
UPSTOX_API_SECRET = os.getenv("UPSTOX_API_SECRET", "").strip()
UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "").strip()
# For quick setup, an access token may be supplied as an environment variable.
# Do not put tokens in frontend JavaScript or commit them to GitHub.
UPSTOX_ACCESS_TOKEN = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()
API_BASE = "https://api.upstox.com"
API_V3 = "https://api.upstox.com/v3"

app = FastAPI(title=APP_TITLE, version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
oauth_states = set()
runtime_token = UPSTOX_ACCESS_TOKEN
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("option_intelligence")
# In-memory live cache only; secrets never leave the backend. On restart, reconnect/re-authenticate.
LIVE_TICKS = {}
STREAMS = {}
STREAM_ERRORS = {}
CHAIN_CACHE = {}
CHAIN_CACHE_TTL = 12
STREAM_LOCK = threading.RLock()

INDEX_KEYS = {
    "NIFTY": "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
}

def token_or_401():
    if not runtime_token:
        raise HTTPException(
            status_code=401,
            detail="Upstox access token not configured. Add UPSTOX_ACCESS_TOKEN in Render environment variables, or configure OAuth credentials."
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
        raise HTTPException(status_code=response.status_code, detail={"upstox_error": detail})
    try:
        return response.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Unexpected response from Upstox")

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_nested(obj, *path):
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _on_stream_message(message):
    """Store latest decoded SDK event. Upstox SDK decodes V3 protobuf into dict-like payloads."""
    try:
        payload = message if isinstance(message, dict) else json.loads(message) if isinstance(message, str) else None
        if not isinstance(payload, dict):
            return
        feeds = payload.get("feeds") or {}
        received = payload.get("currentTs") or payload.get("current_ts")
        for instrument_key, feed in feeds.items():
            # SDK response fields may be nested by feed mode; handle common V3 full/option_greeks shapes.
            core = feed.get("fullFeed", {}).get("marketFF", {}) if isinstance(feed, dict) else {}
            oc = feed.get("oc", {}) if isinstance(feed, dict) else {}
            if not core and isinstance(feed, dict):
                core = feed.get("marketFF", {}) or feed.get("fullFeed", {}) or {}
            ltpc = (core.get("ltpc") or oc.get("ltpc") or (feed.get("ltpc", {}) if isinstance(feed, dict) else {}))
            efd = core.get("eFeedDetails") or oc.get("eFeedDetails") or {}
            greeks = core.get("optionGreeks") or oc.get("optionGreeks") or feed.get("optionGreeks", {}) or {}
            ltp = ltpc.get("ltp") if isinstance(ltpc, dict) else None
            ltt = ltpc.get("ltt") if isinstance(ltpc, dict) else None
            oi = efd.get("oi") if isinstance(efd, dict) else None
            iv = greeks.get("iv") if isinstance(greeks, dict) else None
            if iv is not None:
                # Upstox WebSocket optionGreeks IV is commonly decimal fraction; normalize to percentage.
                try:
                    iv = float(iv) * 100 if 0 < float(iv) < 3 else float(iv)
                except (TypeError, ValueError):
                    pass
            with STREAM_LOCK:
                old = LIVE_TICKS.get(instrument_key, {})
                LIVE_TICKS[instrument_key] = {
                    "instrument_key": instrument_key,
                    "ltp": ltp if ltp is not None else old.get("ltp"),
                    "oi": oi if oi is not None else old.get("oi"),
                    "iv": iv if iv is not None else old.get("iv"),
                    "last_trade_time": ltt or old.get("last_trade_time"),
                    "received_at": utc_now_iso(),
                    "feed_timestamp": received,
                }
    except Exception as exc:
        logger.debug("Could not parse market stream event: %s", exc)


def _start_stream(symbol, instruments):
    """Start one Upstox V3 SDK stream per underlying, in a daemon thread."""
    with STREAM_LOCK:
        existing = STREAMS.get(symbol)
        existing_keys = set(existing.get("keys", [])) if existing else set()
        new_keys = list(dict.fromkeys(instruments))
        if existing and existing.get("streamer"):
            add = [k for k in new_keys if k not in existing_keys]
            if add:
                try:
                    existing["streamer"].subscribe(add, "full")
                    existing["keys"] = list(existing_keys.union(add))
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


def _pick_near_atm_contracts(contracts, spot, max_strikes=12):
    options = [c for c in contracts if c.get("instrument_key") and c.get("instrument_type") in ("CE", "PE")]
    strikes = sorted({float(c["strike_price"]) for c in options if c.get("strike_price") is not None})
    if not strikes:
        return []
    atm = min(strikes, key=lambda x: abs(x - float(spot))) if spot else strikes[len(strikes)//2]
    nearest = sorted(strikes, key=lambda x: abs(x-atm))[:max_strikes]
    keep = set(nearest)
    return [c for c in options if float(c.get("strike_price", -1)) in keep]


@app.get("/")
async def home():
    return FileResponse("static/index.html")

@app.get("/api/status")
async def status():
    return {
        "app": APP_TITLE,
        "provider": "Upstox",
        "token_configured": bool(runtime_token),
        "oauth_configured": bool(UPSTOX_API_KEY and UPSTOX_API_SECRET and UPSTOX_REDIRECT_URI),
        "mode": "Upstox REST + optional V3 WebSocket; analysis-only; no orders are placed", "websocket_sdk_available": __import__("importlib").util.find_spec("upstox_client") is not None, "tradingview_webhook_configured": bool(os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip())
    }

@app.get("/api/login")
async def login():
    if not (UPSTOX_API_KEY and UPSTOX_REDIRECT_URI):
        raise HTTPException(status_code=400, detail="Set UPSTOX_API_KEY and UPSTOX_REDIRECT_URI first.")
    state = secrets.token_urlsafe(24)
    oauth_states.add(state)
    params = {
        "response_type": "code",
        "client_id": UPSTOX_API_KEY,
        "redirect_uri": UPSTOX_REDIRECT_URI,
        "state": state
    }
    return RedirectResponse("https://api.upstox.com/v2/login/authorization/dialog?" + urlencode(params))

@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    global runtime_token
    if error:
        raise HTTPException(status_code=400, detail=f"Upstox login failed: {error}")
    if not code or not state or state not in oauth_states:
        raise HTTPException(status_code=400, detail="Missing or invalid OAuth state. Start login again.")
    oauth_states.discard(state)
    if not (UPSTOX_API_KEY and UPSTOX_API_SECRET and UPSTOX_REDIRECT_URI):
        raise HTTPException(status_code=400, detail="OAuth environment variables are incomplete.")
    payload = {
        "code": code,
        "client_id": UPSTOX_API_KEY,
        "client_secret": UPSTOX_API_SECRET,
        "redirect_uri": UPSTOX_REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.upstox.com/v2/login/authorization/token",
            data=payload,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail={"upstox_error": resp.text[:500]})
    runtime_token = resp.json().get("access_token", "")
    if not runtime_token:
        raise HTTPException(status_code=502, detail="No access token returned by Upstox.")
    # In-memory token is not durable across restart/redeploy; use env token for the simple deployment mode.
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
    end = date.today()
    start = end - timedelta(days=days)
    url = f"{API_V3}/historical-candle/{key}/minutes/{interval}/{end.isoformat()}/{start.isoformat()}"
    data = await upstox_get(url)
    candles_raw = (data.get("data") or {}).get("candles") or []
    # Upstox rows: timestamp, open, high, low, close, volume, open_interest (if available)
    candles = []
    for row in candles_raw:
        if len(row) < 6:
            continue
        candles.append({
            "time": row[0],
            "open": row[1], "high": row[2], "low": row[3], "close": row[4],
            "volume": row[5], "oi": row[6] if len(row) > 6 else None
        })
    return {"symbol": symbol, "instrument_key": key, "interval": interval, "candles": candles}

@app.get("/api/option-contracts")
async def option_contracts(symbol: str = "NIFTY", expiry_date: str = ""):
    """Return option contracts. If unfiltered contracts are empty, try Upstox's supported relative expiries."""
    key = INDEX_KEYS.get(symbol.upper())
    if not key:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")

    async def fetch_contracts(expiry=""):
        params = {"instrument_key": key}
        if expiry:
            params["expiry_date"] = expiry
        response = await upstox_get(f"{API_BASE}/v2/option/contract", params)
        rows = response.get("data", []) if isinstance(response, dict) else []
        # Be tolerant of wrappers used by proxy/provider responses.
        if isinstance(rows, dict):
            rows = rows.get("data", rows.get("contracts", []))
        return rows if isinstance(rows, list) else []

    # A specific actual date or documented relative keyword should be honored directly.
    if expiry_date:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry_date) or expiry_date in {
            "current_week", "next_week", "far_week", "current_month", "next_month", "far_month"
        }:
            rows = await fetch_contracts(expiry_date)
            return {"status": "success", "data": rows, "requested_expiry": expiry_date, "source": "Upstox option contract API"}
        raise HTTPException(status_code=400, detail="expiry_date must be YYYY-MM-DD or a supported Upstox expiry keyword.")

    # First try the unfiltered endpoint; some API responses/accounts return no rows without a keyword.
    rows = await fetch_contracts()
    if not rows:
        collected = {}
        errors = []
        for keyword in ("current_week", "current_month", "next_week", "next_month", "far_week", "far_month"):
            try:
                for row in await fetch_contracts(keyword):
                    ident = row.get("instrument_key") or (row.get("expiry"), row.get("trading_symbol"))
                    if ident:
                        collected[ident] = row
            except HTTPException as exc:
                errors.append(f"{keyword}: {str(exc.detail)[:160]}")
        rows = list(collected.values())
        if not rows and errors:
            logger.warning("Upstox option contract lookup returned no rows for %s; keyword errors: %s", symbol.upper(), " | ".join(errors))
    return {"status": "success", "data": rows, "source": "Upstox option contract API"}


@app.get("/api/option-chain")
async def option_chain(symbol: str = "NIFTY", expiry_date: str = ""):
    key = INDEX_KEYS.get(symbol.upper())
    if not key:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry_date or ""):
        raise HTTPException(status_code=400, detail="Choose an actual expiry date. Click Load expiries, then Load chain.")
    data = await upstox_get(f"{API_BASE}/v2/option/chain", {"instrument_key": key, "expiry_date": expiry_date})
    return data


@app.get("/api/live-derivatives")
async def live_derivatives(symbol: str = "NIFTY", expiry_date: str = "current_week"):
    """REST snapshot for PCR + chain OI/IV; V3 WebSocket updates selected near-ATM contracts."""
    sym = symbol.upper()
    key = INDEX_KEYS.get(sym)
    if not key:
        raise HTTPException(status_code=400, detail="Unknown index symbol.")
    cache_key = (sym, expiry_date)
    now = time.time()
    cached = CHAIN_CACHE.get(cache_key)
    if cached and now - cached["cached_at"] < CHAIN_CACHE_TTL:
        chain_data = cached["data"]
    else:
        try:
            chain_data = await upstox_get(f"{API_BASE}/v2/option/chain", {"instrument_key": key, "expiry_date": expiry_date})
        except HTTPException as exc:
            return {"status": "UNAVAILABLE", "reason": str(exc.detail)[:300], "symbol": sym, "expiry": expiry_date, "timestamp": utc_now_iso(), "oi_status": "UNAVAILABLE", "pcr": "UNAVAILABLE", "iv_average": "UNAVAILABLE", "websocket": "UNAVAILABLE"}
        CHAIN_CACHE[cache_key] = {"cached_at": now, "fetched_at": utc_now_iso(), "data": chain_data}
    rows = (chain_data or {}).get("data") or []
    expiry_used = expiry_date
    fallback_used = False
    if not rows and expiry_date == "current_week":
        # Some underlyings may not have a weekly expiry listed; transparently fall back to current month.
        try:
            fallback = await upstox_get(f"{API_BASE}/v2/option/chain", {"instrument_key": key, "expiry_date": "current_month"})
            fallback_rows = (fallback or {}).get("data") or []
            if fallback_rows:
                rows, chain_data, expiry_used = fallback_rows, fallback, "current_month"
                fallback_used = True
                CHAIN_CACHE[(sym, "current_month")] = {"cached_at": now, "fetched_at": utc_now_iso(), "data": fallback}
                CHAIN_CACHE[cache_key] = {"cached_at": now, "fetched_at": utc_now_iso(), "data": fallback}
        except HTTPException:
            pass
    if not rows:
        return {"status": "UNAVAILABLE", "reason": "Upstox returned no option-chain rows for current weekly or fallback monthly expiry.", "symbol": sym, "expiry": expiry_date, "timestamp": utc_now_iso(), "oi_status": "UNAVAILABLE", "pcr": "UNAVAILABLE", "iv_average": "UNAVAILABLE", "websocket": "UNAVAILABLE"}
    expiry_actual = rows[0].get("expiry") or expiry_used
    spot = rows[0].get("underlying_spot_price")
    try:
        contracts_data = await upstox_get(f"{API_BASE}/v2/option/contract", {"instrument_key": key, "expiry_date": expiry_actual})
        contracts = (contracts_data or {}).get("data") or []
    except HTTPException as exc:
        contracts = []
        with STREAM_LOCK:
            STREAM_ERRORS[sym] = f"Option contracts unavailable: {str(exc.detail)[:200]}"
    selected = _pick_near_atm_contracts(contracts, spot, max_strikes=12)
    if selected:
        _start_stream(sym, [c["instrument_key"] for c in selected])
    # Compute aggregate PCR from the entire same-expiry chain, not from one row's PCR.
    total_call_oi = 0.0; total_put_oi = 0.0; usable_call = 0; usable_put = 0
    chain_out = []
    iv_values = []
    for row in rows:
        cm = (row.get("call_options") or {}).get("market_data") or {}
        pm = (row.get("put_options") or {}).get("market_data") or {}
        cg = (row.get("call_options") or {}).get("option_greeks") or {}
        pg = (row.get("put_options") or {}).get("option_greeks") or {}
        if cm.get("oi") is not None:
            total_call_oi += float(cm["oi"]); usable_call += 1
        if pm.get("oi") is not None:
            total_put_oi += float(pm["oi"]); usable_put += 1
        civ = cg.get("iv", cm.get("iv")); piv = pg.get("iv", pm.get("iv"))
        for val in (civ, piv):
            try:
                if val is not None and float(val) > 0: iv_values.append(float(val))
            except (TypeError, ValueError): pass
        chain_out.append({
            "strike": row.get("strike_price"),
            "call": {"instrument_key": (row.get("call_options") or {}).get("instrument_key"), "ltp": cm.get("ltp"), "oi": cm.get("oi"), "prev_oi": cm.get("prev_oi"), "iv": civ},
            "put": {"instrument_key": (row.get("put_options") or {}).get("instrument_key"), "ltp": pm.get("ltp"), "oi": pm.get("oi"), "prev_oi": pm.get("prev_oi"), "iv": piv},
            "pcr_row": row.get("pcr")
        })
    aggregate_pcr = total_put_oi / total_call_oi if total_call_oi > 0 and usable_call and usable_put else None
    stream_age_sec = None
    live_near_atm = []
    with STREAM_LOCK:
        stream_error = STREAM_ERRORS.get(sym)
        stream_state = STREAMS.get(sym)
        for c in selected:
            tick = LIVE_TICKS.get(c["instrument_key"])
            if tick:
                try:
                    age = max(0, now - datetime.fromisoformat(tick["received_at"].replace("Z", "+00:00")).timestamp())
                    stream_age_sec = age if stream_age_sec is None else min(stream_age_sec, age)
                except Exception: pass
                live_near_atm.append({"instrument_key": c["instrument_key"], "trading_symbol": c.get("trading_symbol"), "strike": c.get("strike_price"), "type": c.get("instrument_type"), **tick})
    stream_ok = bool(live_near_atm and stream_age_sec is not None and stream_age_sec <= 15)
    # REST snapshot is valid for this response but not labelled as a WebSocket tick.
    return {
        "status": "LIVE" if stream_ok else ("REST_SNAPSHOT" if rows else "UNAVAILABLE"),
        "symbol": sym, "underlying_key": key, "expiry": expiry_actual,
        "timestamp": utc_now_iso(), "rest_snapshot_timestamp": (CHAIN_CACHE.get(cache_key) or {}).get("fetched_at", utc_now_iso()),
        "chain_snapshot_age_seconds": round(now - (CHAIN_CACHE.get(cache_key) or {}).get("cached_at", now), 1),
        "expiry_mode_requested": expiry_date, "spot": spot, "pcr": round(aggregate_pcr, 4) if aggregate_pcr is not None else "UNAVAILABLE",
        "total_call_oi": total_call_oi if usable_call else "UNAVAILABLE",
        "total_put_oi": total_put_oi if usable_put else "UNAVAILABLE",
        "oi_status": "REST_SNAPSHOT" if usable_call and usable_put else "UNAVAILABLE",
        "iv_average": round(sum(iv_values)/len(iv_values), 2) if iv_values else "UNAVAILABLE",
        "iv_status": "REST_SNAPSHOT" if iv_values else "UNAVAILABLE",
        "websocket": "LIVE" if stream_ok else ("CONNECTING" if stream_state and not stream_error else "UNAVAILABLE"),
        "websocket_error": stream_error,
        "websocket_last_tick_age_seconds": round(stream_age_sec, 1) if stream_age_sec is not None else None,
        "selected_contracts": len(selected), "live_near_atm": live_near_atm,
        "chain": chain_out,
        "freshness_policy_seconds": 15,
        "note": ("PCR and chain OI are REST snapshots. Only contract fields in live_near_atm are confirmed WebSocket ticks. Values older than 15 seconds are not labelled live." + (" Current-week chain was empty; monthly expiry fallback was used." if fallback_used else ""))
    }


@app.websocket("/ws/live")
async def live_socket(websocket: WebSocket, symbol: str = "NIFTY"):
    await websocket.accept()
    sym = symbol.upper()
    try:
        while True:
            now = time.time()
            with STREAM_LOCK:
                stream_error = STREAM_ERRORS.get(sym)
                state = STREAMS.get(sym)
                recent = []
                for tick in LIVE_TICKS.values():
                    if tick.get("instrument_key") in set(state.get("keys", [])) if state else False:
                        try:
                            age = now - datetime.fromisoformat(tick["received_at"].replace("Z", "+00:00")).timestamp()
                        except Exception: age = 999999
                        recent.append({**tick, "age_seconds": round(max(0, age), 1), "fresh": age <= 15})
            await websocket.send_json({"type": "ticks", "symbol": sym, "timestamp": utc_now_iso(), "stream": "LIVE" if any(x["fresh"] for x in recent) else "STALE/CONNECTING", "error": stream_error, "ticks": recent})
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        logger.debug("Browser websocket closed: %s", exc)


@app.post("/api/tradingview/webhook")
async def tradingview_webhook(request: Request):
    """Optional TradingView alert receiver. It cannot send backend data back into Pine."""
    expected = os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="TradingView webhook is disabled until TRADINGVIEW_WEBHOOK_SECRET is configured.")
    payload = await request.json()
    if payload.get("secret") != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret.")
    # Store only the latest alert in memory; no orders are placed.
    app.state.last_tv_alert = {"received_at": utc_now_iso(), "payload": {k: v for k, v in payload.items() if k != "secret"}}
    return {"ok": True, "received_at": app.state.last_tv_alert["received_at"], "note": "Alert received only; this does not inject data into Pine or place orders."}


@app.get("/api/tradingview/last-alert")
async def tradingview_last_alert():
    return getattr(app.state, "last_tv_alert", {"status": "UNAVAILABLE", "message": "No TradingView alert received in this running instance."})




@app.get("/api/option-quote")
async def option_quote(instrument_key: str):
    """Return a selected option's latest quote, preferring a fresh WebSocket tick."""
    key = instrument_key.strip()
    if not key or "|" not in key:
        raise HTTPException(status_code=400, detail="A valid Upstox option instrument_key is required.")
    now = time.time()
    with STREAM_LOCK:
        tick = LIVE_TICKS.get(key)
        if tick:
            try:
                age = max(0.0, now - datetime.fromisoformat(tick["received_at"].replace("Z", "+00:00")).timestamp())
            except Exception:
                age = 999999
            if age <= 15 and tick.get("ltp") is not None:
                return {
                    "status": "LIVE_TICK", "instrument_key": key, "ltp": tick["ltp"],
                    "timestamp": tick.get("received_at"), "age_seconds": round(age, 1),
                    "source": "Upstox V3 WebSocket"
                }
    # REST LTP fallback is explicitly identified as a snapshot, not a WebSocket tick.
    data = await upstox_get(f"{API_BASE}/v2/market-quote/ltp", {"instrument_key": key})
    quote_data = (data or {}).get("data") or {}
    quote = quote_data.get(key)
    if quote is None and quote_data:
        # Upstox may key response rows by trading symbol rather than instrument_key.
        quote = next(iter(quote_data.values()))
    ltp = quote.get("last_price", quote.get("ltp")) if isinstance(quote, dict) else None
    if ltp is None:
        return {"status": "UNAVAILABLE", "instrument_key": key, "ltp": None, "timestamp": utc_now_iso(), "source": "REST quote", "reason": "No LTP returned for selected contract."}
    return {
        "status": "REST_SNAPSHOT", "instrument_key": key, "ltp": float(ltp),
        "timestamp": utc_now_iso(), "age_seconds": 0, "source": "Upstox REST quote",
        "note": "REST snapshot; not a confirmed WebSocket tick."
    }

@app.get("/api/analysis")
async def analysis(symbol: str = "NIFTY", interval: int = 5):
    # Transparent technical score from index candles. Not a guarantee and not an order instruction.
    result = await candles(symbol=symbol, interval=interval, days=5)
    cs = result["candles"]
    if len(cs) < 30:
        return {"status": "insufficient_data", "message": "Not enough candles to calculate indicators.", "score": 0, "decision": "WAIT"}
    closes = [float(c["close"]) for c in cs]
    highs = [float(c["high"]) for c in cs]
    lows = [float(c["low"]) for c in cs]
    volumes = [float(c["volume"] or 0) for c in cs]
    def ema(values, n):
        alpha = 2/(n+1)
        out = [values[0]]
        for x in values[1:]:
            out.append(alpha*x + (1-alpha)*out[-1])
        return out
    e9, e21 = ema(closes, 9), ema(closes, 21)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    avg_gain = sum(gains[-14:])/14
    avg_loss = sum(losses[-14:])/14
    rsi = 100 if avg_loss == 0 else 100 - 100/(1 + avg_gain/avg_loss)
    # Wilder-style directional movement approximation and ATR
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(cs)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
        up, down = highs[i]-highs[i-1], lows[i-1]-lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
    atr = sum(trs[-14:])/min(14, len(trs))
    avg_vol = sum(volumes[-21:-1])/max(1, len(volumes[-21:-1]))
    rel_vol = volumes[-1]/avg_vol if avg_vol else 0
    # Simple intraday VWAP approximation on the returned candle window.
    pv = sum(((float(c["high"])+float(c["low"])+float(c["close"]))/3)*float(c["volume"] or 0) for c in cs)
    vv = sum(float(c["volume"] or 0) for c in cs)
    vwap = pv/vv if vv else closes[-1]
    bull = 0; bear = 0
    bull += 20 if e9[-1] > e21[-1] else 0
    bear += 20 if e9[-1] < e21[-1] else 0
    bull += 15 if closes[-1] > vwap else 0
    bear += 15 if closes[-1] < vwap else 0
    bull += 10 if rsi > 55 else 0
    bear += 10 if rsi < 45 else 0
    bull += 10 if sum(plus_dm[-14:]) > sum(minus_dm[-14:]) else 0
    bear += 10 if sum(minus_dm[-14:]) > sum(plus_dm[-14:]) else 0
    bull += 10 if rel_vol >= 1.2 and closes[-1] > closes[-2] else 0
    bear += 10 if rel_vol >= 1.2 and closes[-1] < closes[-2] else 0
    recent_high, recent_low = max(highs[-12:-1]), min(lows[-12:-1])
    bull += 20 if closes[-1] > recent_high else 0
    bear += 20 if closes[-1] < recent_low else 0
    # Sideways hard filter: weak EMA separation or repeated overlap suppresses actionable signals.
    separation = abs(e9[-1]-e21[-1]) / closes[-1] * 100 if closes[-1] else 0
    sideways = separation < 0.025 and abs(closes[-1]-vwap)/closes[-1]*100 < 0.08
    score = max(bull, bear)
    decision = "WAIT"
    if not sideways and bull >= 75 and bull > bear:
        decision = "BULLISH BIAS — CE SETUP ONLY AFTER RETEST"
    elif not sideways and bear >= 75 and bear > bull:
        decision = "BEARISH BIAS — PE SETUP ONLY AFTER RETEST"
    return {
        "status": "ok", "symbol": symbol, "interval": interval,
        "decision": decision, "score": score, "bull_score": bull, "bear_score": bear,
        "sideways": sideways, "rsi": round(rsi, 2), "ema9": round(e9[-1], 2),
        "ema21": round(e21[-1], 2), "vwap_approx": round(vwap, 2),
        "atr": round(atr, 2), "relative_volume": round(rel_vol, 2),
        "last_close": closes[-1], "breakout_high": recent_high, "breakdown_low": recent_low,
        "note": "Bias only, not a confirmed entry. Breakout + retest and option-premium risk checks are required."
    }

@app.get("/health")
async def health():
    return {"ok": True}
