"""v1.20.38: trade feasibility and the ordered BUY decision flow (owner's specification, 6 Nov 2026).

  Technical signal generated (BUY CE / BUY PE candidate)
  -> daily signal count: a repeated same-side signal needs a fresh structure + fresh breakout + successful retest, else WAIT
  -> support / resistance room: < 0.25 ATR block; 0.25-0.5 ATR only with exceptional confirmation; > 0.5 ATR normal
  -> breakout and retest: a breakout with its retest holding is the strong confirmation; a forming pattern is not a confirmation
  -> market and option confirmation: Data Check dashboard (breadth, sectors, premium), live data freshness, 60 s reconfirm
  -> FINAL DECISION: BUY CE / BUY PE / WAIT

Two separate scores are shown: the TECHNICAL CONFIRMATION score (the engine's 0-100 score) and the TRADE FEASIBILITY score
(room to the next level, volatility, entry quality). Feasibility is information; only the room rule above blocks entries.
"""
from datetime import datetime, timedelta

ROOM_BLOCK_ATR = 0.25
ROOM_NORMAL_ATR = 0.5
MAX_TICK_AGE_S = 30.0


def sr_room(cs, atr, interval, symbol, side, price):
    """Room to the next opposite level (resistance for CE, support for PE) and distance from the own level, in ATR.
    Same zones and the same split as the Diagnostics replay (diagnostics.sr_features), so live and replay agree."""
    import option_plan as _op
    if not cs or not atr or side not in ("CE", "PE") or price is None:
        return {"room_atr": None, "own_atr": None, "level": None}
    per = max(1, round(375 / max(1, interval)))
    sr = _op.sr_levels(cs[-per * 6:], atr, interval, symbol) or {}
    lv = (sr.get("supports") or []) + (sr.get("resistances") or [])
    below = sorted([l for l in lv if l["price"] <= price], key=lambda l: price - l["price"])
    above = sorted([l for l in lv if l["price"] > price], key=lambda l: l["price"] - price)
    own, opp = (below, above) if side == "CE" else (above, below)
    return {"room_atr": round(abs(opp[0]["price"] - price) / atr, 2) if opp else None,
            "own_atr": round(abs(price - own[0]["price"]) / atr, 2) if own else None,
            "level": opp[0] if opp else None}


def pattern_quality(pattern, side):
    """'retest' (breakout on the trade side, retest holding) > 'breakout' > 'forming' (not a confirmation) > 'against' / 'none'."""
    p = pattern or {}
    if not p.get("side") or p.get("stage") not in ("breakout", "forming"):
        return "none"
    if p.get("side") != side:
        return "against" if p.get("stage") == "breakout" else "none"
    if p.get("stage") == "breakout":
        return "retest" if "retest holding" in str(p.get("name") or "") else "breakout"
    return "forming"


def room_gate(room_atr, quality):
    """< 0.25 ATR: block. 0.25-0.5 ATR: only with exceptional confirmation (breakout with its retest holding). > 0.5 ATR: normal."""
    label = f"Room to the next support / resistance (≥ {ROOM_BLOCK_ATR:g} ATR; {ROOM_BLOCK_ATR:g}-{ROOM_NORMAL_ATR:g} needs a retest-holding breakout)"
    if room_atr is None:
        return {"label": label, "pass": True, "detail": "no opposite level found nearby - open space", "zone": "open"}
    if room_atr < ROOM_BLOCK_ATR:
        return {"label": label, "pass": False, "detail": f"only {room_atr:.2f} ATR to the next level - blocked", "zone": "block"}
    if room_atr < ROOM_NORMAL_ATR:
        ok = quality == "retest"
        return {"label": label, "pass": ok, "zone": "tight",
                "detail": f"{room_atr:.2f} ATR to the next level - " + ("allowed: breakout retest is holding" if ok else "WAIT: needs a breakout with its retest holding")}
    return {"label": label, "pass": True, "detail": f"{room_atr:.2f} ATR to the next level", "zone": "normal"}


def _bar_dt(t):
    try:
        return datetime.fromisoformat(str(t).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def reentry_gate(side, side_buys, pattern, last_closed_bar, interval, structure_ok=True):
    """A second (or later) same-side signal today needs a FRESH structure, a FRESH breakout and a SUCCESSFUL retest: a breakout on the
    trade side that happened after the previous BUY, whose retest is holding, with the price structure still in the trade's direction."""
    label = "Same-side re-entry: fresh structure, fresh breakout and retest after the last BUY"
    got = (side_buys or {}).get(side) or {}
    n = int(got.get("count") or 0)
    if n == 0:
        return {"label": label, "pass": True, "detail": f"first {side} signal today"}
    last = str(got.get("last") or got.get("first") or "")
    if last and str(last_closed_bar or "") == last:
        return {"label": label, "pass": True, "detail": f"this is the bar of today's {side} BUY"}
    p = pattern or {}
    q = pattern_quality(p, side)
    bar_dt, last_dt = _bar_dt(last_closed_bar), _bar_dt(last)
    brk_dt = bar_dt - timedelta(minutes=interval * int(p.get("bars_ago") or 0)) if bar_dt else None
    fresh = bool(brk_dt and last_dt and brk_dt.replace(tzinfo=None) > last_dt.replace(tzinfo=None))
    ok = q == "retest" and fresh and structure_ok
    why = ("ok: new breakout after the last BUY and its retest is holding" if ok else
           "WAIT: no breakout on this side" if q in ("none", "against") else
           "WAIT: the pattern is only forming" if q == "forming" else
           "WAIT: breakout without a successful retest yet" if q == "breakout" else
           "WAIT: the breakout is older than the last BUY" if not fresh else "WAIT: price structure no longer supports the side")
    return {"label": label, "pass": ok, "detail": f"{n} {side} BUY already today (last {last[11:16] or last}) - {why}"}


def freshness_gate(tick_age_s, market_open):
    label = f"Live data fresh (index tick ≤ {MAX_TICK_AGE_S:g} s old)"
    if not market_open:
        return None
    if tick_age_s is None:
        return {"label": label, "pass": True, "detail": "no live stream - using the latest candles (REST)"}
    return {"label": label, "pass": tick_age_s <= MAX_TICK_AGE_S, "detail": f"last tick {tick_age_s:.0f} s ago"}


def feasibility(room_atr, own_atr, stretch_atr, atr, atr_median, quality):
    """TRADE FEASIBILITY score 0-100: room (40), volatility (20), entry quality (40: closeness to EMA 9, distance from the own
    level, pattern quality). Information for the trader; the hard rule is the room gate."""
    parts = {}
    if room_atr is None:
        parts["room"] = 30
    else:
        parts["room"] = round(max(0.0, min(1.0, (room_atr - ROOM_BLOCK_ATR) / (1.5 - ROOM_BLOCK_ATR))) * 40)
    r = (atr / atr_median) if (atr and atr_median) else None
    if r is None:
        parts["volatility"] = 10
    elif 0.7 <= r <= 1.5:
        parts["volatility"] = 20
    elif r < 0.7:
        parts["volatility"] = round(max(0.0, (r - 0.4) / 0.3) * 20)
    else:
        parts["volatility"] = round(max(0.0, (2.2 - r) / 0.7) * 20)
    s = abs(stretch_atr) if stretch_atr is not None else None
    parts["entry"] = (15 if s is None else 20 if s <= 0.5 else 10 if s <= 1.0 else 0)
    parts["own_level"] = (5 if own_atr is None else 10 if 0.5 <= own_atr <= 1.5 else 5)
    parts["pattern"] = {"retest": 10, "breakout": 6, "forming": 2, "none": 4, "against": 0}.get(quality, 4)
    score = sum(parts.values())
    return {"score": int(score), "parts": parts, "atr_vs_median": round(r, 2) if r else None,
            "label": "good" if score >= 65 else "fair" if score >= 45 else "poor"}


# ---- the ordered flow shown to the trader ----
STEPS = [
    ("signal", "Technical signal (BUY CE / BUY PE candidate)"),
    ("count", "Daily signal count / repeated same-side signal"),
    ("room", "Support / resistance room (min 0.25 ATR)"),
    ("pattern", "Breakout and retest (forming patterns do not confirm)"),
    ("market", "Market and option confirmation (breadth, sectors, premium, data freshness)"),
]


def _step_of(label):
    l = str(label)
    if l.startswith(("One trade per side", "Same-side re-entry", "Daily loss limit", "Daily profit target", "Index enabled")):
        return "count"
    if l.startswith("Room to the next support"):
        return "room"
    if l.startswith(("Not a fresh breakout", "No opposite chart-pattern", "Entry trigger", "Pullback-and-resume", "Closed-candle price-action")):
        return "pattern"
    if l.startswith(("Data Check dashboard", "Live data fresh", "Reconfirmed", "Signal fresh", "Entry time window", "Profit check", "Option")):
        return "market"
    return "signal"


def build(tech_gates, fused_gates, final_level, side, tech_score, feas, quality, buy_ok=None, buy_checks=None):
    """Ordered decision flow: each step passes only when all of its gates pass; the first failing step is where the decision stops."""
    groups = {k: [] for k, _ in STEPS}
    for g in list(tech_gates or []) + list(fused_gates or []):
        groups[_step_of(g.get("label"))].append(g)
    for c in (buy_checks or []):
        groups["market"].append({"label": "Profit check: " + str(c.get("label")), "pass": bool(c.get("pass")), "detail": c.get("detail")})
    flow, stopped = [], None
    for key, title in STEPS:
        gs = groups[key]
        ok = all(g.get("pass") for g in gs) and (bool(side) if key == "signal" else True)
        failed = [g for g in gs if not g.get("pass")]
        note = {"signal": f"{side or 'no side'} · technical score {tech_score}/100",
                "pattern": {"retest": "breakout with retest holding (strong)", "breakout": "breakout, retest not yet confirmed",
                            "forming": "pattern only forming - not counted", "against": "pattern breakout AGAINST the side",
                            "none": "no chart pattern"}.get(quality, ""),
                "room": next((g["detail"] for g in gs if str(g.get("label")).startswith("Room to")), ""),
                }.get(key, "")
        flow.append({"step": key, "title": title, "pass": ok, "note": note,
                     "failed": [f"{g['label']}: {g.get('detail', '')}" for g in failed][:4]})
        if not ok and stopped is None:
            stopped = key
    final = ("BUY " + side) if (side and final_level == "CONFIRMED" and buy_ok is not False and stopped is None) else "WAIT"
    return {"steps": flow, "stopped_at": stopped, "final": final, "technical_score": tech_score,
            "feasibility": feas}
