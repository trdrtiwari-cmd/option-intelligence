"""v1.20.36: Opening Range Breakout as a live, switchable strategy (per index, Signals page).

Same rule as the Diagnostics test (diagnostics.orb_events, variant "plain"): the high / low of the first `or_minutes` after 09:15 is the
opening range; the FIRST closed candle that closes above it is a CE entry, below it a PE entry; one trade a day; entries until 12:00.
30-day replay (6 Oct - 6 Nov 2026, 5-min): BANKNIFTY ORB 30 - 15 trades, 60% winners, +26 pts each with the live trailing exit,
positive in both halves and with the fixed 1.5 / 1.5 ATR exit, but still inside its noise (±34) - paper-test first.

The live app still adds its own checks on top (Data Check dashboard, entry window, 60 s reconfirm, profit check, daily limits).
"""
from datetime import datetime, timedelta

from indicators import IST

STRATEGIES = {"pullback": "Pullback (EMA 9 retest) - tested on NIFTY",
              "orb30": "Opening Range Breakout 30 min - tested on BANKNIFTY",
              "off": "Off - no BUY signals"}
SESSION_START = 9 * 60 + 15
LAST_ENTRY = 12 * 60          # same as diagnostics.ORB_LAST_ENTRY


def _ist(c):
    dt = c["_dt"]
    return dt.astimezone(IST)


import os

ORB_REARM = os.getenv("ORB_REARM", "0").strip() == "1"     # v1.20.65: after a FAILED break, take the next break (off = tested one-trade-a-day rule)


def _break_failed(c, side, or_high, or_low):
    """A later closed candle that closes back inside the opening range (or beyond it the other way) means the break failed."""
    return c["close"] <= or_high if side == "CE" else c["close"] >= or_low


def find_break(cs, interval=5, or_minutes=30, now=None, rearm=None):
    """cs: CLOSED normalized candles (oldest first). Returns a dict for today's opening range and its first break (or None)."""
    if not cs:
        return None
    day = _ist(cs[-1]).date()
    today = [c for c in cs if _ist(c).date() == day]
    rng = [c for c in today if SESSION_START <= _ist(c).hour * 60 + _ist(c).minute < SESSION_START + or_minutes]
    out = {"day": day.isoformat(), "or_minutes": or_minutes, "complete": False, "or_high": None, "or_low": None, "break": None}
    need = max(1, or_minutes // max(1, interval))
    if len(rng) < need:
        return out
    out.update(complete=True, or_high=max(c["high"] for c in rng), or_low=min(c["low"] for c in rng))
    rearm = ORB_REARM if rearm is None else rearm
    start_i = 0
    while True:
        found = None
        for i in range(start_i, len(today)):
            c = today[i]
            t = _ist(c)
            m = t.hour * 60 + t.minute
            if m < SESSION_START + or_minutes:
                continue
            if m + interval > LAST_ENTRY:
                break
            side = "CE" if c["close"] > out["or_high"] else "PE" if c["close"] < out["or_low"] else None
            if side:
                close_dt = c["_dt"] + timedelta(minutes=interval)
                found = (i, {"side": side, "bar_time": t.strftime("%H:%M"), "close": c["close"], "close_dt": close_dt,
                             "is_last_bar": c is cs[-1], "failed": False, "failed_at": None})
                break
        if not found:
            break
        i, brk = found
        out["break"] = brk
        # v1.20.65: has the break failed since? (checked on every later closed candle, also after 12:00)
        fail_i = None
        for j in range(i + 1, len(today)):
            if _break_failed(today[j], brk["side"], out["or_high"], out["or_low"]):
                brk["failed"], brk["failed_at"], fail_i = True, _ist(today[j]).strftime("%H:%M"), j
                break
        if fail_i is not None and rearm:
            start_i = fail_i + 1             # opt-in: look for the next break after the failure (the latest break wins)
            continue
        break
    return out


def apply(a, cs, interval=5, or_minutes=30, now=None):
    """Turn an analysis into an ORB analysis: the engine's indicators stay for display, the side / signal / gates come from the
    opening-range rule. Returns (analysis, replay) - the replay dict tells the freshness check when the ORB signal started."""
    a = dict(a)
    ob = find_break(cs, interval, or_minutes, now)
    now = now or datetime.now(IST)
    m_now = now.hour * 60 + now.minute
    brk = (ob or {}).get("break")
    rng_txt = (f"{ob['or_low']:,.2f} - {ob['or_high']:,.2f}" if ob and ob.get("complete") else "not complete yet")
    gates = [
        {"label": f"Opening range 09:15-{(SESSION_START + or_minutes) // 60:02d}:{(SESSION_START + or_minutes) % 60:02d} complete",
         "pass": bool(ob and ob.get("complete")), "detail": f"Range {rng_txt}"},
        {"label": "First close beyond the opening range today", "pass": bool(brk),
         "detail": (f"{brk['side']} break at {brk['bar_time']} (close {brk['close']:,.2f})" if brk else "no candle has closed outside the range yet")},
        {"label": "Before 12:00 (ORB entries end)", "pass": m_now < LAST_ENTRY, "detail": f"Now {now.strftime('%H:%M')} IST"},
    ]
    a["strategy"] = "orb30"
    a["strategy_label"] = STRATEGIES["orb30"]
    a["orb"] = {k: v for k, v in (ob or {}).items() if k != "break"}
    if brk:
        a["orb"]["break"] = {k: v for k, v in brk.items() if k != "close_dt"}
    failed = bool(brk and brk.get("failed"))
    past = m_now >= LAST_ENTRY
    gates.append({"label": "Break still holding (no close back inside the range)", "pass": not failed,
                  "detail": (f"Failed at {brk['failed_at']} - closed back inside the range" if failed else "holding")})
    a["gates"] = gates
    # v1.20.65: a break that failed, or an entry window that has ended, no longer gives the terminal a side. Before this, BANKNIFTY
    # still showed the 10:45 PE break at 15:29 while it was 6.7 ATR above it (and a "FORMING" PE timer 285 min old).
    a["side"] = brk["side"] if (brk and not failed and not past) else None
    ok = all(g["pass"] for g in gates)
    a["signal"] = f"CONFIRMED_{brk['side']}" if ok else "WAIT"
    if brk and failed:
        a["orb_failed"] = {"side": brk["side"], "break_time": brk["bar_time"], "failed_at": brk["failed_at"]}
    a["decision"] = (f"ORB {brk['side']} - first close {'above' if brk['side'] == 'CE' else 'below'} the opening range at {brk['bar_time']}"
                     if ok else f"ORB: the {brk['side']} break at {brk['bar_time']} failed ({brk['failed_at']}: closed back inside the range) - no side" if failed
                     else "ORB: entry window over (12:00) - no side" if (brk and past)
                     else "ORB: waiting for the first close outside the opening range" if (ob and ob.get("complete") and not brk)
                     else "ORB: today's break already happened - one trade a day" if brk else "ORB: opening range not complete")
    replay = {"key": {"generated_at": brk["close_dt"].astimezone(IST).isoformat(), "price": brk["close"],
                      "text": brk["close_dt"].astimezone(IST).strftime("%H:%M") + " IST"}, "lean": None} if brk else {}
    return a, replay
