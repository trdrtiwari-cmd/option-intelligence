"""Signal fusion (v1.9): main-terminal technical signal + Data Check dashboard signal + signal TIMING.

Pure functions (no web-framework imports) so they can be unit-tested.

Why timing matters: a confirmed setup is only worth entering close to the moment it formed. Entering 30 minutes later
means buying after the move (stale signal), after the dashboard flipped, or in the wrong part of the session.
Every signal therefore carries the time it was generated, its age, and how far price travelled since.
"""
from datetime import datetime, timedelta, timezone

from indicators import IST, analyze, drop_forming, mode_rules, normalize_candles

# ---- tunable rules (starting values - review them against your paper-trade CSV) ----
FRESH_BARS = 3            # a signal older than this many candles is LATE (3 x 5m = 15 min)
EXPIRE_BARS = 12          # older than this many candles it is EXPIRED and must re-qualify (12 x 5m = 60 min)
MAX_CHASE_ATR = 1.0       # price already moved more than this many ATR from the signal price => don't chase
REPLAY_BARS = 40          # how far back the replay looks for the start of the current signal
ENTRY_START_MIN = 9 * 60 + 30     # no new entries before 09:30 IST (opening volatility)
ENTRY_CUTOFF_MIN = 15 * 60        # no new entries from 15:00 IST
AGREE_LEVELS = ("CONFIRMED", "BIAS")   # dashboard levels that count as "agrees"


def _hhmm(dt):
    return dt.astimezone(IST).strftime("%H:%M")


def _pack_bar(cs, i, interval):
    close_dt = cs[i]["_dt"] + timedelta(minutes=interval)
    return {"index": i, "bar_open": cs[i]["time"], "generated_at": close_dt.astimezone(IST).isoformat(),
            "text": _hhmm(close_dt) + " IST", "price": cs[i]["close"]}


def replay_signal_start(raw_candles, interval, current, htf_raw=None, htf_interval=None, volume_source=None, now=None, mode="standard"):
    """When did the CURRENT technical signal begin? Re-runs analyze() on the candles as they stood at each earlier
    closed bar (same session only) and walks back while the signal is unchanged. Deterministic and restart-proof.
    Returns {"key": bar | None, "lean": bar | None, "bars": int, "capped": bool} or None if there is too little history.
      key  = first bar of the unbroken run of this exact signal (e.g. CONFIRMED_PE)
      lean = first bar of the unbroken run on the same side (WATCH_PE / CONFIRMED_PE)"""
    cs, _ = drop_forming(normalize_candles(raw_candles), interval, now)
    if len(cs) < 41:
        return None
    last_i = len(cs) - 1
    day = cs[last_i]["_dt"].astimezone(IST).date()
    key, side = current.get("signal"), current.get("side")
    key_open = bool(key) and key != "WAIT"
    lean_open = key_open and bool(side)
    key_start = lean_start = last_i
    htf_cs = normalize_candles(htf_raw) if htf_raw and htf_interval else []
    j, steps = last_i - 1, 0
    while j >= 40 and steps < REPLAY_BARS and (key_open or lean_open):
        if cs[j]["_dt"].astimezone(IST).date() != day:
            break
        close_dt = cs[j]["_dt"] + timedelta(minutes=interval)
        htf_sub = [c for c in htf_cs if c["_dt"] + timedelta(minutes=htf_interval) <= close_dt]
        r = analyze(cs[:j + 1], current.get("symbol", "NIFTY"), interval, htf_raw=htf_sub or None,
                    htf_interval=htf_interval if htf_sub else None, volume_source=volume_source, now=close_dt, mode=mode)
        if r.get("status") != "ok":
            break
        sig = r.get("signal") or "WAIT"
        if key_open:
            if sig == key:
                key_start = j
            else:
                key_open = False
        if lean_open:
            if sig.endswith("_" + side):
                lean_start = j
            else:
                lean_open = False
        j -= 1
        steps += 1
    still_open = key_open or lean_open
    capped = bool(still_open and steps >= REPLAY_BARS)
    return {"key": _pack_bar(cs, key_start, interval) if current.get("signal") not in (None, "WAIT") else None,
            "lean": _pack_bar(cs, lean_start, interval) if side and current.get("signal") not in (None, "WAIT") else None,
            "bars": last_i - key_start, "capped": capped}


class SignalTracker:
    """Remembers when the Data Check dashboard state last CHANGED (the dashboard has no candle history to replay).
    After a server restart the first observation is flagged inexact, and the fusion then falls back to the older,
    more conservative technical start time rather than pretending the signal is brand new."""

    def __init__(self):
        self.state = {}

    def update(self, name, key, price, ts):
        e = self.state.get(name)
        if e is None:
            e = self.state[name] = {"key": key, "since": ts, "observed_from": ts, "exact": False, "price": price}
        elif e["key"] != key:
            e = self.state[name] = {"key": key, "since": ts, "observed_from": e["observed_from"], "exact": True, "price": price}
        return dict(e)


def dash_key(dash):
    """Tracker key: side + whether the dashboard is aligned enough to count (CONFIRMED / BIAS)."""
    if not dash or dash.get("action") == "NO SIGNAL" or not dash.get("side"):
        return "NONE"
    return f"{dash['side']}:{'ALIGNED' if dash.get('level') in AGREE_LEVELS else 'WEAK'}"


def dashboard_summary(dash):
    if not dash:
        return {"available": False, "level": "UNAVAILABLE", "action": "NO SIGNAL", "side": None, "score": None,
                "headline": "Data Check dashboard did not return data.", "checks": []}
    ok = dash.get("action") != "NO SIGNAL"
    return {"available": ok, "level": dash.get("level"), "action": dash.get("action"), "side": dash.get("side"),
            "score": dash.get("score"), "agree": dash.get("agree"), "oppose": dash.get("oppose"), "checks_available": dash.get("available"),
            "headline": dash.get("headline"), "cautions": (dash.get("cautions") or [])[:3],
            "checks": [{"key": c["key"], "name": c["name"], "state": c["state"], "label": c["label"], "weight": c["weight"],
                        "detail": c.get("detail", "")} for c in dash.get("contributions", [])]}


def fuse(a, dash, replay, dash_track, now_dt, market_open, interval, mode="standard"):
    """Combine technical analysis `a` (indicators.analyze output) with the dashboard final signal `dash`
    (dashboard_data.final_signal output). Returns {"gates": [...extra hard gates...], "final": {...}, "timing": {...}, "dashboard": {...}}."""
    now_dt = now_dt.astimezone(IST)
    rules = mode_rules(mode)
    fresh_bars, expire_bars, max_chase = rules["fresh_bars"], rules["expire_bars"], rules["max_chase_atr"]
    side = a.get("side")
    sig = a.get("signal") or "WAIT"
    tech_level = "CONFIRMED" if sig.startswith("CONFIRMED") else "WATCH" if sig.startswith("WATCH") else "WAIT"
    summary = dashboard_summary(dash)
    d_ok = summary["available"]
    d_side, d_level = summary["side"], summary["level"]
    want = 1 if side == "CE" else -1
    agrees = bool(d_ok and side and d_side == side and d_level in AGREE_LEVELS)
    opposes = bool(d_ok and side and d_side and d_side != side and d_level in AGREE_LEVELS + ("WATCH",))

    gates = []
    if side:
        if not d_ok:
            detail = f"Dashboard unavailable ({(dash or {}).get('available', 0)} of 5 checks have data) — the signal cannot be confirmed"
        elif agrees:
            detail = f"Dashboard {d_level} {side} · score {summary['score']:+.0f} · {summary['agree']} of {summary['checks_available']} checks agree"
        elif opposes:
            detail = f"Dashboard leans {d_side} ({d_level}, score {summary['score']:+.0f}) — against the terminal's {side}"
        else:
            detail = f"Dashboard: {summary['action']} (score {summary['score']:+.0f}) — not aligned with {side} yet"
        gates.append({"label": "Data Check dashboard agrees (≥ BIAS, same side)", "pass": agrees, "detail": detail})
        minute = now_dt.hour * 60 + now_dt.minute
        in_window = bool(market_open) and ENTRY_START_MIN <= minute < ENTRY_CUTOFF_MIN
        gates.append({"label": f"Entry time window {ENTRY_START_MIN // 60:02d}:{ENTRY_START_MIN % 60:02d}–{ENTRY_CUTOFF_MIN // 60:02d}:{ENTRY_CUTOFF_MIN % 60:02d} IST",
                      "pass": in_window,
                      "detail": f"Now {now_dt.strftime('%H:%M')} IST" + ("" if market_open else " · market closed") +
                                ("" if in_window or not market_open else " — outside the entry window")})

    precheck = tech_level == "CONFIRMED" and all(g["pass"] for g in gates) and bool(side)
    tech_run = (replay or {}).get("key")
    lean_run = (replay or {}).get("lean")
    timing = {"state": "NONE", "side": side, "generated_at": None, "generated_at_text": None, "age_minutes": None, "age_bars": None,
              "signal_price": None, "price_now": a.get("last_close"), "moved_atr": None, "basis": None, "estimated": False,
              "lean_since_text": lean_run["text"] if lean_run else None, "rules": {
                  "fresh_minutes": fresh_bars * interval, "expire_minutes": expire_bars * interval, "max_chase_atr": max_chase}}

    def describe(since_dt, price, basis, estimated):
        age_min = max(0.0, (now_dt - since_dt).total_seconds() / 60.0)
        atr = a.get("atr")
        moved = ((a.get("last_close") - price) * want / atr) if (atr and price is not None and a.get("last_close") is not None and side) else None
        timing.update({"generated_at": since_dt.astimezone(IST).isoformat(), "generated_at_text": _hhmm(since_dt) + " IST",
                       "age_minutes": round(age_min, 1), "age_bars": round(age_min / interval, 1), "signal_price": price,
                       "moved_atr": round(moved, 2) if moved is not None else None, "basis": basis, "estimated": estimated})
        return age_min, moved

    fresh_gate = None
    if tech_run:
        t_dt = datetime.fromisoformat(tech_run["generated_at"])
        since_dt, price, basis, estimated = t_dt, tech_run["price"], "technical signal (candle close)", False
        if precheck and dash_track:
            d_dt = datetime.fromtimestamp(dash_track["since"], tz=timezone.utc)
            if d_dt > t_dt:
                if dash_track.get("exact"):
                    since_dt, price, basis = d_dt, dash_track.get("price", price), "dashboard alignment (live poll)"
                else:
                    estimated = True       # dashboard was already aligned when tracking began: assume the older time
                    basis = "technical signal (dashboard was already aligned when tracking began)"
        age_min, moved = describe(since_dt, price, basis, estimated)
        if tech_run and replay.get("capped"):
            timing["estimated"] = True
        if precheck:
            if age_min > expire_bars * interval:
                state = "EXPIRED"
            elif age_min > fresh_bars * interval or (moved is not None and moved > max_chase):
                state = "LATE"
            else:
                state = "FRESH"
            timing["state"] = state
            mv = f" · {moved:+.2f} ATR from signal price {price:,.2f}" if moved is not None else ""
            detail = f"Generated {timing['generated_at_text']} · {age_min:.0f} min ago{mv}"
            if state != "FRESH":
                detail += " — don't chase; wait for a new pullback signal"
            fresh_gate = {"label": f"Signal fresh (≤ {fresh_bars * interval} min old, ≤ {max_chase:g} ATR moved)", "pass": state == "FRESH", "detail": detail}
        else:
            timing["state"] = "FORMING"
    elif precheck:   # confirmed but the age cannot be established -> refuse rather than assume fresh
        timing["state"] = "UNKNOWN"
        fresh_gate = {"label": f"Signal fresh (≤ {fresh_bars * interval} min old, ≤ {max_chase:g} ATR moved)", "pass": False,
                      "detail": "Not enough same-session candle history to verify when the signal was generated"}
    if fresh_gate:
        gates.append(fresh_gate)

    all_pass = tech_level == "CONFIRMED" and all(g["pass"] for g in gates)
    dom = max(a.get("bull_score") or 0, a.get("bear_score") or 0)
    dash_part = max(0.0, (summary["score"] or 0) * want) if d_ok and side else 0.0
    confluence = round(0.6 * dom + 0.4 * dash_part) if side and d_ok else (round(0.6 * dom) if side else 0)

    reasons, blockers = [], []
    if side:
        reasons.append(f"Terminal: {sig.replace('_', ' ')} · score {dom}/100 · regime {a.get('regime_label') or a.get('regime')}")
        (reasons if agrees else blockers).append(gates[0]["detail"])
        for g in gates[1:]:
            if not g["pass"]:
                blockers.append(f"{g['label']}: {g['detail']}")
    if all_pass:
        level, action = "CONFIRMED", f"CONFIRMED {side} SETUP"
        headline = f"{side} confirmed by the terminal AND the Data Check dashboard. Signal generated {timing['generated_at_text']} ({timing['age_minutes']:.0f} min ago) — inside the entry window."
    elif tech_level == "CONFIRMED" and opposes:
        level, action = "CONFLICT", f"CONFLICT — terminal {side} vs dashboard {d_side}"
        headline = "Terminal and dashboard disagree on direction. Stand aside until they point the same way."
    elif tech_level == "CONFIRMED" and timing["state"] in ("LATE", "EXPIRED", "UNKNOWN"):
        level, action = "STALE", f"STALE {side} SIGNAL — don't chase"
        headline = f"The {side} setup was generated {timing['generated_at_text'] or 'earlier'} and the entry moment has passed. Wait for the next pullback signal."
    elif tech_level in ("CONFIRMED", "WATCH") and side and not opposes:
        level, action = "WATCH", f"WATCH {side}"
        headline = "Not all conditions line up yet: " + "; ".join(b.split(":")[0] for b in blockers[:3]) if blockers else f"Watching {side}."
        if tech_level == "WATCH":
            headline = f"Terminal gates for {side} are not all met yet — no entry."
    elif tech_level in ("CONFIRMED", "WATCH") and opposes:
        level, action = "CONFLICT", f"CONFLICT — terminal {side} vs dashboard {d_side}"
        headline = "Terminal and dashboard disagree on direction. Stand aside until they point the same way."
    else:
        level, action = "WAIT", "WAIT"
        headline = "No technical setup." + (f" Dashboard leans {d_side} ({d_level}) but a dashboard read alone never creates an entry." if d_ok and d_side and d_level in AGREE_LEVELS else "")
    final = {"level": level, "action": action, "side": side, "confluence_score": confluence, "headline": headline,
             "reasons": reasons, "blockers": blockers, "terminal_signal": sig, "dashboard_level": d_level, "dashboard_side": d_side}
    return {"gates": gates, "final": final, "timing": timing, "dashboard": summary}


def stamp_dashboard_times(tracker, sym, blocks, final, now_ts):
    """Attach 'since' times (IST) to the dashboard's final signal and to each of its checks: the moment the state last
    CHANGED while this server was watching. The first observation after a restart is flagged inexact (the real change
    may be older). Shared by the /dashboard page and the main terminal so both show the same time."""
    price = (blocks.get("zone") or {}).get("price")

    def stamp(name, key):
        e = tracker.update(f"{sym}:{name}", key, price, now_ts)
        dt = datetime.fromtimestamp(e["since"], tz=timezone.utc).astimezone(IST)
        seen = datetime.fromtimestamp(e["observed_from"], tz=timezone.utc).astimezone(IST)
        return {"since": dt.isoformat(), "since_text": dt.strftime("%H:%M:%S") + " IST", "exact": e["exact"],
                "age_minutes": round((now_ts - e["since"]) / 60.0, 1), "observed_from": seen.strftime("%H:%M") + " IST",
                "price_at_change": e.get("price")}

    final["timing"] = stamp("final", f"{final.get('action')}|{final.get('level')}")
    for row in final.get("contributions", []):
        row["timing"] = stamp(row["key"], f"{row['state']}|{row['label']}")
    return final
