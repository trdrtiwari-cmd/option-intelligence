"""Option leg of a signal (v1.19). Pure functions (no web-framework imports) so they can be unit-tested.

When the terminal sees a WATCH CE / WATCH PE it moves to the contract TWO STRIKES AWAY FROM THE ATM and follows that contract:

    CE  ->  ATM + 2 strikes   (two strikes above the ATM)
    PE  ->  ATM - 2 strikes   (two strikes below the ATM: the same "two out" for a put)

OPTION_STRIKE_OFFSET (default 2) and OPTION_STRIKE_SIDE (OTM default, or ITM) change this without a redeploy.
A BUY is only created when the signal is CONFIRMED (every index gate + the 60 s reconfirmation hold) AND this profit check passes:

    1. the contract has a usable premium (>= MIN_PREMIUM) and a sane spread
    2. the contract's OWN premium chart is trending up (buyers win a CE or a PE only when the premium rises)
    3. there is room to a target: the nearest swing / round-number level in the trade direction is far enough away
    4. reward : risk after costs >= MIN_RR  (target and stop are measured on the index, translated into premium with the option's delta)

The target is not fixed: it is the normal ATR target (3 ATR) limited by the nearest resistance (CE) / support (PE), never below MIN_RR x stop.
"""
import os

from indicators import normalize_candles


def _envf(name, default):
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return float(default)


OFFSET = int(_envf("OPTION_STRIKE_OFFSET", 2))
SIDE = os.getenv("OPTION_STRIKE_SIDE", "OTM").strip().upper()          # OTM: CE above / PE below the ATM; ITM mirrors it
MIN_RR = _envf("OPTION_MIN_RR", 1.3)
SWING_SPAN = int(_envf("OPTION_SWING_SPAN", 3))      # a swing high/low must be the extreme of 2 x span + 1 bars (3 -> 7 candles): micro wiggles are not walls
MAX_SPREAD_PCT = _envf("OPTION_MAX_SPREAD_PCT", 4.0)
MIN_PREMIUM = _envf("OPTION_MIN_PREMIUM", 5.0)
ROOM_BUFFER_ATR = 0.25          # stay this far short of the wall
ROUND_STEP = {"NIFTY": 100.0, "BANKNIFTY": 500.0}


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _sorted_rows(rows):
    rows = [r for r in rows or [] if _num(r.get("strike_price")) is not None]
    rows.sort(key=lambda r: float(r["strike_price"]))
    return rows


def _leg(rows, i, atm_i, side):
    row = rows[i]
    leg = (row.get("call_options") if side == "CE" else row.get("put_options")) or {}
    key = leg.get("instrument_key")
    if not key:
        return None
    md, gk = leg.get("market_data") or {}, leg.get("option_greeks") or {}
    strikes = [float(r["strike_price"]) for r in rows]
    step = min((b - a for a, b in zip(strikes, strikes[1:]) if b > a), default=None)
    return {"instrument_key": key, "type": side, "strike": float(row["strike_price"]), "expiry": row.get("expiry"),
            "atm_strike": float(rows[atm_i]["strike_price"]), "offset_steps": i - atm_i, "step": step,
            "ltp": _num(md.get("ltp")), "close": _num(md.get("close_price")), "bid": _num(md.get("bid_price")), "ask": _num(md.get("ask_price")),
            "volume": _num(md.get("volume")), "oi": _num(md.get("oi")), "delta": _num(gk.get("delta")), "iv": _num(gk.get("iv"))}


def pick_strike(rows, spot, side, offset=None, where=None):
    """The contract `offset` strikes from the ATM. rows = Upstox option-chain rows. Returns a dict or None."""
    offset = OFFSET if offset is None else int(offset)
    where = (where or SIDE).upper()
    rows = _sorted_rows(rows)
    spot = _num(spot)
    if not rows or not spot or side not in ("CE", "PE"):
        return None
    atm_i = min(range(len(rows)), key=lambda i: abs(float(rows[i]["strike_price"]) - spot))
    up = (side == "CE") == (where != "ITM")             # OTM CE and ITM PE sit above the ATM
    i = max(0, min(len(rows) - 1, atm_i + (offset if up else -offset)))
    return _leg(rows, i, atm_i, side)


def pick_by_strike(rows, spot, side, strike):
    """The contract at an exact strike (used to keep following the contract the chart was switched to). None if it left the chain."""
    rows = _sorted_rows(rows)
    spot, strike = _num(spot), _num(strike)
    if not rows or not spot or strike is None or side not in ("CE", "PE"):
        return None
    atm_i = min(range(len(rows)), key=lambda i: abs(float(rows[i]["strike_price"]) - spot))
    for i, r in enumerate(rows):
        if float(r["strike_price"]) == strike:
            return _leg(rows, i, atm_i, side)
    return None


class OptionLock:
    """The terminal moves the chart to the CE/PE two strikes from the ATM when a WATCH / CONFIRMED setup appears. Index price wiggles
    would otherwise hop the choice between strikes every few seconds (and reset the premium chart, the profit check and the log entry),
    so the first pick is LOCKED for the life of the setup. It is released when the side flips, the setup has been gone for
    `release_seconds`, or price has run so far that the locked strike is no longer 1..OFFSET+2 strikes on the intended side of the ATM (price ran past it, or away from it)."""

    def __init__(self, release_seconds=300):
        self.state = {}
        self.release_seconds = release_seconds

    def idle(self, sym, now):
        e = self.state.get(sym)
        if e and now - e["last_active"] > self.release_seconds:
            self.state.pop(sym, None)

    def pick(self, sym, rows, spot, side, now):
        fresh = pick_strike(rows, spot, side)
        if not fresh:
            return None
        e = self.state.get(sym)
        if e and e["side"] == side and now - e["last_active"] <= self.release_seconds:
            keep = pick_by_strike(rows, spot, side, e["strike"])
            outward = 1 if (side == "CE") == (SIDE != "ITM") else -1          # CE above / PE below the ATM when out of the money
            if keep and 1 <= keep["offset_steps"] * outward <= OFFSET + 2:
                e["last_active"] = now
                keep["locked_since"], keep["relocked"] = e["since"], False
                return keep
        self.state[sym] = {"side": side, "strike": fresh["strike"], "since": now, "last_active": now}
        fresh["locked_since"], fresh["relocked"] = now, True
        return fresh


def premium_pnl(opt_entry, delta, gross_points, cost_points, lot=None, live_exit=None):
    """Premium P/L of a closed signal. Uses the contract's real exit price when one is known, otherwise entry + delta x index move.
    Net = gross premium move minus the cost allowance (index points x delta). Returns a dict (all None when no premium entry exists)."""
    entry, delta, gross_points = _num(opt_entry), _num(delta), _num(gross_points)
    if entry is None or gross_points is None:
        return {"exit": None, "gross": None, "net": None, "net_rs": None, "basis": None}
    delta = delta if delta and 0.05 <= delta <= 0.95 else 0.5
    live_exit = _num(live_exit)
    if live_exit is not None and live_exit > 0:
        exit_px, basis = live_exit, "contract price"
    else:
        exit_px, basis = max(entry + delta * gross_points, 0.05), "est. from index move x delta"
    gross = exit_px - entry
    net = gross - (_num(cost_points) or 0.0) * delta
    lot = _num(lot)
    return {"exit": round(exit_px, 2), "gross": round(gross, 2), "net": round(net, 2), "net_rs": round(net * lot, 0) if lot else None, "basis": basis}


def decide_buy(final, plan, side):
    """Turn a CONFIRMED index setup into a BUY (or an explicit NO BUY) using the option profit check. Mutates and returns `final`.
    plan None = the option leg is unavailable (no chain): the index setup stands and is logged on index levels only."""
    final["buy"] = False
    final["buy_basis"] = None
    if final.get("level") != "CONFIRMED":
        return final
    if plan is None:
        final.update(buy=True, buy_basis="index", action=f"BUY {side} (index levels)",
                     headline=final.get("headline", "") + " Option contract unavailable, so the index stop and target are used.")
        return final
    if plan.get("ok"):
        c = plan["contract"]
        final.update(buy=True, buy_basis="option", action=f"BUY {c['label']}", headline=plan["summary"])
    else:
        final.update(action=f"CONFIRMED {side} - NO BUY", headline=plan["summary"])
        final["blockers"] = list(final.get("blockers") or []) + [f"Profit check - {x['label']}: {x['detail']}" for x in plan.get("checks", []) if not x["pass"]]
    return final


def swing_levels(candles, price, side, atr, symbol="NIFTY", span=None):
    """Nearest swing high / low (and round number) beyond `price` in the trade direction, from the loaded candles.
    Returns {"price", "distance", "basis"} or None when price is in open air (e.g. at a new high)."""
    span = SWING_SPAN if span is None else span
    price, atr = _num(price), _num(atr)
    cs = normalize_candles(candles or [])
    if not price or not atr or len(cs) < 2 * span + 3:
        return None
    up = side == "CE"
    cand = []
    for i in range(span, len(cs) - span):
        w = cs[i - span:i + span + 1]
        if up and cs[i]["high"] == max(c["high"] for c in w):
            cand.append((cs[i]["high"], "swing high"))
        if not up and cs[i]["low"] == min(c["low"] for c in w):
            cand.append((cs[i]["low"], "swing low"))
    step = ROUND_STEP.get(symbol, 100.0)
    base = (price // step) * step
    for k in range(0, 4):
        lvl = base + (k + 1) * step if up else base - k * step
        cand.append((lvl, "round number"))
    min_gap = 0.3 * atr                           # a level glued to price is not "room"
    ahead = [(p, b) for p, b in cand if (p - price) * (1 if up else -1) >= min_gap]
    if not ahead:
        return None
    p, b = min(ahead, key=lambda x: abs(x[0] - price))
    return {"price": round(p, 2), "distance": round(abs(p - price), 2), "basis": b}


def estimate_delta(offset_steps):
    """Fallback when the chain carries no greeks: ATM ~0.5, each strike out loses ~0.08 (never below 0.15)."""
    return max(0.15, 0.5 - 0.08 * abs(offset_steps or 0))


def build_plan(side, symbol, pick, price, atr, thresholds, cost_points=4.0, lot=None, opt_read=None, room=None,
               sl_points=None, target_points=None, live_ltp=None):
    """Profit check + premium levels for one signal. `thresholds` = dashboard_data.THRESHOLDS. Never raises on missing data:
    a missing piece becomes a failed (or, for the optional premium-chart read, an 'unknown') check."""
    t = thresholds
    price, atr = _num(price), _num(atr)
    checks = []

    def chk(label, ok, detail):
        checks.append({"label": label, "pass": bool(ok), "detail": detail})

    if not pick:
        return {"ok": False, "summary": "No option contract found at the chosen strike (option chain unavailable)", "checks": [], "contract": None, "levels": None}
    ltp = _num(live_ltp) or pick.get("ltp")
    delta = abs(pick["delta"]) if pick.get("delta") and 0.05 <= abs(pick["delta"]) <= 0.95 else estimate_delta(pick.get("offset_steps"))
    delta_src = "chain" if pick.get("delta") and 0.05 <= abs(pick["delta"]) <= 0.95 else "estimated"
    label = f"{symbol} {pick['strike']:g} {side}"
    contract = {"instrument_key": pick["instrument_key"], "type": side, "strike": pick["strike"], "expiry": pick.get("expiry"), "label": label,
                "atm_strike": pick["atm_strike"], "offset_steps": pick["offset_steps"], "ltp": ltp, "delta": round(delta, 2), "delta_source": delta_src,
                "lot": lot, "bid": pick.get("bid"), "ask": pick.get("ask"), "volume": pick.get("volume"), "iv": pick.get("iv")}

    chk(f"Premium usable (≥ ₹{MIN_PREMIUM:g})", bool(ltp and ltp >= MIN_PREMIUM), f"{label} premium {ltp:,.2f}" if ltp else f"{label}: no premium quote")
    bid, ask = pick.get("bid"), pick.get("ask")
    if bid and ask and ask > 0:
        spread = (ask - bid) / ((ask + bid) / 2) * 100
        chk(f"Spread ≤ {MAX_SPREAD_PCT:g}%", spread <= MAX_SPREAD_PCT, f"bid {bid:,.2f} / ask {ask:,.2f} ({spread:.1f}%)")
    else:
        chk(f"Spread ≤ {MAX_SPREAD_PCT:g}%", True, "bid / ask not available — the cost allowance covers the spread")

    if opt_read and opt_read.get("status") == "ok":
        bull, bear = opt_read.get("bull_score") or 0, opt_read.get("bear_score") or 0
        chk("Premium chart trending up (the contract's own candles)", bull >= bear and bull >= 50,
            f"{label} premium: buy-strength {bull}/100 vs sell-strength {bear} · {opt_read.get('regime_label') or opt_read.get('regime')}")
    else:
        chk("Premium chart trending up (the contract's own candles)", True, f"{label} has too few premium candles to score — judged on the index only")

    if not price or not atr:
        chk("ATR available", False, "No ATR / price to size the stop and target")
        return {"ok": False, "summary": "No ATR to size the trade", "checks": checks, "contract": contract, "levels": None}

    sl_pts = _num(sl_points) or max(t["atr_sl"] * atr, t["atr_min_sl_pts"])
    base_tgt = _num(target_points) or t["atr_t2"] * atr
    tgt_pts, tgt_name = base_tgt, f"{base_tgt / atr:g}xATR"
    if room:
        avail = room["distance"] - ROOM_BUFFER_ATR * atr
        if avail < base_tgt:
            tgt_pts, tgt_name = max(avail, 0.0), f"{room['basis']} {room['price']:,.0f}"
        chk(f"Room to the {'resistance' if side == 'CE' else 'support'} ≥ {MIN_RR:g} × stop", avail >= MIN_RR * sl_pts,
            f"nearest {room['basis']} {room['price']:,.2f} is {room['distance']:.1f} pts away ({room['distance'] / atr:.1f} ATR); stop {sl_pts:.1f} pts")
    else:
        chk(f"Room to the {'resistance' if side == 'CE' else 'support'} ≥ {MIN_RR:g} × stop", True, "price is in open air — no swing high/low or round number in the way")

    d = 1 if side == "CE" else -1
    cost_prem = cost_points * delta
    raw_sl = delta * sl_pts
    cap = ltp * t["max_premium_sl_pct"] / 100.0 if ltp else raw_sl
    sl_prem = min(raw_sl, cap)
    k = sl_prem / raw_sl if raw_sl else 1.0
    gain = k * delta * tgt_pts
    rr = (gain - cost_prem) / (sl_prem + cost_prem) if (sl_prem + cost_prem) > 0 else 0.0
    chk(f"Reward : risk ≥ {MIN_RR:g} after costs", rr >= MIN_RR, f"target {tgt_pts:.1f} idx pts vs stop {sl_pts:.1f} · premium +{gain:.1f} / −{sl_prem:.1f} · cost {cost_prem:.1f} → {rr:.2f} : 1")

    levels = None
    if ltp:
        levels = {"index_entry": round(price, 2), "index_stop": round(price - d * sl_pts, 2), "index_target": round(price + d * tgt_pts, 2),
                  "sl_points": round(sl_pts, 2), "target_points": round(tgt_pts, 2), "target_name": tgt_name,
                  "entry": round(ltp, 2), "sl": round(max(ltp - sl_prem, 0.05), 2), "target": round(ltp + gain, 2),
                  "t1": round(ltp + k * delta * min(tgt_pts, t["atr_t1"] * atr), 2), "rr": round(rr, 2), "cost_premium": round(cost_prem, 2),
                  "sl_clamped": raw_sl > cap, "risk_rs": round((sl_prem + cost_prem) * lot, 0) if lot else None,
                  "reward_rs": round((gain - cost_prem) * lot, 0) if lot else None}
    ok = all(c["pass"] for c in checks) and bool(levels)
    if ok:
        summary = (f"{label} @ {ltp:,.2f} · stop {levels['sl']:,.2f} · target {levels['target']:,.2f} ({tgt_name}) · {rr:.1f} : 1"
                   + (f" · risk ₹{levels['risk_rs']:,.0f} / reward ₹{levels['reward_rs']:,.0f} per lot" if lot else ""))
    else:
        why = "; ".join(f"{c['label']}: {c['detail']}" for c in checks if not c["pass"]) or "levels unavailable"
        summary = f"{label}: profit check not met — {why}"
    return {"ok": ok, "summary": summary, "checks": checks, "contract": contract, "levels": levels}
