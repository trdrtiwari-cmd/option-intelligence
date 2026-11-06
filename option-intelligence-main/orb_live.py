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


def find_break(cs, interval=5, or_minutes=30, now=None):
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
    for c in today:
        t = _ist(c)
        m = t.hour * 60 + t.minute
        if m < SESSION_START + or_minutes:
            continue
        if m + interval > LAST_ENTRY:
            break
        side = "CE" if c["close"] > out["or_high"] else "PE" if c["close"] < out["or_low"] else None
        if side:
            close_dt = c["_dt"] + timedelta(minutes=interval)
            out["break"] = {"side": side, "bar_time": t.strftime("%H:%M"), "close": c["close"], "close_dt": close_dt,
                            "is_last_bar": c is cs[-1]}
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
    a["gates"] = gates
    a["side"] = brk["side"] if brk else None
    ok = all(g["pass"] for g in gates)
    a["signal"] = f"CONFIRMED_{brk['side']}" if ok else "WAIT"
    a["decision"] = (f"ORB {brk['side']} - first close {'above' if brk['side'] == 'CE' else 'below'} the opening range at {brk['bar_time']}"
                     if ok else "ORB: waiting for the first close outside the opening range" if (ob and ob.get("complete") and not brk)
                     else "ORB: today's break already happened - one trade a day" if brk else "ORB: opening range not complete")
    replay = {"key": {"generated_at": brk["close_dt"].astimezone(IST).isoformat(), "price": brk["close"],
                      "text": brk["close_dt"].astimezone(IST).strftime("%H:%M") + " IST"}, "lean": None} if brk else {}
    return a, replay
