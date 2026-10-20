"""Pure calculation helpers for the "Data Check -> Signals" dashboard (no web-framework imports).

Everything here takes plain dicts / lists and returns plain dicts, so it can be unit-tested without
Upstox access. main.py does the fetching; this module does the maths and the signal rules.

Signal vocabulary (same everywhere): BULLISH, BEARISH, NEUTRAL, CAUTION, UNAVAILABLE.
All thresholds live in THRESHOLDS so you can tune them in one place.
"""
import csv
import io
import re
from datetime import timedelta

from indicators import IST, ema

THRESHOLDS = {
    "ad_bull": 1.5,        # advance/decline ratio at or above this -> BULLISH
    "ad_bear": 0.67,       # at or below this -> BEARISH
    "sector_share": 2 / 3,  # share of sectors that must be green (or red) for BULLISH (or BEARISH)
    "premium_move": 2.0,   # % change of ATM option premium treated as a real move
    "premium_gap": 3.0,    # CE% minus PE% gap needed for a directional call
    "zone_up": 3,          # zone score >= this -> UP
    "zone_down": -3,       # zone score <= this -> DOWN
}

# Major sector indices. Aliases are matched (normalised) against Upstox's index names / trading symbols,
# so a small naming difference ("Nifty Fin Service" vs "Nifty Financial Services") still resolves.
SECTORS = [
    ("Bank", ["niftybank"]),
    ("Fin Services", ["niftyfinservice", "niftyfinancialservices"]),
    ("Pvt Bank", ["niftypvtbank", "niftyprivatebank"]),
    ("PSU Bank", ["niftypsubank"]),
    ("IT", ["niftyit"]),
    ("Auto", ["niftyauto"]),
    ("FMCG", ["niftyfmcg"]),
    ("Pharma", ["niftypharma"]),
    ("Metal", ["niftymetal"]),
    ("Energy", ["niftyenergy"]),
    ("Oil & Gas", ["niftyoilgas", "niftyoilandgas"]),
    ("Realty", ["niftyrealty"]),
]
# Used only if the instrument file could not be downloaded (a wrong guess simply shows "unavailable").
SECTOR_FALLBACK_KEYS = {
    "Bank": "NSE_INDEX|Nifty Bank", "Fin Services": "NSE_INDEX|Nifty Fin Service",
    "Pvt Bank": "NSE_INDEX|Nifty Pvt Bank", "PSU Bank": "NSE_INDEX|Nifty PSU Bank",
    "IT": "NSE_INDEX|Nifty IT", "Auto": "NSE_INDEX|Nifty Auto", "FMCG": "NSE_INDEX|Nifty FMCG",
    "Pharma": "NSE_INDEX|Nifty Pharma", "Metal": "NSE_INDEX|Nifty Metal", "Energy": "NSE_INDEX|Nifty Energy",
    "Oil & Gas": "NSE_INDEX|Nifty Oil and Gas", "Realty": "NSE_INDEX|Nifty Realty",
}

# Offline fallback for the Nifty 50 list (used only when NSE's constituent CSV cannot be downloaded AND the
# instrument file is available). It can be out of date after an index rebalance - the live CSV is preferred.
FALLBACK_NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
    "BEL", "BHARTIARTL", "BSE", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH",
    "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN",
    "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID",
    "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM",
    "TITAN", "TMPV", "TRENT", "ULTRACEMCO",
]

UNIVERSES = {
    "nifty50": {"label": "Nifty 50", "file": "ind_nifty50list.csv"},
    "nifty100": {"label": "Nifty 100", "file": "ind_nifty100list.csv"},
    "nifty500": {"label": "Nifty 500", "file": "ind_nifty500list.csv"},
    "niftybank": {"label": "Nifty Bank", "file": "ind_niftybanklist.csv"},
}


# ---------------------------------------------------------------- small helpers
def num(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def norm_name(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def signal(state, label, detail=""):
    return {"state": state, "label": label, "detail": detail}


def unavailable(reason):
    return {"status": "UNAVAILABLE", "reason": str(reason)[:300],
            "signal": signal("UNAVAILABLE", "UNAVAILABLE", str(reason)[:200])}


# ---------------------------------------------------------------- constituents / keys
def parse_constituents_csv(text):
    """NSE index-constituent CSV -> [{symbol, name, industry, isin, key}]. Only EQ series with a valid ISIN."""
    out, seen = [], set()
    reader = csv.DictReader(io.StringIO((text or "").lstrip("\ufeff")))
    for raw in reader:
        row = {norm_name(k): (v or "").strip() for k, v in raw.items() if k}
        symbol, isin = row.get("symbol", ""), row.get("isincode", "")
        series = row.get("series", "EQ") or "EQ"
        if not symbol or not isin.startswith("IN") or series.upper() != "EQ" or isin in seen:
            continue
        seen.add(isin)
        out.append({"symbol": symbol, "name": row.get("companyname", symbol), "industry": row.get("industry", ""),
                    "isin": isin, "key": f"NSE_EQ|{isin}"})
    return out


def resolve_sector_keys(index_rows):
    """Map sector name -> instrument key using the instrument file's NSE_INDEX rows.
    Returns ({sector: key}, [sectors not found])."""
    by_norm = {}
    for r in index_rows or []:
        if r.get("segment") not in (None, "NSE_INDEX") or not r.get("instrument_key"):
            continue
        for field in ("name", "trading_symbol"):
            by_norm.setdefault(norm_name(r.get(field)), r["instrument_key"])
    found, missing = {}, []
    for name, aliases in SECTORS:
        key = next((by_norm[a] for a in aliases if a in by_norm), None)
        if key:
            found[name] = key
        else:
            missing.append(name)
    return found, missing


# ---------------------------------------------------------------- quotes
def quote_change(q):
    """Upstox quote dict -> {ltp, prev_close, change, pct} or None.
    Previous close is taken from net_change when the feed provides it, otherwise from ohlc.close."""
    if not isinstance(q, dict):
        return None
    ltp = num(q.get("last_price", q.get("ltp")))
    if ltp is None or ltp <= 0:
        return None
    net = num(q.get("net_change"))
    ohlc_close = num((q.get("ohlc") or {}).get("close"))
    prev = None
    if net is not None:
        prev = ltp - net
        # net_change of exactly 0 while the quote's close differs usually means "not populated"
        if net == 0 and ohlc_close and abs(ltp - ohlc_close) / ohlc_close > 0.0005:
            prev = ohlc_close
    if prev is None or prev <= 0:
        prev = ohlc_close
    if prev is None or prev <= 0:
        return None
    return {"ltp": ltp, "prev_close": prev, "change": ltp - prev, "pct": (ltp - prev) / prev * 100.0}


def quotes_by_key(response):
    """Upstox quotes response -> {instrument_key: quote_change dict}. The response is keyed by
    'EXCHANGE:SYMBOL'; the instrument_key is in each row's instrument_token."""
    out = {}
    data = (response or {}).get("data") or {}
    for k, q in data.items():
        if not isinstance(q, dict):
            continue
        key = q.get("instrument_token") or k
        qc = quote_change(q)
        if qc:
            out[key] = qc
            out[k] = qc  # also reachable by the EXCHANGE:SYMBOL form
    return out


# ---------------------------------------------------------------- 1. advance / decline
def breadth_signal(ratio, adv, dec):
    t = THRESHOLDS
    if ratio is None:
        return signal("UNAVAILABLE", "UNAVAILABLE", "No quotes")
    if ratio >= t["ad_bull"]:
        return signal("BULLISH", "BULLISH", f"{adv} advancing vs {dec} declining (ratio ≥ {t['ad_bull']})")
    if ratio <= t["ad_bear"]:
        return signal("BEARISH", "BEARISH", f"{dec} declining vs {adv} advancing (ratio ≤ {t['ad_bear']})")
    return signal("NEUTRAL", "NEUTRAL", "Mixed breadth — no clear majority")


def advance_decline(constituents, quotes, universe_label, universe_source=""):
    rows, adv, dec, unch, missing = [], 0, 0, 0, 0
    for c in constituents:
        q = quotes.get(c["key"])
        if not q:
            missing += 1
            continue
        pct = q["pct"]
        if pct > 0:
            adv += 1
        elif pct < 0:
            dec += 1
        else:
            unch += 1
        rows.append({"symbol": c["symbol"], "pct": round(pct, 2), "ltp": q["ltp"]})
    total = adv + dec + unch
    if total == 0:
        return unavailable("No stock quotes returned for " + universe_label)
    ratio = (adv / dec) if dec else None
    ratio_for_signal = ratio if ratio is not None else (float("inf") if adv else None)
    rows.sort(key=lambda r: r["pct"], reverse=True)
    return {
        "status": "OK", "universe": universe_label, "universe_source": universe_source,
        "advances": adv, "declines": dec, "unchanged": unch, "quoted": total, "missing": missing,
        "ratio": round(ratio, 2) if ratio is not None else None, "ratio_text": f"{ratio:.2f}" if ratio is not None else ("∞" if adv else "—"),
        "pct_advancing": round(adv / total * 100, 1),
        "gainers": rows[:5], "losers": rows[::-1][:5],
        "signal": breadth_signal(ratio_for_signal, adv, dec),
    }


# ---------------------------------------------------------------- 2. sector heat map
def sector_heatmap(sector_keys, quotes, unresolved=()):
    tiles = []
    for name, _ in SECTORS:
        q = quotes.get(sector_keys.get(name)) if sector_keys.get(name) else None
        tiles.append({"name": name, "key": sector_keys.get(name), "pct": round(q["pct"], 2) if q else None,
                      "ltp": q["ltp"] if q else None})
    live = [t for t in tiles if t["pct"] is not None]
    if len(live) < 4:
        res = unavailable(f"Only {len(live)} sector index quotes returned")
        res["tiles"] = tiles
        return res
    green = sum(1 for t in live if t["pct"] > 0.05)
    red = sum(1 for t in live if t["pct"] < -0.05)
    need = THRESHOLDS["sector_share"]
    avg = sum(t["pct"] for t in live) / len(live)
    leaders = sorted(live, key=lambda t: t["pct"], reverse=True)
    if green / len(live) >= need:
        sig = signal("BULLISH", "BULLISH", f"{green}/{len(live)} sectors green · avg {avg:+.2f}%")
    elif red / len(live) >= need:
        sig = signal("BEARISH", "BEARISH", f"{red}/{len(live)} sectors red · avg {avg:+.2f}%")
    else:
        sig = signal("NEUTRAL", "NEUTRAL", f"Mixed rotation: {green} green / {red} red · avg {avg:+.2f}%")
    return {"status": "OK", "tiles": tiles, "green": green, "red": red, "count": len(live), "avg_pct": round(avg, 2),
            "leader": leaders[0]["name"], "laggard": leaders[-1]["name"], "unresolved": list(unresolved), "signal": sig}


# ---------------------------------------------------------------- 3. option premium % change
def _pct(ltp, close):
    ltp, close = num(ltp), num(close)
    if ltp is None or close is None or close <= 0 or ltp <= 0:
        return None
    return (ltp - close) / close * 100.0


def premium_signal(ce_pct, pe_pct):
    t = THRESHOLDS
    if ce_pct is None or pe_pct is None:
        return signal("UNAVAILABLE", "UNAVAILABLE", "ATM premium change unavailable")
    mv, gap = t["premium_move"], t["premium_gap"]
    if ce_pct >= 0 and pe_pct <= 0 and ce_pct - pe_pct >= gap:
        return signal("BULLISH", "BULLISH", f"Calls {ce_pct:+.1f}% while puts {pe_pct:+.1f}% — buyers favour CE")
    if pe_pct >= 0 and ce_pct <= 0 and pe_pct - ce_pct >= gap:
        return signal("BEARISH", "BEARISH", f"Puts {pe_pct:+.1f}% while calls {ce_pct:+.1f}% — buyers favour PE")
    if ce_pct >= mv and pe_pct >= mv:
        return signal("CAUTION", "VOLATILITY UP", f"Both premiums rising (CE {ce_pct:+.1f}%, PE {pe_pct:+.1f}%) — IV spike, no direction")
    if ce_pct <= -mv and pe_pct <= -mv:
        return signal("CAUTION", "PREMIUM DECAY", f"Both premiums falling (CE {ce_pct:+.1f}%, PE {pe_pct:+.1f}%) — theta/IV crush, avoid buying")
    return signal("NEUTRAL", "NEUTRAL", f"CE {ce_pct:+.1f}% · PE {pe_pct:+.1f}% — no decisive move")


def build_option_premium(chain_rows, spot, live=None, steps=2):
    """chain_rows: Upstox option-chain rows. live: {instrument_key: (ltp, close_price)} for FRESH ticks only.
    Returns ATM CE/PE premium % change, a +/- `steps` strike table and the signal."""
    live = live or {}
    rows = [r for r in chain_rows or [] if num(r.get("strike_price")) is not None]
    rows.sort(key=lambda r: float(r["strike_price"]))
    if not rows:
        return unavailable("Option chain returned no rows")
    spot = num(spot) or num(rows[0].get("underlying_spot_price"))
    if not spot:
        return unavailable("Underlying spot price missing from option chain")
    atm_i = min(range(len(rows)), key=lambda i: abs(float(rows[i]["strike_price"]) - spot))

    def leg(opt):
        opt = opt or {}
        md = opt.get("market_data") or {}
        key = opt.get("instrument_key")
        ltp, close = num(md.get("ltp")), num(md.get("close_price"))
        src = "REST snapshot"
        if key in live and live[key][0]:
            ltp = num(live[key][0])
            close = num(live[key][1]) or close
            src = "WebSocket tick"
        return {"key": key, "ltp": ltp, "prev_close": close, "pct": _pct(ltp, close), "source": src,
                "volume": num(md.get("volume")), "oi": num(md.get("oi"))}

    table = []
    for i in range(max(0, atm_i - steps), min(len(rows), atm_i + steps + 1)):
        r = rows[i]
        table.append({"strike": float(r["strike_price"]), "offset": i - atm_i, "atm": i == atm_i,
                      "ce": leg(r.get("call_options")), "pe": leg(r.get("put_options"))})
    atm = next(t for t in table if t["atm"])

    def avg(side):
        vals = [t[side]["pct"] for t in table if abs(t["offset"]) <= 1 and t[side]["pct"] is not None]
        return sum(vals) / len(vals) if vals else None

    ce_avg, pe_avg = avg("ce"), avg("pe")
    # Direction uses the ATM contracts (the ones traders actually buy); the 3-strike average is shown as a cross-check.
    sig = premium_signal(atm["ce"]["pct"], atm["pe"]["pct"])
    return {"status": "OK", "spot": spot, "atm_strike": atm["strike"], "table": table,
            "ce_pct": atm["ce"]["pct"], "pe_pct": atm["pe"]["pct"], "ce_ltp": atm["ce"]["ltp"], "pe_ltp": atm["pe"]["ltp"],
            "ce_avg3_pct": ce_avg, "pe_avg3_pct": pe_avg, "signal": sig}


# ---------------------------------------------------------------- 4. option premium graph (CE vs PE)
def leg_state(closes):
    """RISING / FALLING / FLAT from the last close vs its 9-EMA and vs 3 bars ago."""
    if len(closes) < 10:
        return None
    e9, last, ref = ema(closes, 9)[-1], closes[-1], closes[-4]
    if last > e9 and last > ref:
        return "RISING"
    if last < e9 and last < ref:
        return "FALLING"
    return "FLAT"


def graph_signal(ce_state, pe_state):
    if ce_state is None or pe_state is None:
        return signal("UNAVAILABLE", "UNAVAILABLE", "Not enough premium candles yet (need 10+ bars each)")
    pair = (ce_state, pe_state)
    if pair == ("RISING", "FALLING"):
        return signal("BULLISH", "BULLISH", "CE premium rising, PE premium falling")
    if pair == ("FALLING", "RISING"):
        return signal("BEARISH", "BEARISH", "PE premium rising, CE premium falling")
    if pair == ("RISING", "RISING"):
        return signal("CAUTION", "VOLATILITY UP", "Both premiums rising — IV expansion, direction unclear")
    if pair == ("FALLING", "FALLING"):
        return signal("CAUTION", "PREMIUM DECAY", "Both premiums falling — time decay / IV crush")
    if ce_state == "RISING":
        return signal("BULLISH", "LEAN BULLISH", f"CE rising, PE {pe_state.lower()}")
    if pe_state == "RISING":
        return signal("BEARISH", "LEAN BEARISH", f"PE rising, CE {ce_state.lower()}")
    return signal("NEUTRAL", "NEUTRAL", f"CE {ce_state.lower()} · PE {pe_state.lower()}")


def _latest_session(cs):
    if not cs:
        return []
    day = cs[-1]["_dt"].astimezone(IST).date()
    return [c for c in cs if c["_dt"].astimezone(IST).date() == day]


def build_premium_graph(ce_candles, pe_candles, ce_info, pe_info):
    """ce_candles / pe_candles: normalized candles (with _dt). Returns chart series for the latest session."""
    ce, pe = _latest_session(ce_candles), _latest_session(pe_candles)
    if not ce and not pe:
        return unavailable("No option premium candles returned (contract may be illiquid or the feed is closed)")

    def series(cs):
        return [{"time": int(c["_dt"].timestamp()), "open": c["open"], "high": c["high"], "low": c["low"],
                 "close": c["close"], "volume": c["volume"]} for c in cs]

    ce_s, pe_s = series(ce), series(pe)
    ce_state = leg_state([c["close"] for c in ce]) if ce else None
    pe_state = leg_state([c["close"] for c in pe]) if pe else None

    def day_change(cs):
        if not cs or cs[0]["open"] <= 0:
            return None
        return (cs[-1]["close"] - cs[0]["open"]) / cs[0]["open"] * 100.0

    sig = graph_signal(ce_state, pe_state)
    return {"status": "OK", "ce": {**ce_info, "candles": ce_s, "state": ce_state, "from_open_pct": day_change(ce)},
            "pe": {**pe_info, "candles": pe_s, "state": pe_state, "from_open_pct": day_change(pe)},
            "session": (ce or pe)[-1]["_dt"].astimezone(IST).strftime("%Y-%m-%d"), "signal": sig}


# ---------------------------------------------------------------- 5. index price zone (Up / Mid / Down)
def _minute_of_day(dt):
    d = dt.astimezone(IST)
    return d.hour * 60 + d.minute


def index_zone(candles, last_price=None, label="Nifty"):
    """Classify the index as UP / MID / DOWN from five transparent checks (+1 / 0 / -1 each):
    vs opening range (first 15 min), vs session average price, vs previous close, vs day open, vs previous-day H/L.
    Score >= +3 -> UP, <= -3 -> DOWN, otherwise MID."""
    if not candles:
        return unavailable("No index candles")
    days = []
    for c in candles:
        d = c["_dt"].astimezone(IST).date()
        if not days or days[-1][0] != d:
            days.append((d, []))
        days[-1][1].append(c)
    today_d, today = days[-1]
    prev = days[-2][1] if len(days) >= 2 else []
    if not prev:
        return unavailable("Previous session candles missing (cannot compute previous close / high / low)")
    price = num(last_price) or today[-1]["close"]
    day_open = today[0]["open"]
    prev_close = prev[-1]["close"]
    pdh, pdl = max(c["high"] for c in prev), min(c["low"] for c in prev)
    or_bars = [c for c in today if 555 <= _minute_of_day(c["_dt"]) < 570] or today[:1]
    orh, orl = max(c["high"] for c in or_bars), min(c["low"] for c in or_bars)
    vol = sum(c["volume"] for c in today)
    if vol > 0:
        avg_price = sum(((c["high"] + c["low"] + c["close"]) / 3) * c["volume"] for c in today) / vol
        avg_basis = "VWAP"
    else:
        avg_price = sum((c["high"] + c["low"] + c["close"]) / 3 for c in today) / len(today)
        avg_basis = "Session avg price"
    day_high, day_low = max(c["high"] for c in today), min(c["low"] for c in today)
    day_high, day_low = max(day_high, price), min(day_low, price)

    def tri(cond_up, cond_down):
        return 1 if cond_up else -1 if cond_down else 0

    checks = [
        {"label": "vs opening range (09:15–09:30)", "score": tri(price > orh, price < orl),
         "detail": f"Range {orl:,.2f} – {orh:,.2f}"},
        {"label": f"vs {avg_basis}", "score": tri(price > avg_price, price < avg_price), "detail": f"{avg_price:,.2f}"},
        {"label": "vs previous close", "score": tri(price > prev_close, price < prev_close), "detail": f"{prev_close:,.2f}"},
        {"label": "vs today's open", "score": tri(price > day_open, price < day_open), "detail": f"{day_open:,.2f}"},
        {"label": "vs previous-day high / low", "score": tri(price > pdh, price < pdl),
         "detail": f"PDL {pdl:,.2f} · PDH {pdh:,.2f}"},
    ]
    score = sum(c["score"] for c in checks)
    t = THRESHOLDS
    zone = "UP" if score >= t["zone_up"] else "DOWN" if score <= t["zone_down"] else "MID"
    state = {"UP": "BULLISH", "DOWN": "BEARISH", "MID": "NEUTRAL"}[zone]
    detail = {"UP": f"{label} trading above its reference levels (score {score:+d}/5)",
              "DOWN": f"{label} trading below its reference levels (score {score:+d}/5)",
              "MID": f"{label} inside its reference band — range-bound / undecided (score {score:+d}/5)"}[zone]
    span = day_high - day_low
    return {"status": "OK", "zone": zone, "score": score, "price": price, "session": str(today_d),
            "change_pct": (price - prev_close) / prev_close * 100.0 if prev_close else None,
            "day_range_pos": ((price - day_low) / span * 100.0) if span > 0 else 50.0,
            "levels": {"day_open": day_open, "prev_close": prev_close, "pdh": pdh, "pdl": pdl, "orh": orh, "orl": orl,
                       "avg_price": avg_price, "avg_basis": avg_basis, "day_high": day_high, "day_low": day_low},
            "checks": checks, "signal": signal(state, zone, detail)}


# ---------------------------------------------------------------- overall confluence
def overall_bias(signals):
    """signals: {row_key: signal dict}. Counts only rows that produced a real reading."""
    live = {k: s for k, s in signals.items() if s and s.get("state") != "UNAVAILABLE"}
    bull = sum(1 for s in live.values() if s["state"] == "BULLISH")
    bear = sum(1 for s in live.values() if s["state"] == "BEARISH")
    caution = sum(1 for s in live.values() if s["state"] == "CAUTION")
    neutral = len(live) - bull - bear - caution
    n = len(live)
    if n < 3:
        return {"state": "UNAVAILABLE", "label": "NOT ENOUGH DATA", "bull": bull, "bear": bear, "neutral": neutral,
                "caution": caution, "count": n, "detail": f"Only {n} of {len(signals)} checks have data"}
    need = max(3, (n // 2) + 1)  # a clear majority, never fewer than 3
    if bull >= need and bear <= 1:
        state, label, detail = "BULLISH", "BULLISH BIAS · favour CE", f"{bull} of {n} checks bullish"
    elif bear >= need and bull <= 1:
        state, label, detail = "BEARISH", "BEARISH BIAS · favour PE", f"{bear} of {n} checks bearish"
    else:
        state, label, detail = "NEUTRAL", "MIXED · WAIT", f"{bull} bullish · {bear} bearish · {neutral + caution} neutral/caution — no clear alignment"
    if caution and state != "NEUTRAL":
        detail += f" (note: {caution} caution flag{'s' if caution > 1 else ''} — premium decay / IV spike)"
    return {"state": state, "label": label, "bull": bull, "bear": bear, "neutral": neutral, "caution": caution,
            "count": n, "detail": detail}


# ---------------------------------------------------------------- final signal + interpretation
WEIGHTS = {"zone": 30, "premium": 25, "graph": 15, "breadth": 15, "sectors": 15}
CHECK_NAMES = {"zone": "Index price zone", "premium": "Option premium % change", "graph": "Option premium graph",
               "breadth": "Advance/decline", "sectors": "Sector heat map"}
_DIR = {"BULLISH": 1, "BEARISH": -1}


def final_signal(blocks, market_open=True):
    """Weighted vote of the five checks -> action + plain-language interpretation.
    blocks: {"zone","premium","graph","breadth","sectors"} -> block dicts (each with a 'signal').
    Rules: net score = weighted direction of the checks that have data (-100..+100).
    |net| < 25 -> WAIT.  Otherwise the side is the sign of the score; it is CONFIRMED only when |net| >= 55, nothing
    disagrees and the index price zone agrees; BIAS when |net| >= 40 with at most one disagreeing check; else WATCH.
    A price zone that opposes the side caps it at WATCH; a premium-decay flag caps CONFIRMED at BIAS."""
    sigs = {k: (blocks.get(k) or {}).get("signal") or signal("UNAVAILABLE", "UNAVAILABLE") for k in WEIGHTS}
    avail = [k for k in WEIGHTS if sigs[k]["state"] != "UNAVAILABLE"]
    rows = []
    for k in WEIGHTS:
        d = _DIR.get(sigs[k]["state"], 0)
        have = k in avail
        rows.append({"key": k, "name": CHECK_NAMES[k], "weight": WEIGHTS[k], "state": sigs[k]["state"], "label": sigs[k]["label"],
                     "dir": d, "points": WEIGHTS[k] * d if have else None, "detail": sigs[k].get("detail", "")})
    if len(avail) < 3:
        return {"action": "NO SIGNAL", "state": "UNAVAILABLE", "level": "INSUFFICIENT DATA", "score": None, "side": None,
                "headline": f"Only {len(avail)} of 5 checks have data — no call can be made.",
                "reasons": [], "against": [], "cautions": ["Check the Upstox token / market hours; rows marked UNAVAILABLE show the reason."],
                "plan": [], "invalidation": [], "contributions": rows,
                "disclaimer": "Rule-based read of market data; not a trade instruction."}
    total_w = sum(WEIGHTS[k] for k in avail)
    net = sum(WEIGHTS[k] * _DIR.get(sigs[k]["state"], 0) for k in avail) / total_w * 100.0
    side = "CE" if net > 0 else "PE" if net < 0 else None
    want = 1 if side == "CE" else -1
    agree = [k for k in avail if _DIR.get(sigs[k]["state"], 0) == want] if side else []
    oppose = [k for k in avail if side and _DIR.get(sigs[k]["state"], 0) == -want]
    quiet = [k for k in avail if k not in agree and k not in oppose]
    zone_state = sigs["zone"]["state"]
    decay = any(sigs[k]["label"] == "PREMIUM DECAY" for k in ("premium", "graph"))
    iv_up = any(sigs[k]["label"] == "VOLATILITY UP" for k in ("premium", "graph"))
    zone_agrees = side is not None and _DIR.get(zone_state, 0) == want
    zone_opposes = side is not None and _DIR.get(zone_state, 0) == -want

    if side is None or abs(net) < 25:
        level, action, state = "NO EDGE", "WAIT", "NEUTRAL"
    else:
        if abs(net) >= 55 and not oppose and zone_agrees:
            level = "CONFIRMED"
        elif abs(net) >= 40 and len(oppose) <= 1:
            level = "BIAS"
        else:
            level = "WATCH"
        if zone_opposes:
            level = "WATCH"
        if decay and level == "CONFIRMED":
            level = "BIAS"
        state = "BULLISH" if side == "CE" else "BEARISH"
        action = {"CONFIRMED": f"BUY {side} SETUP", "BIAS": f"{side} BIAS — wait for trigger", "WATCH": f"WATCH {side} — not aligned yet"}[level]

    n = len(avail)
    names = lambda ks: ", ".join(CHECK_NAMES[k] for k in ks)
    if level == "NO EDGE":
        headline = f"No edge: the {n} available checks cancel out (score {net:+.0f}/100). Stand aside until they line up."
    elif level == "CONFIRMED":
        headline = f"{side} setup is confirmed: {len(agree)} of {n} checks agree, none disagree, and price action is on the same side (score {net:+.0f}/100)."
    elif level == "BIAS":
        headline = f"Leaning {side}: {len(agree)} of {n} checks agree" + (f", but {names(oppose)} disagrees" if oppose else "") + f" (score {net:+.0f}/100). A bias, not an entry."
    else:
        why = "price action disagrees" if zone_opposes else "too few checks agree"
        headline = f"Early {side} lean only: {why} (score {net:+.0f}/100). Watch, don't act."

    reasons = [f"{CHECK_NAMES[k]}: {sigs[k]['detail']}" for k in agree]
    against = [f"{CHECK_NAMES[k]}: {sigs[k]['detail']}" for k in oppose]
    neutral_notes = [f"{CHECK_NAMES[k]}: {sigs[k]['detail']}" for k in quiet]
    cautions = []
    if decay:
        cautions.append("Option premiums are decaying on both sides — buying options loses to time/IV even if direction is right. Prefer waiting for a premium expansion.")
    if iv_up:
        cautions.append("Premiums are rising on both sides (IV spike): options are expensive, so the same index move pays less. Use smaller size or wait for IV to settle.")
    if not market_open:
        cautions.append("Market is closed — this reflects the last session and is not actionable.")
    missing = [CHECK_NAMES[k] for k in WEIGHTS if k not in avail]
    if missing:
        cautions.append("No data for: " + ", ".join(missing) + " — the score uses the remaining checks only.")
    cautions.extend(neutral_notes[:2] if level != "NO EDGE" else neutral_notes)

    zone = blocks.get("zone") or {}
    L = zone.get("levels") or {}
    plan, invalidation = [], []
    if side == "CE" and L:
        if level == "CONFIRMED":
            plan.append(f"Enter CE only on a pullback that holds above {L['avg_price']:,.2f} ({L['avg_basis']}) or the opening-range high {L['orh']:,.2f} — don't chase a vertical move.")
        else:
            plan.append(f"Wait for price to hold above {L['orh']:,.2f} (opening-range high) and above {L['avg_price']:,.2f} ({L['avg_basis']}) before considering a CE.")
        plan.append(f"Upside reference levels: day high {L['day_high']:,.2f}, then previous-day high {L['pdh']:,.2f}.")
        invalidation.append(f"Index closes back below {L['avg_price']:,.2f} ({L['avg_basis']}) or below the day open {L['day_open']:,.2f}.")
    elif side == "PE" and L:
        if level == "CONFIRMED":
            plan.append(f"Enter PE only on a bounce that fails below {L['avg_price']:,.2f} ({L['avg_basis']}) or the opening-range low {L['orl']:,.2f} — don't chase a vertical drop.")
        else:
            plan.append(f"Wait for price to hold below {L['orl']:,.2f} (opening-range low) and below {L['avg_price']:,.2f} ({L['avg_basis']}) before considering a PE.")
        plan.append(f"Downside reference levels: day low {L['day_low']:,.2f}, then previous-day low {L['pdl']:,.2f}.")
        invalidation.append(f"Index closes back above {L['avg_price']:,.2f} ({L['avg_basis']}) or above the day open {L['day_open']:,.2f}.")
    elif level == "NO EDGE":
        plan.append("Do nothing until at least three checks, including the index price zone, point the same way.")
        if L:
            plan.append(f"Watch the opening-range break: above {L['orh']:,.2f} favours CE, below {L['orl']:,.2f} favours PE.")
    if side:
        invalidation.append(f"Signal weakens if the option-premium checks flip against {side}, or if the {CHECK_NAMES['zone'].lower()} moves back to MID.")
        if "PE" == side:
            invalidation.append("Breadth turning positive (ratio ≥ 1.5) while price holds is a warning that the sell-off is losing strength.")
        else:
            invalidation.append("Breadth turning negative (ratio ≤ 0.67) while price holds is a warning that the rally is losing strength.")
        plan.append("Set the stop-loss and position size before entering; use the main terminal's confirmation gates and paper trading to validate.")
    return {"action": action, "state": state, "level": level, "score": round(net, 1), "side": side,
            "agree": len(agree), "oppose": len(oppose), "available": n,
            "headline": headline, "reasons": reasons, "against": against, "cautions": cautions,
            "plan": plan, "invalidation": invalidation, "contributions": rows,
            "disclaimer": "Rule-based read of market data; not a trade instruction. Confirmation lowers false signals but cannot remove losses."}
