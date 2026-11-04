"""Option plan for a signal (v1.19). Pure functions, no web framework, so it can be unit-tested.

What happens to a signal now:

  WATCH CE / PE   -> pick the contract 2 strikes IN THE MONEY (CE: ATM - 2 strikes, PE: ATM + 2 strikes by default)
                     so the terminal can switch its chart to it and the phone gets a heads-up.
  CONFIRMED       -> second look before anything is called a BUY ("profit check"):
                       1. index stop  = the ATR stop (0.75 ATR, min 15 pts, or the owner's custom multiple)
                       2. index target= the ATR target (3 ATR, or custom) BUT capped just before the next support / resistance zone in the
                                        trade direction - a target beyond a strong level is a hope, not a plan
                       3. reward : risk on the index must be >= MIN_RR (1.5) and the target >= 1.2 ATR
                       4. option premium levels from delta, gamma and theta of the chosen contract (Upstox option-chain greeks; a
                          moneyness-based delta if the chain has none), premium stop capped at 25 % of the premium
                       5. after round-trip charges (Rs) the premium profit at target must be >= 2 x charges, premium reward : risk >= 1.2,
                          bid/ask spread <= 3 % when the book is known, and the premium itself must not be trending down
                     Only when all of that passes is the signal a BUY: it is logged with buy price, stop, target and later the exit and
                     net P/L, and the BUY alert is pushed. Otherwise it stays WATCH with the reason.

Every number is configurable from the environment (OPTION_* below) and the strike offset also from the Signals page.
"""
import os

STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25, "SENSEX": 100}


def _envf(name, default):
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return float(default)


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


DEFAULTS = {
    "steps": int(_envf("OPTION_STRIKE_STEPS", 2)),            # strikes away from ATM
    "moneyness": (os.getenv("OPTION_MONEYNESS", "ITM").strip().upper() or "ITM"),   # ITM = CE below ATM / PE above ATM (default); OTM = the other way
    "min_rr": _envf("OPTION_MIN_RR", 1.0),                    # index reward : risk (1 : 1 with the 1.5 ATR stop / 1.5 ATR target plan)
    "min_target_atr": _envf("OPTION_MIN_TARGET_ATR", 1.2),    # target must be at least this far (ATR); never more than the plan's own target
    "sr_buffer_atr": _envf("OPTION_SR_BUFFER_ATR", 0.15),     # stop the target this far before the level
    "sr_ignore_atr": _envf("OPTION_SR_IGNORE_ATR", 0.3),      # a level closer than this is being broken now - look past it
    "max_spread_pct": _envf("OPTION_MAX_SPREAD_PCT", 3.0),
    "charges_rs": _envf("OPTION_CHARGES_RS", 60.0),           # brokerage + STT + exchange + GST + stamp, one round trip, per order
    "lots": max(1, int(_envf("OPTION_LOTS", 1))),
    "min_profit_x_charges": _envf("OPTION_MIN_PROFIT_X_CHARGES", 2.0),
    "min_premium_rr": _envf("OPTION_MIN_PREMIUM_RR", 0.8),
    "max_premium_sl_pct": _envf("OPTION_MAX_PREMIUM_SL_PCT", 25.0),
    "hold_minutes": _envf("OPTION_HOLD_MINUTES", 45.0),       # expected holding time, for theta decay
    "min_target_pts": 0.0,
}

# v1.19.5 exit plans. The stop is never closer than MIN_SL_ATR (1.5 ATR, at least 20 index points) - the owner's rule: entries got
# stopped by normal noise (median adverse move after a signal was 45-55 pts, about 1.8 ATR). The target is chosen per plan.
# In the Oct-Nov replays, on the signals the new time windows let through, a 1.5 ATR target was reached far more often within the
# session than 2-3 ATR targets (which mostly ended as time exits), so 1.5 ATR (1 : 1) is the default.
MIN_SL_ATR = _envf("MIN_SL_ATR", 1.5)
MIN_SL_PTS = _envf("MIN_SL_PTS", 20)
EXIT_PLANS = {
    "t1.5": {"label": "Stop 1.5 ATR · target 1.5 ATR (1 : 1)", "target_atr": 1.5},
    "t2": {"label": "Stop 1.5 ATR · target 2 ATR (1 : 1.33)", "target_atr": 2.0},
    "t3": {"label": "Stop 1.5 ATR · target 3 ATR (1 : 2)", "target_atr": 3.0},
}
DEFAULT_EXIT_PLAN = os.getenv("EXIT_PLAN", "t1.5") if os.getenv("EXIT_PLAN", "t1.5") in EXIT_PLANS else "t1.5"


def plan_levels(plan_name=None, custom_sl_atr=None, custom_target_atr=None):
    """(sl_atr, min_sl_pts, target_atr, plan_key) for the chosen exit plan. A custom stop from the Signals page is floored at MIN_SL_ATR."""
    key = plan_name if plan_name in EXIT_PLANS else DEFAULT_EXIT_PLAN
    sl = max(MIN_SL_ATR, float(custom_sl_atr)) if custom_sl_atr else MIN_SL_ATR
    tgt = float(custom_target_atr) if custom_target_atr else EXIT_PLANS[key]["target_atr"]
    return sl, MIN_SL_PTS, tgt, key


def strike_step(symbol, strikes=None):
    if strikes and len(strikes) >= 3:
        gaps = sorted({round(b - a, 2) for a, b in zip(strikes, strikes[1:]) if b > a})
        if gaps:
            return gaps[0]
    return STRIKE_STEP.get(str(symbol).upper(), 50)


def _leg(row, side):
    o = (row.get("call_options") if side == "CE" else row.get("put_options")) or {}
    md, gk = o.get("market_data") or {}, o.get("option_greeks") or {}
    return {"instrument_key": o.get("instrument_key"), "ltp": _num(md.get("ltp")), "bid": _num(md.get("bid_price")), "ask": _num(md.get("ask_price")),
            "oi": _num(md.get("oi")), "volume": _num(md.get("volume")), "delta": _num(gk.get("delta")), "gamma": _num(gk.get("gamma")),
            "theta": _num(gk.get("theta")), "iv": _num(gk.get("iv", md.get("iv")))}


def pick_contract(chain_rows, spot, side, symbol="NIFTY", steps=None, moneyness=None, expiry=None, lot_size=None):
    """The contract `steps` strikes from ATM on the trade side. Chain rows are Upstox /v2/option/chain rows.
    Falls back toward ATM when the wanted strike has no live price. Returns a dict or None."""
    steps = DEFAULTS["steps"] if steps is None else max(0, int(steps))
    moneyness = (moneyness or DEFAULTS["moneyness"]).upper()
    spot = _num(spot) or _num((chain_rows or [{}])[0].get("underlying_spot_price") if chain_rows else None)
    if side not in ("CE", "PE") or not chain_rows or not spot:
        return None
    by_strike = {}
    for r in chain_rows:
        k = _num(r.get("strike_price"))
        if k is not None:
            by_strike[k] = r
    strikes = sorted(by_strike)
    if not strikes:
        return None
    atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    # OTM: CE strikes above spot, PE strikes below. ITM: the reverse.
    direction = (1 if side == "CE" else -1) * (1 if moneyness == "OTM" else -1)
    for s in range(steps, -1, -1):                 # wanted strike first, then step back toward ATM if it has no price
        i = atm_i + direction * s
        if not 0 <= i < len(strikes):
            continue
        leg = _leg(by_strike[strikes[i]], side)
        if leg["instrument_key"] and (leg["ltp"] or 0) > 0:
            label = "ATM" if s == 0 else f"{s} strike{'s' if s > 1 else ''} {moneyness}"
            return {"symbol": str(symbol).upper(), "type": side, "strike": strikes[i], "atm_strike": strikes[atm_i], "steps": s, "steps_wanted": steps,
                    "moneyness": moneyness if s else "ATM", "label": label, "expiry": expiry, "lot_size": lot_size, "spot": round(spot, 2),
                    "step": strike_step(symbol, strikes), **leg}
    return None


def fallback_delta(pick):
    """Delta when the chain has no greeks: ~0.5 at the money, about 0.08 less per strike out of the money, 0.08 more per strike in it."""
    s = (pick or {}).get("steps") or 0
    if (pick or {}).get("moneyness") == "ITM":
        return min(0.85, 0.5 + 0.08 * s)
    return max(0.15, 0.5 - 0.08 * s)


# ---------------------------------------------------------------- support / resistance (port of the terminal's computeSR)
def _ist_day(c, ist):
    return c["_dt"].astimezone(ist).date()


def sr_levels(cs, atr, interval=5, symbol="NIFTY", ist=None, per_side=3):
    """Support / resistance zones from normalised candles (indicators.normalize_candles): swing pivots (3 bars each side) over the
    last 5 sessions plus previous-day high / low / close and today's high / low, clustered within max(0.6 ATR, 0.04 %).
    Same rules as the terminal's S/R map. Returns {"supports": [...], "resistances": [...]} nearest first."""
    from indicators import IST
    ist = ist or IST
    n = len(cs)
    if n < 30 or not atr:
        return {"supports": [], "resistances": []}
    last = cs[-1]["close"]
    tol = max(atr * 0.6, last * 0.0004)
    per_session = max(1, round(375 / max(1, interval)))
    start, L = max(0, n - per_session * 5), 3
    cands = []
    for i in range(max(L, start), n - L):
        is_h = all(cs[i]["high"] > cs[i - k]["high"] and cs[i]["high"] >= cs[i + k]["high"] for k in range(1, L + 1))
        is_l = all(cs[i]["low"] < cs[i - k]["low"] and cs[i]["low"] <= cs[i + k]["low"] for k in range(1, L + 1))
        if is_h:
            cands.append({"price": cs[i]["high"], "idx": i, "kind": "Swing", "w": 2, "swing": True})
        if is_l:
            cands.append({"price": cs[i]["low"], "idx": i, "kind": "Swing", "w": 2, "swing": True})
    days = []
    for i, c in enumerate(cs):
        d = _ist_day(c, ist)
        if not days or days[-1][0] != d:
            days.append([d, i, i])
        else:
            days[-1][2] = i
    if len(days) >= 2:
        _, pf, pt = days[-2]
        _, tf, tt = days[-1]
        ph = max(range(pf, pt + 1), key=lambda i: cs[i]["high"]); pl = min(range(pf, pt + 1), key=lambda i: cs[i]["low"])
        cands += [{"price": cs[ph]["high"], "idx": ph, "kind": "PDH", "w": 3}, {"price": cs[pl]["low"], "idx": pl, "kind": "PDL", "w": 3},
                  {"price": cs[pt]["close"], "idx": pt, "kind": "PDC", "w": 2}]
        th = max(range(tf, tt + 1), key=lambda i: cs[i]["high"]); tl = min(range(tf, tt + 1), key=lambda i: cs[i]["low"])
        if th < n - 1:
            cands.append({"price": cs[th]["high"], "idx": th, "kind": "Day H", "w": 2})
        if tl < n - 1:
            cands.append({"price": cs[tl]["low"], "idx": tl, "kind": "Day L", "w": 2})
    cands.sort(key=lambda c: c["price"])
    zones = []
    for c in cands:
        if not zones or abs(c["price"] - zones[-1]["mean"]) > tol:
            zones.append({"items": [], "mean": c["price"]})
        z = zones[-1]
        z["items"].append(c)
        z["mean"] = sum(x["price"] for x in z["items"]) / len(z["items"])
    step = STRIKE_STEP.get(str(symbol).upper())
    levels = []
    for z in zones:
        tw = sum(x["w"] for x in z["items"])
        price = sum(x["price"] * x["w"] for x in z["items"]) / tw
        last_idx = max(x["idx"] for x in z["items"])
        recency = 2 if last_idx >= n - per_session else 1 if last_idx >= n - per_session * 3 else 0
        rnd = bool(step and abs(price - round(price / step) * step) <= tol * 0.6)
        score = tw + recency + (1 if rnd else 0)
        kinds = sorted({x["kind"] for x in z["items"] if not x.get("swing")})
        swings = sum(1 for x in z["items"] if x.get("swing"))
        basis = " · ".join(kinds + ([f"{swings} swing{'s' if swings > 1 else ''}"] if swings else []) + (["round no."] if rnd else []))
        levels.append({"price": round(price, 2), "score": score, "strength": "Strong" if score >= 8 else "Medium" if score >= 5 else "Weak",
                       "basis": basis, "distance": round(abs(last - price), 2)})

    def pick(arr):
        arr = sorted(arr, key=lambda l: l["distance"])
        chosen = ([l for l in arr if l["score"] >= 5] + [l for l in arr if l["score"] < 5])[:per_side]
        return sorted(chosen, key=lambda l: l["distance"])
    return {"supports": pick([l for l in levels if l["price"] <= last]), "resistances": pick([l for l in levels if l["price"] > last]), "last": last}


# ---------------------------------------------------------------- the profit check
def profit_plan(side, entry, atr, sr=None, pick=None, premium_trend=None, sl_atr=0.75, min_sl_pts=15.0, target_atr=3.0, cfg=None):
    """Second look at a CONFIRMED setup: where is the realistic target, and does the option pay after costs if price gets there?
    Returns {"ok", "checks": [{label, pass, detail}], "index": {...}, "option": {...} | None, "summary"}."""
    c = dict(DEFAULTS, **(cfg or {}))
    entry, atr = _num(entry), _num(atr)
    checks = []
    if side not in ("CE", "PE") or not entry or not atr or atr <= 0:
        return {"ok": False, "checks": [{"label": "Price and ATR available", "pass": False, "detail": "no price / ATR"}], "index": None, "option": None,
                "summary": "Profit check not possible (no price / ATR)."}
    d = 1 if side == "CE" else -1
    risk = max(sl_atr * atr, min_sl_pts)
    base = max(target_atr * atr, c.get("min_target_pts") or 0.0)
    target_pts, basis, barrier = base, f"{target_atr:g} ATR", None
    levels = (sr or {}).get("resistances" if side == "CE" else "supports") or []
    for lv in levels:
        dist = (lv["price"] - entry) * d
        if dist <= c["sr_ignore_atr"] * atr or lv.get("strength") == "Weak":
            continue                      # being broken right now / single-touch zone: not a wall
        barrier = lv
        capped = dist - c["sr_buffer_atr"] * atr
        if capped < target_pts:
            target_pts = capped
            basis = f"just before {'R' if side == 'CE' else 'S'} {lv['price']:,.2f} ({lv['strength']}, {lv.get('basis') or 'zone'})"
        break
    min_target = min(base, max(c["min_rr"] * risk, c["min_target_atr"] * atr, c.get("min_target_pts") or 0.0))
    rr = target_pts / risk if risk else 0
    checks.append({"label": f"Room to the target (≥ {c['min_rr']:g} × risk and ≥ {c['min_target_atr']:g} ATR)", "pass": target_pts >= min_target - 1e-9,
                   "detail": f"target {target_pts:+.1f} pts ({basis}) vs stop {risk:.1f} pts · reward:risk {rr:.2f}"
                             + ("" if target_pts >= min_target - 1e-9 else f" — needs ≥ {min_target:.1f} pts; the next level is too close")})
    index = {"entry": round(entry, 2), "stop": round(entry - d * risk, 2), "target": round(entry + d * target_pts, 2), "risk_pts": round(risk, 2),
             "reward_pts": round(target_pts, 2), "rr": round(rr, 2), "target_basis": basis, "atr": round(atr, 2),
             "sl_atr": round(risk / atr, 2), "target_atr": round(target_pts / atr, 2),
             "barrier": {"price": barrier["price"], "strength": barrier["strength"]} if barrier else None}
    option = None
    if not pick:
        checks.append({"label": "Option contract priced", "pass": False, "detail": "No option chain / no live premium for the chosen strike"})
    else:
        ltp = _num(pick.get("ask")) if (_num(pick.get("ask")) or 0) > 0 else _num(pick.get("ltp"))
        delta_src = "chain greeks"
        delta = abs(_num(pick.get("delta")) or 0)
        if not 0.03 <= delta <= 1:
            delta, delta_src = fallback_delta(pick), "estimated from moneyness"
        gamma = abs(_num(pick.get("gamma")) or 0) if delta_src == "chain greeks" else 0.0
        theta = _num(pick.get("theta")) or 0.0           # premium per day (negative)
        decay = abs(theta) * c["hold_minutes"] / 375.0
        gain = delta * target_pts + 0.5 * gamma * target_pts ** 2 - decay
        loss_raw = max(0.0, delta * risk - 0.5 * gamma * risk ** 2) + decay
        cap = (ltp or 0) * c["max_premium_sl_pct"] / 100.0
        loss = min(loss_raw, cap) if cap > 0 else loss_raw
        lot = int(_num(pick.get("lot_size")) or 0) or None
        qty = (lot or 0) * c["lots"]
        charges = c["charges_rs"]
        exp_profit_rs = gain * qty - charges if qty else None
        exp_loss_rs = loss * qty + charges if qty else None
        spread = None
        if (_num(pick.get("bid")) or 0) > 0 and (_num(pick.get("ask")) or 0) > 0:
            mid = (pick["bid"] + pick["ask"]) / 2
            spread = (pick["ask"] - pick["bid"]) / mid * 100 if mid else None
        name = f"{pick.get('symbol', '')} {pick.get('strike'):g} {side}".strip()
        checks.append({"label": "Option contract priced", "pass": bool(ltp and ltp > 0), "detail": f"{name} ({pick.get('label')}) · premium {ltp or 0:,.2f}"
                       + (f" · lot {lot}" if lot else "")})
        if spread is not None:
            checks.append({"label": f"Bid/ask spread ≤ {c['max_spread_pct']:g}%", "pass": spread <= c["max_spread_pct"],
                           "detail": f"bid {pick['bid']:,.2f} / ask {pick['ask']:,.2f} = {spread:.1f}% of the premium"})
        prem_rr = gain / loss if loss > 0 else 0
        if qty:
            checks.append({"label": f"Profit at target ≥ {c['min_profit_x_charges']:g} × charges (₹{charges:,.0f})", "pass": exp_profit_rs >= c["min_profit_x_charges"] * charges,
                           "detail": f"≈ {gain:+.2f} premium × {qty} = ₹{gain * qty:,.0f} − charges ₹{charges:,.0f} = ₹{exp_profit_rs:,.0f}"})
        else:
            checks.append({"label": "Premium gain at target covers costs", "pass": gain > 0,
                           "detail": f"≈ {gain:+.2f} premium per unit (lot size unknown, charges not applied)"})
        checks.append({"label": f"Premium reward : risk ≥ {c['min_premium_rr']:g}", "pass": prem_rr >= c["min_premium_rr"],
                       "detail": f"+{gain:.2f} / −{loss:.2f} premium = {prem_rr:.2f} (delta {delta:.2f} {delta_src}"
                                 + (f", gamma {gamma:.4f}" if gamma else "") + (f", theta {theta:.1f}/day → −{decay:.2f} over {c['hold_minutes']:g} min" if theta else "") + ")"})
        if premium_trend:
            pt = premium_trend
            falling = pt.get("status") == "ok" and (pt.get("bear_score") or 0) > (pt.get("bull_score") or 0) and str(pt.get("regime_label")) in ("DOWNTREND", "BEARISH")
            checks.append({"label": "Option premium not trending down", "pass": not falling,
                           "detail": (f"premium chart {pt.get('regime_label')} (bull {pt.get('bull_score')} / bear {pt.get('bear_score')})" if pt.get("status") == "ok"
                                      else "premium chart too short to read — not used")})
        option = {"name": name, "instrument_key": pick.get("instrument_key"), "type": side, "strike": pick.get("strike"), "expiry": pick.get("expiry"),
                  "label": pick.get("label"), "entry": round(ltp, 2) if ltp else None,
                  "stop": round(max(0.05, ltp - loss), 2) if ltp else None, "target": round(ltp + gain, 2) if ltp else None,
                  "delta": round(delta, 3), "delta_source": delta_src, "gamma": gamma or None, "theta": theta or None, "iv": pick.get("iv"),
                  "lot_size": lot, "lots": c["lots"], "qty": qty or None, "charges_rs": charges, "spread_pct": round(spread, 2) if spread is not None else None,
                  "exp_profit_rs": round(exp_profit_rs, 0) if exp_profit_rs is not None else None,
                  "exp_loss_rs": round(exp_loss_rs, 0) if exp_loss_rs is not None else None, "premium_rr": round(prem_rr, 2), "sl_capped": loss_raw > cap > 0}
    ok = all(ch["pass"] for ch in checks)
    if ok and option:
        summary = (f"BUY {option['name']} @ {option['entry']:,.2f} · SL {option['stop']:,.2f} · target {option['target']:,.2f}"
                   f" (index {index['entry']:,.0f} → SL {index['stop']:,.0f} / T {index['target']:,.0f}, R:R {index['rr']:.1f})")
    else:
        bad = [ch for ch in checks if not ch["pass"]]
        summary = "No buy — profit check failed: " + "; ".join(f"{ch['label']} ({ch['detail']})" for ch in bad[:2])
    return {"ok": ok, "checks": checks, "index": index, "option": option, "summary": summary}
