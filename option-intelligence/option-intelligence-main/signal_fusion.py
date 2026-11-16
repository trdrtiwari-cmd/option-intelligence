"""Signal fusion (v1.9): main-terminal technical signal + Data Check dashboard signal + signal TIMING.

Pure functions (no web-framework imports) so they can be unit-tested.

Why timing matters: a confirmed setup is only worth entering close to the moment it formed. Entering 30 minutes later
means buying after the move (stale signal), after the dashboard flipped, or in the wrong part of the session.
Every signal therefore carries the time it was generated, its age, and how far price travelled since.
"""
import os
from datetime import datetime, timedelta, timezone

from indicators import IST, analyze, drop_forming, mode_rules, normalize_candles

# ---- tunable rules (starting values - review them against your paper-trade CSV) ----
FRESH_BARS = 3            # a signal older than this many candles is LATE (3 x 5m = 15 min)
EXPIRE_BARS = 12          # older than this many candles it is EXPIRED and must re-qualify (12 x 5m = 60 min)
MAX_CHASE_ATR = 1.0       # price already moved more than this many ATR from the signal price => don't chase
REPLAY_BARS = 40          # how far back the replay looks for the start of the current signal
ENTRY_START_MIN = 9 * 60 + 45     # no new entries before 09:45 IST (opening volatility; replay: every 09:15-09:45 entry lost, 09:45-10:30 averaged +9 pts)
def _env_minutes(name, default):
    """HH:MM from the environment (e.g. ENTRY_CUTOFF=13:30) -> minutes after midnight."""
    try:
        h, m = os.getenv(name, "").strip().split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return default


# 14 days of replayed signals (v1.11 diagnostics): entries before 12:00 made money, entries after 13:30 lost in both modes.
# Override with ENTRY_CUTOFF=15:00 (or any HH:MM) in the environment if your own diagnostics say otherwise.
ENTRY_CUTOFF_MIN = _env_minutes("ENTRY_CUTOFF", 13 * 60 + 30)


def _parse_windows(text):
    out = []
    for part in (text or "").split(","):
        try:
            a, b = part.strip().split("-")
            ha, ma = a.split(":"); hb, mb = b.split(":")
            out.append((int(ha) * 60 + int(ma), int(hb) * 60 + int(mb)))
        except ValueError:
            continue
    return out


# v1.19.5: entries only in the two parts of the session that paid in EVERY replay file (5 Oct - 4 Nov 2026, NIFTY 5m):
#   09:45-12:00  scalp 27 trades 70 % winners +11.2 pts | standard 17 trades 65 % +8.0
#   14:30-15:00  scalp  7 trades 71 % +11.7            | standard  5 trades 80 % +16.0
# and none in the two that lost in every file:
#   12:00-13:30  scalp 53 % +2.5 (second half 44 %, -1.8) | standard 42 % -3.2 (second half 29 %, -9.7)
#   13:30-14:30  scalp 36 % -5.8                          | standard 31 % -8.4
# ENTRY_WINDOWS="09:45-12:00,14:30-15:00" in the environment changes them (old single cutoff: ENTRY_CUTOFF still works if ENTRY_WINDOWS is unset).
ENTRY_WINDOWS = _parse_windows(os.getenv("ENTRY_WINDOWS", "")) or (
    [(ENTRY_START_MIN, ENTRY_CUTOFF_MIN)] if os.getenv("ENTRY_CUTOFF", "").strip() else [(9 * 60 + 45, 12 * 60), (14 * 60 + 30, 15 * 60)])


def in_entry_window(minute):
    return any(a <= minute < b for a, b in ENTRY_WINDOWS)


def entry_windows_text():
    return ", ".join(f"{a // 60:02d}:{a % 60:02d}–{b // 60:02d}:{b % 60:02d}" for a, b in ENTRY_WINDOWS)
AGREE_LEVELS = ("CONFIRMED", "BIAS")
try:
    SCORE_CAP = int(os.getenv("SCORE_CAP", "0") or 0)      # 0 = no upper score limit (v1.19); e.g. 90 restores the old cap
except ValueError:
    SCORE_CAP = 0   # dashboard levels that count as "agrees"


def blocker_label(text):
    """Rule name of a blocker string "label: detail". v1.20.71: the label itself may contain ': ' inside brackets
    ("Reconfirmed (60s hold: setup persists, price holds)") or clock times ("09:45"), so cut at the first ': ' that
    is outside brackets - not at the first colon."""
    s, depth = str(text), 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0 and s[i + 1:i + 2] == " ":
            if s[:i] == "Profit check":        # "Profit check: <rule>: detail" - the rule name is the second part
                rest = blocker_label(s[i + 2:])
                return ("Profit check: " + rest).strip()
            return s[:i].strip()
    return s.strip()


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


RECONFIRM_SECONDS = int(os.getenv("RECONFIRM_SECONDS", "60") or 0)       # default hold time; 0 turns reconfirmation off
RECONFIRM_MAX_ADVERSE_ATR = float(os.getenv("RECONFIRM_MAX_ADVERSE_ATR", "0.5") or 0.5)   # price may not fall this far against the signal
RECONFIRM_GRACE = float(os.getenv("RECONFIRM_GRACE_SECONDS", "20") or 20)  # a setup may vanish this long without resetting the timer
RECONFIRM_MIN_POLLS = 2


class ReconfirmTracker:
    """Second look at a freshly generated setup (v1.14). A setup that passes every gate does NOT become CONFIRMED at once:
    it is PENDING until it has (a) kept passing every gate for `window` seconds across at least two looks and (b) price has
    not moved more than `max_adverse_atr` ATR against the signal price. If the setup vanishes (beyond a short grace) the
    clock resets; if price moves against it the attempt FAILS and stays failed until the setup disappears and re-forms.
    Pure Python / in-memory, one entry per symbol|interval|mode. update() returns an `event` exactly once per transition."""

    def __init__(self):
        self.state = {}

    def update(self, name, cand, side, price, atr, ts, window, max_adverse_atr=None, grace=None, min_polls=None):
        max_adv = RECONFIRM_MAX_ADVERSE_ATR if max_adverse_atr is None else max_adverse_atr
        grace = RECONFIRM_GRACE if grace is None else grace
        min_polls = RECONFIRM_MIN_POLLS if min_polls is None else min_polls
        e = self.state.get(name)
        if window <= 0:
            self.state.pop(name, None)
            return {"state": "OFF", "window": 0, "event": None}
        if not cand:
            if e is not None:
                e.setdefault("lost_at", ts)
                if ts - e["lost_at"] > grace:
                    self.state.pop(name, None)
                    return {"state": "NONE", "window": window, "event": "LOST" if e["state"] in ("PENDING", "CONFIRMED") else None, "side": e.get("side")}
            return {"state": "NONE", "window": window, "event": None}
        event = None
        if e is not None and e.get("side") != side:
            e = None                                     # the setup flipped CE <-> PE: a brand-new attempt
        if e is None:
            e = self.state[name] = {"state": "PENDING", "side": side, "since": ts, "price0": price, "worst": 0.0, "polls": 0, "last_price": price}
            event = "STARTED"
        e.pop("lost_at", None)
        e["polls"] += 1
        e["last_price"] = price
        want = 1 if side == "CE" else -1
        adverse = ((e["price0"] - price) * want / atr) if (atr and price is not None and e["price0"] is not None) else 0.0
        e["worst"] = max(e["worst"], adverse)
        elapsed = ts - e["since"]
        if e["state"] == "PENDING":
            if e["worst"] > max_adv:
                e["state"], e["failed_at"] = "FAILED", ts
                e["reason"] = f"price moved {e['worst']:.2f} ATR against the signal price within {elapsed:.0f}s (limit {max_adv:g} ATR)"
                event = "FAILED"
            elif elapsed >= window and e["polls"] >= min_polls:
                e["state"], e["confirmed_at"], e["confirmed_price"] = "CONFIRMED", ts, price
                event = "CONFIRMED"
        return {"state": e["state"], "side": side, "window": window, "elapsed": round(elapsed, 1), "remaining": round(max(0.0, window - elapsed), 1),
                "since": e["since"], "price0": e["price0"], "price_now": price, "worst_adverse_atr": round(e["worst"], 2), "adverse_now_atr": round(adverse, 2),
                "limit_atr": max_adv, "polls": e["polls"], "confirmed_at": e.get("confirmed_at"), "reason": e.get("reason"), "event": event}


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


def signal_age_limits(rules, interval):
    """(fresh, expire) minutes for a signal. The bar counts were tuned on 5-minute candles; on 15 / 30-minute candles the same counts
    meant a setup stayed 'fresh' for 45 min and 'live' for 3 hours (v1.20.12). Keep the 5-minute time limits, but never less than
    2 bars fresh / 4 bars to expire on slower candles."""
    iv = max(1, int(interval or 5))
    fresh = min(rules["fresh_bars"] * iv, max(rules["fresh_bars"] * 5, 2 * iv))
    expire = min(rules["expire_bars"] * iv, max(rules["expire_bars"] * 5, 4 * iv))
    return fresh, max(expire, fresh)


def fuse(a, dash, replay, dash_track, now_dt, market_open, interval, mode="standard", reconfirm=None, daily_losses=None, max_daily_losses=0,
         side_buys=None, current_bar=None, day_pnl_rs=None, daily_target_rs=0, symbol_enabled=True, extra_gates=None):
    """Combine technical analysis `a` (indicators.analyze output) with the dashboard final signal `dash`
    (dashboard_data.final_signal output). `reconfirm` = {"tracker", "name", "window", "price", "ts"} switches on the hold-and-recheck
    step: a setup that passes every other gate is PENDING until the tracker has seen it hold for `window` seconds. Returns {"gates": [...extra hard gates...], "final": {...}, "timing": {...}, "dashboard": {...}}."""
    now_dt = now_dt.astimezone(IST)
    rules = mode_rules(mode)
    fresh_bars, expire_bars, max_chase = rules["fresh_bars"], rules["expire_bars"], rules["max_chase_atr"]
    fresh_min, expire_min = signal_age_limits(rules, interval)
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
        in_window = bool(market_open) and in_entry_window(minute)
        gates.append({"label": f"Entry time window {entry_windows_text()} IST",
                      "pass": in_window,
                      "detail": f"Now {now_dt.strftime('%H:%M')} IST" + ("" if market_open else " · market closed") +
                                ("" if in_window or not market_open else " — outside the entry window")})

    precheck = tech_level == "CONFIRMED" and all(g["pass"] for g in gates) and bool(side)
    tech_run = (replay or {}).get("key")
    lean_run = (replay or {}).get("lean")
    timing = {"state": "NONE", "side": side, "generated_at": None, "generated_at_text": None, "age_minutes": None, "age_bars": None,
              "signal_price": None, "price_now": a.get("last_close"), "moved_atr": None, "basis": None, "estimated": False,
              "lean_since_text": lean_run["text"] if lean_run else None, "session_day": None, "rules": {
                  "fresh_minutes": fresh_min, "expire_minutes": expire_min, "max_chase_atr": max_chase}}

    def describe(since_dt, price, basis, estimated):
        age_min = max(0.0, (now_dt - since_dt).total_seconds() / 60.0)
        atr = a.get("atr")
        moved = ((a.get("last_close") - price) * want / atr) if (atr and price is not None and a.get("last_close") is not None and side) else None
        # v1.20.71: before the open the last signal is from the previous session - say which day, not "2,446 min ago"
        prev = since_dt.astimezone(IST).date() != now_dt.astimezone(IST).date()
        timing["session_day"] = since_dt.astimezone(IST).strftime("%d %b") if prev else None
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
            if age_min > expire_min:
                state = "EXPIRED"
            elif age_min > fresh_min or (moved is not None and moved > max_chase):
                state = "LATE"
            else:
                state = "FRESH"
            timing["state"] = state
            mv = f" · {moved:+.2f} ATR from signal price {price:,.2f}" if moved is not None else ""
            detail = f"Generated {timing['generated_at_text']} · {age_min:.0f} min ago{mv}"
            if state != "FRESH":
                detail += " — don't chase; wait for a new pullback signal"
            fresh_gate = {"label": f"Signal fresh (≤ {fresh_min} min old, ≤ {max_chase:g} ATR moved)", "pass": state == "FRESH", "detail": detail}
        else:
            timing["state"] = "FORMING"
    elif precheck:   # confirmed but the age cannot be established -> refuse rather than assume fresh
        timing["state"] = "UNKNOWN"
        fresh_gate = {"label": f"Signal fresh (≤ {fresh_min} min old, ≤ {max_chase:g} ATR moved)", "pass": False,
                      "detail": "Not enough same-session candle history to verify when the signal was generated"}
    if fresh_gate:
        gates.append(fresh_gate)

    if side and SCORE_CAP:
        # v1.19: OFF by default. The old "score < 90" gate (from one 51-trade replay) blocked exactly the strongest trend moments -
        # e.g. 4 Nov 2026: NIFTY UPTREND breakout, score 92/100, decision WAIT (this gate alone fails at any score ≥ 90). Over-extension is already handled by the
        # RSI ceiling, the "not chasing (ATR beyond EMA 9)" gate and the freshness gate. SCORE_CAP=90 in the environment restores it.
        dom_score = max(a.get("bull_score") or 0, a.get("bear_score") or 0)
        gates.append({"label": f"Avoid extreme score bucket (score < {SCORE_CAP})", "pass": dom_score < SCORE_CAP,
                      "detail": f"Technical score {dom_score}/100 (SCORE_CAP={SCORE_CAP})."})
    # (v1.19) the separate "before 13:30 IST" gate was removed: it duplicated the entry-time-window gate above (ENTRY_CUTOFF).

    if side and daily_losses is not None and max_daily_losses and max_daily_losses > 0:
        # v1.15: after N stop-outs the day is done. In the replay one day (9 signals, 1 winner, -166 pts) wiped out 2.6x the whole
        # period's profit; stopping after 2 losses cut that day's damage and lifted the 11-day total from +63 to +140 pts (scalp).
        gates.append({"label": f"Daily loss limit (max {max_daily_losses} stop-outs per day)", "pass": daily_losses < max_daily_losses,
                      "detail": f"{daily_losses} stop-out(s) logged today" + ("" if daily_losses < max_daily_losses else " — no more entries today; the market is not paying this setup")})
    if side and side_buys is not None:
        # v1.20.23: only the first BUY per side per day. Replay (6 Oct - 5 Nov, NIFTY 5m standard): 1st same-side signal 9 trades 89% / +21.5 pts,
        # 2nd or later 5 trades 40% / -4.0. The bar that produced today's first BUY still passes, so that BUY is not cancelled after it is logged.
        got = side_buys.get(side) or {}
        n_ = int(got.get("count") or 0)
        same_bar = bool(current_bar) and n_ == 1 and str(got.get("first") or "") == str(current_bar)
        gates.append({"label": "One trade per side per day", "pass": n_ == 0 or same_bar,
                      "detail": (f"no {side} BUY yet today" if n_ == 0 else "this is today's first " + side + " BUY") if (n_ == 0 or same_bar)
                                else f"{n_} {side} BUY already taken today (first at {got.get('first')}) — later same-side signals lost in the replay"})
    if side and not symbol_enabled:
        # v1.20.35: this index is switched off for BUY signals (Signals page). BANKNIFTY, 6 Oct-6 Nov replay: 13 signals, 2 winners,
        # price went ~2.3 ATR against the entry before moving its way; worse than random entries in both halves and with both value sets.
        gates.append({"label": "Index enabled for BUY signals", "pass": False,
                      "detail": "off on the Signals page - in the 30-day replay its signals lost more than random entries; analysis still shown"})
    if side and daily_target_rs and day_pnl_rs is not None:
        # v1.20.28: a small buyer's daily goal - once today's booked profit reaches it, no new BUYs (protects the day's profit)
        hit = day_pnl_rs >= daily_target_rs
        gates.append({"label": f"Daily profit target not reached (₹{daily_target_rs:,.0f})", "pass": not hit,
                      "detail": (f"booked today ₹{day_pnl_rs:,.0f} - target reached, done for the day" if hit
                                 else f"booked today ₹{day_pnl_rs:,.0f} of ₹{daily_target_rs:,.0f}")})
    if side and extra_gates:
        gates.extend(g for g in extra_gates if g)        # v1.20.38: decision-flow gates (re-entry, S/R room, data freshness)
    rc = None
    cand = bool(side) and tech_level == "CONFIRMED" and all(g["pass"] for g in gates)
    if reconfirm:
        price_now = reconfirm.get("price") if reconfirm.get("price") is not None else a.get("last_close")
        rc = reconfirm["tracker"].update(reconfirm["name"], cand, side, price_now, a.get("atr"), reconfirm["ts"], reconfirm["window"])
        if cand and rc["state"] in ("PENDING", "FAILED", "CONFIRMED"):
            w = rc["window"]
            mv = f"price {rc['adverse_now_atr']:+.2f} ATR against, worst {rc['worst_adverse_atr']:.2f} (limit {rc['limit_atr']:g})"
            if rc["state"] == "PENDING":
                detail = f"Holding {rc['elapsed']:.0f}s of {w}s · {rc['remaining']:.0f}s left · {mv}"
            elif rc["state"] == "FAILED":
                detail = f"Failed — {rc['reason']}. Wait for the setup to re-form."
            else:
                detail = f"Held {w}s with the setup intact · {mv}"
            gates.append({"label": f"Reconfirmed ({w}s hold: setup persists, price holds)", "pass": rc["state"] == "CONFIRMED", "detail": detail})
    all_pass = tech_level == "CONFIRMED" and all(g["pass"] for g in gates)
    dom = max(a.get("bull_score") or 0, a.get("bear_score") or 0)
    dash_part = max(0.0, (summary["score"] or 0) * want) if d_ok and side else 0.0
    confluence = round(0.6 * dom + 0.4 * dash_part) if side and d_ok else (round(0.6 * dom) if side else 0)

    reasons, blockers = [], []
    if side:
        reasons.append(f"Terminal: {sig.replace('_', ' ')} · score {dom}/100 · regime {a.get('regime_label') or a.get('regime')}")
        for g in gates:
            if not g["pass"]:
                blockers.append(f"{g['label']}: {g['detail']}")
        if agrees:
            reasons.append(gates[2]["detail"] if len(gates) > 2 else "Data Check agrees with the technical side.")
    if all_pass:
        level, action = "CONFIRMED", f"CONFIRMED {side} SETUP"
        headline = f"{side} confirmed by the terminal AND the Data Check dashboard" + (f", and it held for {rc['window']}s." if rc and rc.get("state") == "CONFIRMED" else ".") + f" Signal generated {timing['generated_at_text']} ({timing['age_minutes']:.0f} min ago) — inside the entry window."
    elif cand and rc and rc["state"] == "PENDING":
        level, action = "PENDING", f"RECONFIRMING {side} — {rc['remaining']:.0f}s left"
        headline = f"{side} setup detected at {timing['generated_at_text'] or 'now'}. Not an entry yet: it must hold for {rc['window']}s ({rc['remaining']:.0f}s left) with price not moving {rc['limit_atr']:g} ATR against it."
    elif cand and rc and rc["state"] == "FAILED":
        level, action = "WATCH", f"WATCH {side} — reconfirmation failed"
        headline = f"The {side} setup did not hold: {rc['reason']}. No entry; wait for a fresh setup."
    elif tech_level == "CONFIRMED" and opposes:
        level, action = "CONFLICT", f"CONFLICT — terminal {side} vs dashboard {d_side}"
        headline = "Terminal and dashboard disagree on direction. Stand aside until they point the same way."
    elif tech_level == "CONFIRMED" and timing["state"] in ("LATE", "EXPIRED", "UNKNOWN"):
        level, action = "STALE", f"STALE {side} SIGNAL — don't chase"
        headline = f"The {side} setup was generated {timing['generated_at_text'] or 'earlier'} and the entry moment has passed. Wait for the next pullback signal."
    elif tech_level in ("CONFIRMED", "WATCH") and side and not opposes:
        level, action = "WATCH", f"WATCH {side}"
        headline = "Not all conditions line up yet: " + "; ".join(blocker_label(b) for b in blockers[:3]) if blockers else f"Watching {side}."
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
    return {"gates": gates, "final": final, "timing": timing, "dashboard": summary, "reconfirm": rc}


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
