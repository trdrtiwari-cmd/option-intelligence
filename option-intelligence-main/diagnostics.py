"""Signal diagnostics (v1.11): "where do the corrections need to be?" answered from data, not opinion.

It replays the technical engine (indicators.analyze) bar by bar over historical candles - exactly as it would have run live,
using only candles that had closed at that moment - then measures what the market did AFTER every signal:

  * CONFIRMED signals    : the entries the terminal would have told you to take.
  * Near-misses          : WATCH signals that failed exactly ONE gate (which gate blocked an entry that might have paid?).
  * Exit grid            : target x stop-loss combinations in index points -> which exit rule fits these signals.
  * Breakdowns           : time of day, side, regime, score, RSI, stretch from EMA 9, entry trigger, day.

All results are in INDEX POINTS after a round-trip cost (spread + slippage + decay allowance), conservative on ties
(if stop and target both fall inside one candle, the stop is counted first). Entry = open of the candle after the signal.
Pure Python, no web-framework imports.

Not covered (no history exists for them): the Data Check dashboard (breadth, sectors, option-premium checks) and option
prices. The signal journal (v1.11) records those going forward so they can be evaluated later.
"""
import json
import math
import sys
from datetime import timedelta

import dashboard_data as dd
from indicators import IST, analyze, normalize_candles, efficiency_ratio
import signal_fusion as _sf

TARGET_GRID = (15, 20, 25, 30, 40, 60)
STOP_GRID = (10, 15, 20, 25, 30, 40)
ATR_TARGETS = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0)     # ATR-multiple exits (what the Data Check dashboard now uses)
ATR_STOPS = (0.5, 0.75, 1.0, 1.5, 2.0)
HOUR_BUCKETS = (("09:15–09:45", 555, 585), ("09:45–10:30", 585, 630), ("10:30–12:00", 630, 720),
                ("12:00–13:30", 720, 810), ("13:30–14:30", 810, 870), ("14:30–15:30", 870, 931))
SQUAREOFF_MIN = 15 * 60 + 20   # live time exit
MIN_N = 8          # fewer samples than this are shown but never used to draw a conclusion


# ---------------------------------------------------------------- replay
def replay(raw_candles, symbol="NIFTY", interval=5, mode="scalp", htf_raw=None, htf_interval=None, volume_source=None, first_bar=40, tune=None,
           window=None, progress=None, score_min=60, live_window=True, pattern_minutes=None):
    """One record per closed bar from `first_bar` on: the technical signal as it stood when that bar closed.
    live_window (v1.19.1): apply the live app's entry-time window (ENTRY_START_MIN..ENTRY_CUTOFF) so the replay only counts
    entries the app would actually have signalled. Before this, entries at 09:15-09:45 and after 13:30 were counted too."""
    cs = normalize_candles(raw_candles)
    htf = normalize_candles(htf_raw) if htf_raw and htf_interval else []
    window = window or int(10 * 375 / max(1, interval))      # the live terminal analyses ~10 days of candles; use the same lookback
    out, h = [], 0
    for i in range(first_bar, len(cs)):
        if progress and (i - first_bar) % 25 == 0:
            progress(i - first_bar, len(cs) - first_bar)
        close_dt = cs[i]["_dt"] + timedelta(minutes=interval)
        while h < len(htf) and htf[h]["_dt"] + timedelta(minutes=htf_interval) <= close_dt:
            h += 1
        sub = htf[:h]
        a = analyze(cs[max(0, i + 1 - window):i + 1], symbol, interval, htf_raw=sub or None, htf_interval=htf_interval if sub else None,
                    volume_source=volume_source, now=close_dt, mode=mode, score_min=score_min, pattern_minutes=pattern_minutes,
                    trend_entry_variant="off", tune=tune)   # v1.20.28: replay the base rules; strong-trend entries are measured as a what-if
        if a.get("status") != "ok":
            continue
        side, atr = a.get("side"), a.get("atr")
        gates = list(a.get("gates") or [])
        if live_window and side:
            m = close_dt.astimezone(IST).hour * 60 + close_dt.astimezone(IST).minute
            ok = _sf.in_entry_window(m)
            gates.append({"label": "Entry time window (live app)", "pass": ok, "detail": ""})
            if not ok and a["signal"].startswith("CONFIRMED"):
                a = dict(a, signal="WATCH_" + side)
        failed = [g["label"] for g in gates if not g["pass"]]
        trig = next((g for g in gates if g["label"].startswith(("Entry trigger", "Pullback-and-resume"))), None)
        trigger = None
        if trig and trig["pass"]:
            trigger = "pause break" if trig["detail"].startswith("Pause break") else "EMA 9 retest"
        stretch = None
        if side and atr:
            stretch = ((a["ema9"] - a["last_close"]) if side == "PE" else (a["last_close"] - a["ema9"])) / atr
        rsi = a.get("rsi")
        cpat = a.get("chart_pattern") or {}
        out.append({"i": i, "signal": a["signal"], "side": side, "score": a["score"], "regime": a.get("regime_label"), "phase": a.get("regime_phase"),
                    "pattern": cpat.get("name"), "pattern_side": cpat.get("side"), "pattern_stage": cpat.get("stage"), "pe": a.get("pattern_effects"),
                    "failed": failed, "trigger": trigger, "stretch_atr": stretch, "atr": atr,
                    "rsi_ext": (rsi if side == "PE" else 100 - rsi) if (rsi is not None and side) else None,
                    "rsi": rsi, "vwap": a.get("vwap") if a.get("vwap") is not None else a.get("vwap_approx")})
    return cs, out


def events_from(replayed):
    """Run starts: first bar of an unbroken CONFIRMED run, and first bar of each 'WATCH with exactly one failed gate' run."""
    confirmed, near = [], []
    prev = None
    for r in replayed:
        key = (r["signal"], tuple(r["failed"]) if r["signal"].startswith("WATCH") else ())
        if prev is None or key != prev[0] or r["i"] != prev[1] + 1:
            if r["signal"].startswith("CONFIRMED"):
                confirmed.append(r)
            elif r["signal"].startswith("WATCH") and len(r["failed"]) == 1:
                near.append({**r, "gate": r["failed"][0]})
        prev = (key, r["i"])
    return confirmed, near


# ---------------------------------------------------------------- outcomes
def _path(cs, ev, interval, horizon):
    """Candles after the signal bar, same session only. Entry = open of the next candle."""
    i = ev["i"]
    if i + 1 >= len(cs):
        return None, None
    day = cs[i]["_dt"].astimezone(IST).date()
    if cs[i + 1]["_dt"].astimezone(IST).date() != day:
        return None, None
    path = []
    for j in range(i + 1, min(len(cs), i + 1 + horizon)):
        jt = cs[j]["_dt"].astimezone(IST)
        if jt.date() != day or jt.hour * 60 + jt.minute >= SQUAREOFF_MIN:     # v1.19.5: the live app squares off at 15:20
            break
        path.append(cs[j])
    return (cs[i + 1]["open"], path) if path else (None, None)


def first_touch(path, entry, d, target, stop):
    """Walk the candles; stop wins ties inside one candle. Returns (points, reason)."""
    for c in path:
        adverse = (entry - c["low"]) if d > 0 else (c["high"] - entry)
        fav = (c["high"] - entry) if d > 0 else (entry - c["low"])
        if adverse >= stop:
            return -float(stop), "stop"
        if fav >= target:
            return float(target), "target"
    return d * (path[-1]["close"] - entry), "time"


def managed_exit(path, entry, d, stop, activate=None, trail=None, half_at=None):
    """Stop-loss that trails. Before activation the stop sits `stop` points away. After the move reaches `activate` points
    (or, with half_at, after half the position is sold at +half_at and the rest moves to breakeven) the stop trails
    `trail` points behind the best price. Adverse moves are checked before favourable ones inside a candle (conservative).
    Returns (points per full unit, reason)."""
    mfe, level, banked, half_done = 0.0, -float(stop), 0.0, False
    for c in path:
        worst = (c["low"] - entry) if d > 0 else (entry - c["high"])
        best = (c["high"] - entry) if d > 0 else (entry - c["low"])
        if worst <= level:
            rem = 0.5 if half_done else 1.0
            return banked + rem * level, ("trail" if level > 0 else "stop")
        mfe = max(mfe, best)
        if half_at is not None and not half_done and mfe >= half_at:
            half_done, banked, level = True, 0.5 * half_at, max(level, 0.0)
        if trail is not None and (half_done or (half_at is None and activate is not None and mfe >= activate)):
            level = max(level, mfe - trail)
    rem = 0.5 if half_done else 1.0
    return banked + rem * d * (path[-1]["close"] - entry), "time"


STRATEGIES = (
    ("Fixed target 30 / stop 20", lambda p, e, d: first_touch(p, e, d, 30, 20)[0]),
    ("Fixed target 40 / stop 15", lambda p, e, d: first_touch(p, e, d, 40, 15)[0]),
    ("Fixed target 60 / stop 15", lambda p, e, d: first_touch(p, e, d, 60, 15)[0]),
    ("Trailing: stop 20, trail 20 once +20", lambda p, e, d: managed_exit(p, e, d, 20, activate=20, trail=20)[0]),
    ("Trailing: stop 15, trail 15 once +20", lambda p, e, d: managed_exit(p, e, d, 15, activate=20, trail=15)[0]),
    ("Trailing: stop 20, trail 25 once +30", lambda p, e, d: managed_exit(p, e, d, 20, activate=30, trail=25)[0]),
    ("Scale out: ½ at +30, rest trails 20 (stop 20)", lambda p, e, d: managed_exit(p, e, d, 20, trail=20, half_at=30)[0]),
    ("Scale out: ½ at +30, rest trails 25 (stop 15)", lambda p, e, d: managed_exit(p, e, d, 15, trail=25, half_at=30)[0]),
    ("Scale out: ½ at +40, rest trails 30 (stop 20)", lambda p, e, d: managed_exit(p, e, d, 20, trail=30, half_at=40)[0]),
)


# v1.19.6: the same exit styles in ATR units, all keeping the 1.5 ATR minimum stop the live plan uses. Compare each with
# the live plan (stop 1.5 / target 1.5 ATR) - a trailing variant only replaces the live plan if it wins in BOTH halves.
ATR_STRATEGIES = (
    ("ATR trail: stop 1.5, trail 1.0 once +1.0", lambda p, e, d, a: managed_exit(p, e, d, 1.5 * a, activate=1.0 * a, trail=1.0 * a)[0]),
    ("ATR trail: stop 1.5, trail 1.0 once +1.5", lambda p, e, d, a: managed_exit(p, e, d, 1.5 * a, activate=1.5 * a, trail=1.0 * a)[0]),
    ("ATR trail: stop 1.5, trail 0.75 once +1.2", lambda p, e, d, a: managed_exit(p, e, d, 1.5 * a, activate=1.2 * a, trail=0.75 * a)[0]),
    ("ATR scale out: ½ at +1.5, rest trails 1.0 (stop 1.5)", lambda p, e, d, a: managed_exit(p, e, d, 1.5 * a, trail=1.0 * a, half_at=1.5 * a)[0]),
)


def excursions(path, entry, d):
    mfe = max((c["high"] - entry) if d > 0 else (entry - c["low"]) for c in path)
    mae = max((entry - c["low"]) if d > 0 else (c["high"] - entry) for c in path)
    return max(mfe, 0.0), max(mae, 0.0)


def stats(pnls):
    n = len(pnls)
    if not n:
        return {"n": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    mean = sum(pnls) / n
    sd = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (n - 1)) if n > 1 else 0.0
    gl = abs(sum(losses))
    return {"n": n, "win_rate": round(100.0 * len(wins) / n, 1), "avg": round(mean, 2), "total": round(sum(pnls), 1),
            "pf": round(sum(wins) / gl, 2) if gl else None, "se": round(sd / math.sqrt(n), 2) if n > 1 else None}


def evaluate(cs, events, interval, horizon, cost, target, stop, with_extras=False):
    """Outcome record for every event (adds pnl at the baseline exit, excursions and the whole target x stop grid)."""
    rows = []
    for ev in events:
        entry, path = _path(cs, ev, interval, horizon)
        if path is None:
            continue
        d = 1 if ev["side"] == "CE" else -1
        mfe, mae = excursions(path, entry, d)
        pnl, why = first_touch(path, entry, d, target, stop)
        grid = {f"{t}/{s}": first_touch(path, entry, d, t, s)[0] - cost for t in TARGET_GRID for s in STOP_GRID}
        strat = {name: fn(path, entry, d) - cost for name, fn in STRATEGIES} if with_extras else {}
        atr_v = ev.get("atr")
        if with_extras and atr_v:
            strat.update({name: fn(path, entry, d, atr_v) - cost for name, fn in ATR_STRATEGIES})
        atr_grid = {f"{t}/{s_}": first_touch(path, entry, d, t * atr_v, s_ * atr_v)[0] - cost
                    for t in ATR_TARGETS for s_ in ATR_STOPS} if atr_v else {}
        dt = cs[ev["i"]]["_dt"].astimezone(IST)
        zone = vs_prev = None
        if with_extras:
            try:
                z = dd.index_zone(cs[max(0, ev["i"] - 600):ev["i"] + 1], cs[ev["i"]]["close"], "Index")
                if z.get("status") == "OK":
                    want = "UP" if ev["side"] == "CE" else "DOWN"
                    zone = "agrees (" + want + ")" if z["zone"] == want else "MID" if z["zone"] == "MID" else "opposes"
                    vs_prev = "with prev-close side" if (cs[ev["i"]]["close"] - z["levels"]["prev_close"]) * d > 0 else "against prev-close side"
            except Exception:
                pass
        rows.append({**ev, "day": dt.strftime("%Y-%m-%d"), "minute": dt.hour * 60 + dt.minute, "hhmm": dt.strftime("%H:%M"),
                     "entry": entry, "mfe": mfe, "mae": mae, "pnl": pnl - cost, "exit": why, "grid": grid, "bars": len(path),
                     "strat": strat, "zone": zone, "vs_prev": vs_prev, "atr_grid": atr_grid})
    return rows


def random_entry_baseline(cs, interval, horizon, cost, target, stop, first_bar=40, n=800, seed=7):
    """What random entries (random bar, random CE/PE side) earn with the same exit rule on the same candles.
    Signals only have an edge to the extent they beat THIS number, not zero."""
    import random
    rnd = random.Random(seed)
    idx = list(range(first_bar, len(cs) - 2))
    if not idx:
        return {"n": 0}
    pnls = []
    for i in rnd.sample(idx, min(n, len(idx))):
        side = "CE" if rnd.random() < 0.5 else "PE"
        entry, path = _path(cs, {"i": i}, interval, horizon)
        if path is None:
            continue
        pnls.append(first_touch(path, entry, 1 if side == "CE" else -1, target, stop)[0] - cost)
    return stats(pnls)


# ---------------------------------------------------------------- reporting
def _bucket(rows, fn, order=None):
    groups = {}
    for r in rows:
        k = fn(r)
        if k is not None:
            groups.setdefault(k, []).append(r["pnl"])
    keys = order or sorted(groups)
    return [{"label": k, **stats(groups[k])} for k in keys if k in groups]


def _hour(r):
    for label, a, b in HOUR_BUCKETS:
        if a <= r["minute"] < b:
            return label
    return "other"


def _band(v, edges, labels):
    if v is None:
        return None
    for e, lab in zip(edges, labels):
        if v < e:
            return lab
    return labels[-1]


def build_report(rows_conf, rows_near, params, random_stats=None):
    cost, target, stop = params["cost"], params["target"], params["stop"]
    base = stats([r["pnl"] for r in rows_conf])
    report = {"params": params, "confirmed": {"count": len(rows_conf), "stats": base}, "limitations": [
        "Technical engine only: breadth, sectors and option-premium checks (the Data Check dashboard) have no history to replay.",
        "Index points, not rupees: an option moves roughly delta (about 0.4-0.6 for ATM) x index points, minus decay and spread. "
        "'Cost' is your allowance for that, per round trip, in index points.",
        "Fills assume you enter at the open of the candle after the signal; stops are assumed to fill at the stop level (real slippage is worse in fast markets).",
        "A few weeks of candles is a small sample. Differences smaller than about 2 standard errors are treated as noise, and with many buckets one or two can still look good by chance — confirm anything you act on over a second, different period."]}
    if rows_conf:
        mf = sorted(r["mfe"] for r in rows_conf); ma = sorted(r["mae"] for r in rows_conf)
        med = lambda xs: xs[len(xs) // 2]
        report["confirmed"].update({"median_mfe": round(med(mf), 1), "median_mae": round(med(ma), 1),
                                    "pct_reach_20": round(100.0 * sum(1 for r in rows_conf if r["mfe"] >= 20) / len(rows_conf), 1),
                                    "pct_reach_30": round(100.0 * sum(1 for r in rows_conf if r["mfe"] >= 30) / len(rows_conf), 1),
                                    "pct_reach_40": round(100.0 * sum(1 for r in rows_conf if r["mfe"] >= 40) / len(rows_conf), 1),
                                    "exits": {k: sum(1 for r in rows_conf if r["exit"] == k) for k in ("target", "stop", "time")}})
        grid = []
        for t in TARGET_GRID:
            row = []
            for s in STOP_GRID:
                st = stats([r["grid"][f"{t}/{s}"] for r in rows_conf])
                row.append({"target": t, "stop": s, **st})
            grid.append(row)
        report["grid"] = grid
        cells = [c for row in grid for c in row if c["n"] >= MIN_N]
        report["best_exit"] = max(cells, key=lambda c: c["avg"]) if cells else None
    if rows_conf:
        ag = []
        for t in ATR_TARGETS:
            for s_ in ATR_STOPS:
                vals = [r["atr_grid"][f"{t}/{s_}"] for r in rows_conf if r.get("atr_grid")]
                if vals:
                    ag.append({"target_atr": t, "stop_atr": s_, **stats(vals)})
        report["atr_grid"] = ag
        cand = [c for c in ag if c["n"] >= MIN_N]
        report["best_atr_exit"] = max(cand, key=lambda c: c["avg"]) if cand else None
        atrs = sorted(r["atr"] for r in rows_conf if r.get("atr"))
        report["median_atr"] = round(atrs[len(atrs) // 2], 2) if atrs else None
    report["groups"] = {
        "Time of day (IST)": _bucket(rows_conf, _hour, [h[0] for h in HOUR_BUCKETS]),
        "Side": _bucket(rows_conf, lambda r: r["side"]),
        "Entry trigger": _bucket(rows_conf, lambda r: r["trigger"] or "n/a"),
        "Regime phase": _bucket(rows_conf, lambda r: r["phase"] or r["regime"] or "n/a"),
        "Score": _bucket(rows_conf, lambda r: _band(r["score"], (80, 90), ("70–79", "80–89", "90–100")), ["70–79", "80–89", "90–100"]),
        "RSI extremity (low = exhausted)": _bucket(rows_conf, lambda r: _band(r["rsi_ext"], (20, 30, 40), ("< 20", "20–30", "30–40", "≥ 40")), ["< 20", "20–30", "30–40", "≥ 40"]),
        "Stretch beyond EMA 9": _bucket(rows_conf, lambda r: _band(r["stretch_atr"], (0.5, 1.0, 2.0, 3.5), ("< 0.5 ATR", "0.5–1", "1–2", "2–3.5", "> 3.5 ATR")), ["< 0.5 ATR", "0.5–1", "1–2", "2–3.5", "> 3.5 ATR"]),
        "Day": _bucket(rows_conf, lambda r: r["day"]),
        "Price zone vs signal side (dashboard's zone check)": _bucket(rows_conf, lambda r: r["zone"], ["agrees (UP)", "agrees (DOWN)", "MID", "opposes"]),
        "Price vs previous close": _bucket(rows_conf, lambda r: r["vs_prev"], ["with prev-close side", "against prev-close side"]),
        "Cutoff what-if: entries before / after 12:00": _bucket(rows_conf, lambda r: "before 12:00" if r["minute"] < 720 else "12:00 or later", ["before 12:00", "12:00 or later"]),
        "Cutoff what-if: entries before / after 13:30": _bucket(rows_conf, lambda r: "before 13:30" if r["minute"] < 810 else "13:30 or later", ["before 13:30", "13:30 or later"]),
        # v1.20.13: the "buy at support" question, measured on the signals actually taken
        "Distance from support (CE) / resistance (PE) at entry": _bucket(rows_conf, lambda r: "no level found" if r.get("sr_dist") is None else
                                                                          _band(r["sr_dist"], (0.5, 1.5), ("at the level (< 0.5 ATR)", "0.5–1.5 ATR away", "> 1.5 ATR away (extended)")),
                                                                          ["at the level (< 0.5 ATR)", "0.5–1.5 ATR away", "> 1.5 ATR away (extended)", "no level found"]),
        "Room to the next opposite level": _bucket(rows_conf, lambda r: "no level found" if r.get("sr_room") is None else
                                                   _band(r["sr_room"], (1.0, 2.0), ("< 1 ATR", "1–2 ATR", "> 2 ATR")), ["< 1 ATR", "1–2 ATR", "> 2 ATR", "no level found"]),
        "Chart pattern at entry": _bucket(rows_conf, lambda r: "no pattern" if not r.get("pattern") or r.get("pattern") == "None" else
                                          "range (no break)" if r.get("pattern_stage") == "range" else
                                          ("breakout WITH the trade" if r.get("pattern_side") == r["side"] else "breakout AGAINST the trade") if r.get("pattern_stage") == "breakout" else
                                          ("forming WITH the trade" if r.get("pattern_side") == r["side"] else "forming AGAINST the trade"),
                                          ["breakout WITH the trade", "forming WITH the trade", "range (no break)", "forming AGAINST the trade", "breakout AGAINST the trade", "no pattern"]),
        "Signal of the day (same side)": _bucket(rows_conf, lambda r: "1st" if r.get("nth_today", 1) == 1 else "2nd or later", ["1st", "2nd or later"]),
    }
    report["strategies"] = [{"name": name, **stats([r["strat"][name] for r in rows_conf if name in r["strat"]])}
                            for name, _ in STRATEGIES + ATR_STRATEGIES] if rows_conf else []
    report["strategies"] = [x for x in report["strategies"] if x.get("n")]
    report["cost_table"] = [{"cost": c2, **stats([r["pnl"] + cost - c2 for r in rows_conf])} for c2 in (0, 2, 4, 6, 8)] if rows_conf else []
    report["trades"] = [{"day": r["day"], "time": r["hhmm"], "side": r["side"], "pnl": round(r["pnl"], 1), "exit": r["exit"], "mfe": round(r["mfe"], 1),
                         "mae": round(r["mae"], 1), "score": r["score"], "regime": r["regime"], "phase": r["phase"], "trigger": r["trigger"],
                         "rsi_ext": None if r["rsi_ext"] is None else round(r["rsi_ext"], 1), "stretch_atr": None if r["stretch_atr"] is None else round(r["stretch_atr"], 2),
                         "zone": r["zone"], "vs_prev": r["vs_prev"], "atr": None if not r.get("atr") else round(r["atr"], 2),
                         "sr_dist_atr": r.get("sr_dist"), "sr_room_atr": r.get("sr_room"), "sr_strength": r.get("sr_strength"),
                         "nth_today": r.get("nth_today"), "pattern": r.get("pattern"), "pattern_side": r.get("pattern_side"),
                         "pattern_stage": r.get("pattern_stage")} for r in rows_conf]
    gates = {}
    for r in rows_near:
        gates.setdefault(r["gate"], []).append(r["pnl"])
    report["random_entries"] = random_stats or {"n": 0}
    report["ablation"] = sorted(({"gate": g, **stats(v)} for g, v in gates.items()), key=lambda x: -x["n"])
    report["verdict"] = verdict(report)
    return report


SIG_Z = 2.0        # chance alone produces |z| > 1.5 about 1 time in 8; 2.0 keeps false alarms near 1 in 20 per comparison


def _sig(s, ref=0.0):
    """True when a mean differs from `ref` by more than SIG_Z standard errors (and the sample is not tiny)."""
    return bool(s.get("se") and s.get("n", 0) >= 15 and abs(s["avg"] - ref) > SIG_Z * s["se"])


def verdict(rep):
    """Plain-language findings, each tied to numbers and to whether the sample is big enough to trust."""
    out, c = [], rep["confirmed"]
    p = rep["params"]
    base = c["stats"]
    if c["count"] < 15:
        out.append({"level": "info", "text": f"Only {c['count']} confirmed signals in this sample — too few to judge anything reliably. "
                    f"Use more days (or run it again after the market has produced more signals)."})
    if base.get("n"):
        lvl = "good" if base["avg"] > 0 and _sig(base) else "bad" if base["avg"] < 0 else "info"
        out.append({"level": lvl, "text": f"Confirmed signals with a {p['target']}-point target / {p['stop']}-point stop and {p['cost']} points of cost: "
                    f"{base['n']} trades, {base['win_rate']}% winners, {base['avg']:+.1f} points per trade ({base['total']:+.0f} total, profit factor {base['pf']}). "
                    + ("This is a measurable edge." if lvl == "good" else "No reliable edge at this exit — see the exit grid and the blockers below." if lvl != "good" and base["avg"] <= 0
                       else "Positive but within noise — do not trust it yet.")})
    rnd = rep.get("random_entries") or {}
    if base.get("n", 0) >= MIN_N and rnd.get("n", 0) >= 50 and base.get("se") is not None:
        edge = base["avg"] - rnd["avg"]
        se = math.sqrt((base["se"] or 0) ** 2 + (rnd["se"] or 0) ** 2)
        clear = edge > SIG_Z * se
        lvl = "good" if clear and base["n"] >= 30 else "bad" if edge < 0 and abs(edge) > SIG_Z * se else "info"
        # v1.19.6: a large edge with fewer than 30 trades is "promising, not proven" - it used to be called "indistinguishable from random".
        tail = (" — statistically real." if lvl == "good" else " — the signals are WORSE than guessing." if lvl == "bad"
                else f" — clearly better than random, but only {base['n']} trades; it needs 30+ before it counts as proven." if clear
                else " — indistinguishable from random entries in this sample, so the direction/timing logic is not adding value yet; results are being driven by the exit rule and cost.")
        out.append({"level": lvl, "text": f"Versus random entries: random CE/PE entries with the same exit made {rnd['avg']:+.1f} points per trade (n={rnd['n']}); the signals made {base['avg']:+.1f}. "
                    f"Signal edge = {edge:+.1f} points per trade" + tail})
    if c.get("median_mfe") is not None:
        out.append({"level": "info", "text": f"After a confirmed signal the market typically went {c['median_mfe']} points your way at best and {c['median_mae']} points against you "
                    f"before the horizon. {c['pct_reach_20']}% reached +20, {c['pct_reach_30']}% reached +30, {c['pct_reach_40']}% reached +40."
                    + (f" A stop tighter than about {c['median_mae']} points is inside the normal noise of these entries and will be hit before the move develops." if c["median_mae"] else "")})
    be = rep.get("best_exit")
    if be and base.get("n"):
        # the pair you typed need not be a cell of the fixed grid (e.g. 60/60), but `base` is always computed for exactly that pair
        cur_avg = base["avg"]
        if be["avg"] > cur_avg + 0.5:
            out.append({"level": "action", "text": f"Best tested exit: target {be['target']} / stop {be['stop']} → {be['avg']:+.1f} points per trade (n={be['n']}), "
                        f"versus {cur_avg:+.1f} at {p['target']}/{p['stop']}. Changing the paper-trade stop/target rule toward this is the first correction to test."})
    strat = [x for x in rep.get("strategies", []) if x.get("n", 0) >= MIN_N]
    if strat and base.get("n"):
        top = max(strat, key=lambda x: x["avg"])
        fixed = next((x for x in strat if x["name"].startswith("Fixed target 30")), None)
        if fixed and top["avg"] > fixed["avg"] + 1.0 and top["avg"] > 0:
            out.append({"level": "action", "text": f"Best exit style tested: “{top['name']}” → {top['avg']:+.1f} points per trade ({top['win_rate']}% winners, n={top['n']}) "
                        f"versus {fixed['avg']:+.1f} for a plain 30/20. Managing the exit (trailing / scaling out) is worth more than changing the entry."})
    worst, best = [], []
    for name, rows in rep["groups"].items():
        for g in rows:
            if g.get("n", 0) >= MIN_N and _sig(g):
                (best if g["avg"] > 0 else worst).append((name, g))
    for name, g in sorted(worst, key=lambda x: x[1]["avg"])[:4]:
        out.append({"level": "action", "text": f"Avoid / tighten — {name}: “{g['label']}” lost {g['avg']:+.1f} points per trade over {g['n']} signals ({g['win_rate']}% winners)."})
    for name, g in sorted(best, key=lambda x: -x[1]["avg"])[:3]:
        out.append({"level": "good", "text": f"Works — {name}: “{g['label']}” made {g['avg']:+.1f} points per trade over {g['n']} signals ({g['win_rate']}% winners)."})
    ref = base["avg"] if base.get("n") else 0.0
    for a in rep["ablation"]:
        if a.get("n", 0) < MIN_N:
            continue
        if a["avg"] > max(ref, 0) + 0.5 and a["avg"] > 0:
            out.append({"level": "action", "text": f"Gate too strict? “{a['gate']}” was the ONLY thing blocking {a['n']} setups that averaged {a['avg']:+.1f} points per trade "
                        f"(confirmed signals: {ref:+.1f}). Relaxing this gate would have added profitable entries."})
        elif a["avg"] < 0 and _sig(a):
            out.append({"level": "good", "text": f"Gate earning its keep: “{a['gate']}” blocked {a['n']} setups that would have lost {a['avg']:+.1f} points per trade. Keep it."})
    b = rep.get("best_atr_exit")
    if b and rep.get("median_atr"):
        out.append({"level": "info", "text": f"ATR exits (what the Data Check dashboard uses): best tested is target {b['target_atr']:g} ATR / stop {b['stop_atr']:g} ATR "
                    f"→ {b['avg']:+.1f} points per trade (n={b['n']}, median 5-minute ATR {rep['median_atr']:g} pts). Compare with the dashboard setting in THRESHOLDS (atr_sl, atr_t2)."})
    if not any(v["level"] == "action" for v in out):
        out.append({"level": "info", "text": "No single correction stands out with enough data. Use more days, or try the other signal mode and compare."})
    return out


# ---------------------------------------------------------------- win-rate finder (v1.19.1)
def winrate_finder(rows, min_win=70.0):
    """Which exit (fixed points or ATR multiples) gives >= min_win % winners AND a positive average in BOTH halves of the period?
    A rule that only works in one half is luck, not an edge. Returns {"split", "cells": [...], "near": [...]}."""
    days = sorted({r["day"] for r in rows})
    if len(days) < 4 or len(rows) < 12:
        return {"split": None, "cells": [], "near": [], "note": "Too few days / signals to split the period in two."}
    cut = days[len(days) // 2]
    h1 = [r for r in rows if r["day"] < cut]
    h2 = [r for r in rows if r["day"] >= cut]
    cells = []
    for kind, key, keys in (("points", "grid", [f"{t}/{s}" for t in TARGET_GRID for s in STOP_GRID]),
                            ("ATR", "atr_grid", [f"{t}/{s}" for t in ATR_TARGETS for s in ATR_STOPS])):
        for k in keys:
            a = stats([r[key][k] for r in rows if k in r.get(key, {})])
            b = stats([r[key][k] for r in h1 if k in r.get(key, {})])
            c = stats([r[key][k] for r in h2 if k in r.get(key, {})])
            if not (a.get("n") and b.get("n") and c.get("n")):
                continue
            t, s_ = k.split("/")
            cells.append({"kind": kind, "target": float(t), "stop": float(s_), "all": a, "first": b, "second": c,
                          "worst_win": min(b["win_rate"], c["win_rate"]), "worst_avg": min(b["avg"], c["avg"])})
    good = sorted([x for x in cells if x["worst_win"] >= min_win and x["worst_avg"] > 0], key=lambda x: (-x["worst_avg"], -x["worst_win"]))
    near = sorted([x for x in cells if x["worst_avg"] > 0], key=lambda x: -x["worst_win"])[:8]
    return {"split": {"first": f"{days[0]} – {h1[-1]['day'] if h1 else '-'}", "second": f"{cut} – {days[-1]}", "n1": len(h1), "n2": len(h2)},
            "cells": good[:10], "near": near, "min_win": min_win}


# ---------------------------------------------------------------- missed big moves (v1.19)
def missed_moves(cs, replayed, horizon=12, move_atr=2.5, max_adverse_atr=0.75, before=2, after=3):
    """'Which gate made me miss the big move?' answered from the replay.

    A big move starts at bar i when, entering at the next bar's open, price travels >= move_atr x ATR in one direction within
    `horizon` bars (same session) WITHOUT first going max_adverse_atr x ATR the other way (so the normal stop would have survived).
    Overlapping starts in the same direction count as one move. A move is CAUGHT when a CONFIRMED signal on that side appeared
    from `before` bars before to `after` bars after its start. For every missed move the gates that failed on that side during the
    same window are counted. Returns {moves, caught, missed, blockers: [{gate, moves, pct}], examples: [...]}."""
    rec = {r["i"]: r for r in replayed}
    moves, busy_until = [], {1: -1, -1: -1}
    for i in sorted(rec):
        r = rec[i]
        atr = r.get("atr")
        if not atr or i + 1 >= len(cs):
            continue
        day = cs[i]["_dt"].astimezone(IST).date()
        if cs[i + 1]["_dt"].astimezone(IST).date() != day:
            continue
        entry = cs[i + 1]["open"]
        for d in (1, -1):
            if i <= busy_until[d]:
                continue
            best = 0.0
            for j in range(i + 1, min(len(cs), i + 1 + horizon)):
                c = cs[j]
                if c["_dt"].astimezone(IST).date() != day:
                    break
                adverse = (entry - c["low"]) if d > 0 else (c["high"] - entry)
                if adverse >= max_adverse_atr * atr:
                    break
                best = max(best, (c["high"] - entry) if d > 0 else (entry - c["low"]))
                if best >= move_atr * atr:
                    moves.append({"i": i, "d": d, "end": j, "points": round(best, 1), "atr": atr})
                    busy_until[d] = j
                    break
    blockers, caught, examples = {}, 0, []
    for m in moves:
        side = "CE" if m["d"] > 0 else "PE"
        win = [rec[j] for j in range(m["i"] - before, m["i"] + after + 1) if j in rec]
        if any(w["signal"] == f"CONFIRMED_{side}" for w in win):
            caught += 1
            continue
        same = [w for w in win if w.get("side") == side]
        gates = {g for w in same for g in w["failed"]}
        if not same:
            gates = {"Engine leaned the other way / no side (score)"}
        for g in gates:
            blockers[g] = blockers.get(g, 0) + 1
        if True:                    # v1.20.15: keep every example, then the MOST RECENT 40 (the first 25 used to hide the latest days)
            dt = cs[m["i"]]["_dt"].astimezone(IST)
            examples.append({"day": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%H:%M"), "side": side, "points": m["points"],
                             "atr_move": round(m["points"] / m["atr"], 1), "blocked_by": sorted(gates)})
    examples = examples[-40:][::-1]                                   # newest first
    missed = len(moves) - caught
    rows = sorted(({"gate": g, "moves": n, "pct": round(100.0 * n / missed, 1) if missed else 0.0} for g, n in blockers.items()), key=lambda x: -x["moves"])
    return {"moves": len(moves), "caught": caught, "missed": missed, "blockers": rows, "examples": examples,
            "rule": f"move ≥ {move_atr:g} ATR within {horizon} bars without {max_adverse_atr:g} ATR against first; caught = CONFIRMED on that side from {before} bars before to {after} after the start"}


# ---------------------------------------------------------------- v1.20.7 what-if: trend-day entries (Diagnostics only)
TREND_RELAX = {
    "base": ("Entry trigger", "Pullback-and-resume"),
    "chase": ("Entry trigger", "Pullback-and-resume", "Not chasing"),
}
MIDDAY = (12 * 60, 14 * 60 + 30)        # the entry-window gap a trend day may fill; before 09:45 / after 15:00 stay blocked


def _touch(cs, i, d, atr, sl_m=1.5, tg_m=1.5):
    """1.5 / 1.5 ATR test trade entered at the open after bar i: ('target'|'stop'|None, index of the bar that decided it)."""
    if i + 1 >= len(cs) or not atr:
        return None, None
    day = cs[i]["_dt"].astimezone(IST).date()
    entry = cs[i + 1]["open"]
    for k in range(i + 1, len(cs)):
        kt = cs[k]["_dt"].astimezone(IST)
        if kt.date() != day or kt.hour * 60 + kt.minute >= SQUAREOFF_MIN:
            return None, k
        c = cs[k]
        if ((entry - c["low"]) if d > 0 else (c["high"] - entry)) >= sl_m * atr:
            return "stop", k
        if ((c["high"] - entry) if d > 0 else (entry - c["low"])) >= tg_m * atr:
            return "target", k
    return None, None


def trend_day_events(cs, replayed, variant="base", min_proofs=2, max_per_day=3, interval=5):
    """Extra entries a 'trend-day' rule would add. Strictly causal - uses only what was known when each bar closed:
      * the engine leans side S with score >= the threshold (the score gate is NOT relaxed) and every other gate passes, except
        the entry trigger / pullback (and, for variant 'chase', the not-chasing gate), and the entry window ONLY for 12:00-14:30;
      * proof of a trend day: at least `min_proofs` earlier same-side setups of the same day (>= 3 bars apart) whose 1.5 ATR test
        trade had ALREADY reached its target, without any same-side setup hitting its stop since;
      * one trend-day trade at a time, at most `max_per_day` per day, never on a bar that is already a confirmed signal."""
    relax = TREND_RELAX[variant]
    by_day, out = {}, []
    for r in replayed:
        by_day.setdefault(cs[r["i"]]["_dt"].astimezone(IST).date(), []).append(r)
    for day, rows in by_day.items():
        proofs = []                     # (side, start_i, result, decided_at)
        busy_until, taken, last_proof_i = -1, 0, {}
        for r in rows:
            i, side = r["i"], r.get("side")
            if not side or not r.get("atr"):
                continue
            d = 1 if side == "CE" else -1
            if not any(f.startswith("Score ≥") for f in r["failed"]) and not any(f.startswith("Engine leaned") for f in r["failed"]) \
                    and i - last_proof_i.get(side, -99) >= 3:
                res, k = _touch(cs, i, d, r["atr"])
                proofs.append((side, i, res, k))
                last_proof_i[side] = i
            if r["signal"].startswith("CONFIRMED") or i <= busy_until or taken >= max_per_day:
                continue
            m = cs[i]["_dt"].astimezone(IST)
            minute = m.hour * 60 + m.minute + interval          # the bar's close time, as the live window check uses
            ok = True
            for f in r["failed"]:
                if f.startswith(relax):
                    continue
                if f.startswith("Entry time window") and MIDDAY[0] <= minute < MIDDAY[1]:
                    continue
                ok = False
                break
            if not ok:
                continue
            known = [p for p in proofs if p[0] == side and p[1] < i and p[3] is not None and p[3] <= i]
            if any(p[2] == "stop" for p in known):
                last_stop = max(p[3] for p in known if p[2] == "stop")
                known = [p for p in known if p[3] > last_stop]
            if sum(1 for p in known if p[2] == "target") < min_proofs:
                continue
            res, k = _touch(cs, i, d, r["atr"])
            busy_until = k if k is not None else i + 12
            taken += 1
            out.append({**r, "trend_proofs": sum(1 for p in known if p[2] == "target")})
    return out


def trend_day_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop, plan_key="1.5/1.5"):
    """Report for both variants: the extra trades alone, by half, and added to the confirmed signals."""
    days = sorted({r["day"] for r in conf_rows}) or sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    base_vals = [r["atr_grid"][plan_key] for r in conf_rows if plan_key in r.get("atr_grid", {})]
    out = {"plan": plan_key, "baseline": stats(base_vals), "variants": []}
    for variant, label in (("base", "Trend day: skip the pullback trigger, allow 12:00-14:30"),
                           ("chase", "Trend day + allow chasing (> 1 ATR beyond EMA 9)")):
        evs = trend_day_events(cs, replayed, variant, interval=interval)
        rows = evaluate(cs, evs, interval, horizon, cost, target, stop)
        vals = [r["atr_grid"][plan_key] for r in rows if plan_key in r.get("atr_grid", {})]
        v = {"variant": variant, "label": label, "extra": stats(vals), "combined": stats(base_vals + vals),
             "trades": [{"day": r["day"], "time": r["hhmm"], "side": r["side"], "pts": round(r["atr_grid"].get(plan_key, 0), 1),
                         "proofs": r.get("trend_proofs")} for r in rows][:40]}
        if cut:
            v["first"] = stats([r["atr_grid"][plan_key] for r in rows if r["day"] < cut and plan_key in r.get("atr_grid", {})])
            v["second"] = stats([r["atr_grid"][plan_key] for r in rows if r["day"] >= cut and plan_key in r.get("atr_grid", {})])
        e, f_, s_ = v["extra"], v.get("first") or {}, v.get("second") or {}
        both = bool(f_.get("n")) and bool(s_.get("n")) and f_.get("avg", 0) > 0 and s_.get("avg", 0) > 0
        v["verdict"] = ("no extra trades in this period" if not e.get("n") else
                        "promising - positive in both halves with 15+ trades; still paper-test before going live" if both and e["n"] >= 15 and e["avg"] > 0 else
                        "positive but too few trades (need 15+, positive in both halves)" if e.get("avg", 0) > 0 else
                        "loses - keep the current rules")
        out["variants"].append(v)
    return out


# ---------------------------------------------------------------- v1.20.27/28 strong trend entries (trend_entry.py)
import trend_entry as _te


def strong_trend_events(cs, replayed, variant="rsi", max_per_day=3):
    """Extra entries the strong-trend rule (trend_entry.check, the same code the live app uses) would add: bars blocked only by the
    relaxable pullback rules, on a strong one-way trend. One trade at a time, at most `max_per_day` per day, never on a bar that is
    already a confirmed signal. The entry window and every other gate still apply."""
    variant = {"strict": "rsi", "loose": "continuation"}.get(variant, variant)
    out, busy_until, taken_day = [], -1, {}
    for r in replayed:
        i, side, atr = r["i"], r.get("side"), r.get("atr")
        if not side or not atr or r["signal"].startswith("CONFIRMED") or i <= busy_until or i < 20:
            continue
        te = _te.check(cs[:i + 1], side, atr, r.get("rsi"), r.get("vwap"), r.get("stretch_atr"), r["failed"], variant)
        if not te:
            continue
        day = cs[i]["_dt"].astimezone(IST).date()
        if taken_day.get(day, 0) >= max_per_day:
            continue
        res, k = _touch(cs, i, 1 if side == "CE" else -1, atr)
        busy_until = k if k is not None else i + 12
        taken_day[day] = taken_day.get(day, 0) + 1
        out.append({**r, "trend_eff": te["efficiency"], "trend_move_atr": te["move_atr"]})
    return out


def strong_trend_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop):
    """Report: extra trades alone (live trailing exit and fixed 1.5 / 1.5 ATR), by half, and added to the current signals."""
    days = sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    tr = lambda rows: [r["strat"][TRAIL_NAME] for r in rows if TRAIL_NAME in r.get("strat", {})]
    base = tr(conf_rows)
    out = {"baseline": stats(base), "variants": []}
    for variant, label in (("rsi", "Strong trend: allow RSI up to 75 (CE) / down to 25 (PE)"),
                           ("continuation", "Strong trend continuation: break of the last 3 bars, pullback rules relaxed")):
        rows = evaluate(cs, strong_trend_events(cs, replayed, variant), interval, horizon, cost, target, stop, with_extras=True)
        v = {"variant": variant, "label": label, "trail": stats(tr(rows)), "combined": stats(base + tr(rows)),
             "fixed_atr": stats([r["atr_grid"]["1.5/1.5"] for r in rows if "1.5/1.5" in r.get("atr_grid", {})]),
             "trades": [{"day": r["day"], "time": r["hhmm"], "side": r["side"], "rsi": round(r.get("rsi") or 0, 1),
                         "eff": r.get("trend_eff"), "move_atr": r.get("trend_move_atr"),
                         "pts": round(r["strat"].get(TRAIL_NAME, 0), 1)} for r in rows][:40]}
        if cut:
            v["first"] = stats(tr([r for r in rows if r["day"] < cut]))
            v["second"] = stats(tr([r for r in rows if r["day"] >= cut]))
        e, f_, s_, fx = v["trail"], v.get("first") or {}, v.get("second") or {}, v["fixed_atr"]
        both = bool(f_.get("n")) and bool(s_.get("n")) and f_.get("avg", 0) > 0 and s_.get("avg", 0) > 0
        solid = (e.get("se") or 0) > 0 and e.get("avg", 0) >= e["se"] and fx.get("avg", -1) > 0
        v["verdict"] = ("no extra trades in this period" if not e.get("n") else
                        "works - positive in both halves, 15+ trades, beats its noise and also wins with a fixed exit; worth a live paper test"
                        if both and e["n"] >= 15 and solid else
                        "fragile - positive in both halves with 15+ trades, but inside its noise or the fixed exit loses; do not switch on"
                        if both and e["n"] >= 15 and e["avg"] > 0 else
                        "positive, not proven (need 15+ trades, positive in both halves)" if e.get("avg", 0) > 0 else
                        "loses - keep it off")
        out["variants"].append(v)
    return out


# ---------------------------------------------------------------- v1.20.16 chart patterns: old rule vs pattern rule
TRIG_LABELS = ("Entry trigger (EMA 9 retest resumes, or break of the last pause)", "Pullback-and-resume trigger (retest holds)")
CANDLE_LABEL = "Closed-candle price-action confirmation"
AGAINST_LABEL = "No opposite chart-pattern breakout"


def _pattern_failed(r, mode, rule):
    """Failed gates of a replayed bar under rule 'old' (last candle / pullback only) or 'new' (a chart-pattern breakout on the trade side
    can confirm, an opposite breakout blocks), whatever CHART_PATTERN_MODE the replay itself ran with."""
    pe = r.get("pe")
    if not pe or not r.get("side"):
        return None
    trig = TRIG_LABELS[0] if mode == "scalp" else TRIG_LABELS[1]
    base = [f for f in r["failed"] if f not in TRIG_LABELS and f != CANDLE_LABEL and f != AGAINST_LABEL]
    pok, pag = pe.get("pattern_ok"), pe.get("pattern_against")
    t_ok, c_ok = pe.get("trigger_old_ok"), pe.get("candle_old_ok")
    if t_ok is None:                                   # gates were not evaluated that far (no ATR): keep what the replay saw
        return list(r["failed"])
    if rule == "old":
        return base + ([trig] if not t_ok else []) + ([CANDLE_LABEL] if c_ok is False else [])
    return base + ([trig] if not (t_ok or pok) else []) + ([CANDLE_LABEL] if c_ok is False and not pok else []) + ([AGAINST_LABEL] if pag else [])


def _run_starts(replayed, ok):
    out, prev = [], None
    for r in replayed:
        k = r.get("side") if ok(r) else None
        if k and (prev is None or prev[0] != k or r["i"] != prev[1] + 1):
            out.append(r)
        prev = (k, r["i"]) if k else None
    return out


def pattern_rule_compare(cs, replayed, interval, horizon, cost, target, stop, mode):
    """Entries under the old rule vs the chart-pattern rule on the same bars, judged on the live trailing exit (and by half)."""
    sets = {}
    for rule in ("old", "new"):
        evs = _run_starts(replayed, lambda r, rule=rule: (lambda f: f is not None and not f)(_pattern_failed(r, mode, rule)))
        sets[rule] = evaluate(cs, evs, interval, horizon, cost, target, stop, with_extras=True)
    keys = {rule: {(r["i"], r["side"]) for r in rows} for rule, rows in sets.items()}
    days = sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    tr = lambda rows: [r["strat"][TRAIL_NAME] for r in rows if TRAIL_NAME in r.get("strat", {})]
    out = {}
    for rule, rows in sets.items():
        d = {"trail": stats(tr(rows)), "points": stats([r["pnl"] for r in rows])}
        if cut:
            d["first"] = stats(tr([r for r in rows if r["day"] < cut]))
            d["second"] = stats(tr([r for r in rows if r["day"] >= cut]))
        out[rule] = d
    added = [r for r in sets["new"] if (r["i"], r["side"]) not in keys["old"]]
    removed = [r for r in sets["old"] if (r["i"], r["side"]) not in keys["new"]]
    out["added"] = {"trail": stats(tr(added)), "trades": [{"day": r["day"], "time": r["hhmm"], "side": r["side"], "pattern": r.get("pattern"),
                                                             "pts": round(r["strat"].get(TRAIL_NAME, 0), 1)} for r in added][:30]}
    out["removed"] = {"trail": stats(tr(removed)), "trades": [{"day": r["day"], "time": r["hhmm"], "side": r["side"], "pattern": r.get("pattern"),
                                                                 "pts": round(r["strat"].get(TRAIL_NAME, 0), 1)} for r in removed][:30]}
    # why the rule did (not) add trades: how many bars had a pattern breakout on the engine's side, and what else blocked them
    pbars = [r for r in replayed if (r.get("pe") or {}).get("pattern_ok")]
    blk = {}
    for r in pbars:
        for f_ in [x for x in (_pattern_failed(r, mode, "new") or [])]:
            blk[f_] = blk.get(f_, 0) + 1
    out["pattern_bars"] = len(pbars)
    out["pattern_blockers"] = sorted(({"gate": k, "bars": v} for k, v in blk.items()), key=lambda x: -x["bars"])[:8]
    o, n = out["old"]["trail"], out["new"]["trail"]
    nf, ns = out["new"].get("first") or {}, out["new"].get("second") or {}
    of, os_ = out["old"].get("first") or {}, out["old"].get("second") or {}
    both_better = bool(nf.get("n")) and bool(ns.get("n")) and nf.get("total", 0) >= of.get("total", 0) and ns.get("total", 0) >= os_.get("total", 0)
    out["verdict"] = ("no difference in this period" if not added and not removed else
                      "pattern rule better in both halves - keep it" if both_better and n.get("total", 0) > o.get("total", 0) else
                      "pattern rule worse - set CHART_PATTERN_MODE=info" if n.get("total", 0) < o.get("total", 0) else
                      "mixed - keep watching")
    return out


# ---------------------------------------------------------------- v1.20.20 leading triggers instead of the pullback trigger
def leading_compare(cs, replayed, interval, horizon, cost, target, stop, mode):
    """For each leading trigger (leading.py): entries where it fires on a bar whose ONLY missing piece (old rules) was the entry trigger
    ('strict'), and entries where it fires with just the engine's side, the score and the entry window ('loose'). Live trailing exit."""
    import leading as _ld
    days = sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    tr = lambda rows: [r["strat"][TRAIL_NAME] for r in rows if TRAIL_NAME in r.get("strat", {})]
    base = evaluate(cs, _run_starts(replayed, lambda r: (lambda f: f is not None and not f)(_pattern_failed(r, mode, "old"))),
                    interval, horizon, cost, target, stop, with_extras=True)
    base_keys = {(r["i"], r["side"]) for r in base}
    out = {"baseline": stats(tr(base)), "baseline_total": round(sum(tr(base)), 1), "kinds": []}
    for kind in _ld.KINDS:
        for variant in ("strict", "loose"):
            def ok(r, kind=kind, variant=variant):
                f = _pattern_failed(r, mode, "old")
                if f is None or not _ld.fires(cs, r["i"], r["side"], kind):
                    return False
                rest = [x for x in f if x not in TRIG_LABELS]
                if variant == "loose":
                    rest = [x for x in rest if x.startswith(("Score", "Entry time window"))]
                return not rest
            rows = evaluate(cs, _run_starts(replayed, ok), interval, horizon, cost, target, stop, with_extras=True)
            extra = [r for r in rows if (r["i"], r["side"]) not in base_keys]
            d = {"kind": kind, "label": _ld.LABELS[kind], "variant": variant, "entries": stats(tr(rows)), "extra": stats(tr(extra)),
                 "combined_total": round(sum(tr(base)) + sum(tr(extra)), 1)}
            if cut:
                d["extra_first"] = stats(tr([r for r in extra if r["day"] < cut]))
                d["extra_second"] = stats(tr([r for r in extra if r["day"] >= cut]))
            e, f_, s_ = d["extra"], d.get("extra_first") or {}, d.get("extra_second") or {}
            both = bool(f_.get("n")) and bool(s_.get("n")) and f_.get("avg", 0) > 0 and s_.get("avg", 0) > 0
            d["verdict"] = ("no extra entries" if not e.get("n") else
                            ("adds profit in both halves (15+ trades) - candidate for LEADING_TRIGGER" if variant == "strict" else
                             "adds profit in both halves (15+ trades) - but LEADING_TRIGGER only gives the STRICT version live; this loose one skips the trend / chasing / sideways rules")
                            if both and e["n"] >= 15 and e["avg"] > 0 else
                            "positive, not proven yet (need 15+ extra trades, positive in both halves)" if e.get("avg", 0) > 0 else
                            "loses - do not use")
            out["kinds"].append(d)
    return out


# ---------------------------------------------------------------- v1.20.22 Opening Range Breakout (ORB), a strategy the app does not use
ORB_LAST_ENTRY = 12 * 60          # first break only, before noon (the morning window is where the signals pay)


def orb_events(cs, replayed, interval, or_minutes=15, variant="plain"):
    """Classic Opening Range Breakout (Crabel; Zarattini & Aziz): the high / low of the first `or_minutes` after 09:15 is the opening
    range; the FIRST 5-minute close above it is a CE entry, below it a PE entry; one trade a day, entries until 12:00.
    variant 'plain'      - the break alone
            'with_trend' - the engine must lean the same side on that bar
            'width'      - only when the opening range is 0.5-2.5 ATR wide (skips dead and chaotic opens)"""
    rec = {r["i"]: r for r in replayed}
    by_day = {}
    for i, c in enumerate(cs):
        by_day.setdefault(c["_dt"].astimezone(IST).date(), []).append(i)
    out = []
    for day, idx in by_day.items():
        start = 9 * 60 + 15
        orb = [i for i in idx if start <= (lambda t: t.hour * 60 + t.minute)(cs[i]["_dt"].astimezone(IST)) < start + or_minutes]
        if not orb:
            continue
        hi, lo = max(cs[i]["high"] for i in orb), min(cs[i]["low"] for i in orb)
        for i in idx:
            t = cs[i]["_dt"].astimezone(IST)
            m = t.hour * 60 + t.minute
            if m < start + or_minutes or i in orb:
                continue
            if m + interval > ORB_LAST_ENTRY:
                break
            c = cs[i]["close"]
            side = "CE" if c > hi else "PE" if c < lo else None
            if not side:
                continue
            r = rec.get(i)
            atr = r.get("atr") if r else None
            if not atr:
                break
            if variant == "with_trend" and r.get("side") != side:
                break                                     # the first break did not agree with the engine: no trade that day
            if variant == "width" and not 0.5 <= (hi - lo) / atr <= 2.5:
                break
            out.append({**r, "side": side, "or_high": hi, "or_low": lo, "or_width_atr": round((hi - lo) / atr, 2)})
            break
    return out


def orb_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop):
    days = sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    tr = lambda rows: [r["strat"][TRAIL_NAME] for r in rows if TRAIL_NAME in r.get("strat", {})]
    out = {"baseline": stats(tr(conf_rows)), "variants": []}
    for orm in (15, 30):
        for variant, label in (("plain", "break alone"), ("with_trend", "engine leans the same side"), ("width", "range 0.5-2.5 ATR wide")):
            rows = evaluate(cs, orb_events(cs, replayed, interval, orm, variant), interval, horizon, cost, target, stop, with_extras=True)
            v = {"or_minutes": orm, "variant": variant, "label": f"ORB {orm} min - {label}", "trail": stats(tr(rows)),
                 "points": stats([r["pnl"] for r in rows]), "fixed_atr": stats([r["atr_grid"]["1.5/1.5"] for r in rows if "1.5/1.5" in r.get("atr_grid", {})]),
                 "trades": [{"day": r["day"], "time": r["hhmm"], "side": r["side"], "or_width_atr": r.get("or_width_atr"),
                             "pts": round(r["strat"].get(TRAIL_NAME, 0), 1)} for r in rows][:30]}
            if cut:
                v["first"] = stats(tr([r for r in rows if r["day"] < cut]))
                v["second"] = stats(tr([r for r in rows if r["day"] >= cut]))
            e, f_, s_ = v["trail"], v.get("first") or {}, v.get("second") or {}
            both = bool(f_.get("n")) and bool(s_.get("n")) and f_.get("avg", 0) > 0 and s_.get("avg", 0) > 0
            fx = v.get("fixed_atr") or {}
            solid = (e.get("se") or 0) > 0 and e.get("avg", 0) >= e["se"] and fx.get("avg", -1) > 0
            # v1.20.23: "works" now also needs the average to clear 1 standard error AND the fixed 1.5 / 1.5 ATR exit to be positive -
            # otherwise the profit comes from one or two big trailing winners and would vanish with a slightly different exit.
            v["verdict"] = ("no trades" if not e.get("n") else
                            "works - positive in both halves, 15+ trades, beats its noise and also wins with a fixed exit; worth a live paper test"
                            if both and e["n"] >= 15 and e["avg"] > 0 and solid else
                            "fragile - positive in both halves with 15+ trades, but " + (
                                f"the fixed 1.5/1.5 ATR exit loses ({fx.get('avg', 0):+.1f} pts) and " if fx.get("avg", -1) <= 0 else "") +
                            f"the average ({e['avg']:+.1f}) is inside its noise (±{e.get('se') or 0:.1f}); a few big winners carry it - do not switch on"
                            if both and e["n"] >= 15 and e["avg"] > 0 else
                            "positive, not proven (need 15+ trades, positive in both halves)" if e.get("avg", 0) > 0 else
                            "loses in this period")
            out["variants"].append(v)
    return out


# ---------------------------------------------------------------- v1.20.13 support / resistance: does buying at the level work?
def _sr_at(cs, i, atr, interval, symbol, cache=None, every=6):
    """S/R zones as they were known when bar i closed (same rules as the terminal's S/R map). Recomputed every `every` bars."""
    import option_plan as _op
    key = i - (i % every)
    if cache is not None and key in cache:
        sr = cache[key]
    else:
        per = max(1, round(375 / max(1, interval)))
        sr = _op.sr_levels(cs[max(0, key - per * 6):key + 1], atr, interval, symbol)
        if cache is not None:
            cache[key] = sr
    return sr


def _levels_around(sr, price):
    """All zone prices from a cached S/R read, split again around `price` (the cache may be a few bars old)."""
    lv = [l for l in (sr.get("supports") or []) + (sr.get("resistances") or [])]
    below = sorted([l for l in lv if l["price"] <= price], key=lambda l: price - l["price"])
    above = sorted([l for l in lv if l["price"] > price], key=lambda l: l["price"] - price)
    return below, above


def sr_features(cs, rows, interval, symbol):
    """Adds to each evaluated row: distance from the level it is bought off (support for CE, resistance for PE) and the room to the
    next opposite level, both in ATR, plus the n-th same-side signal of the day."""
    cache, seen = {}, {}
    for r in sorted(rows, key=lambda x: x["i"]):
        atr = r.get("atr")
        price = cs[r["i"]]["close"]
        sr = _sr_at(cs, r["i"], atr, interval, symbol, cache) if atr else {}
        below, above = _levels_around(sr, price) if sr else ([], [])
        own, opp = (below, above) if r["side"] == "CE" else (above, below)
        r["sr_dist"] = round(abs(price - own[0]["price"]) / atr, 2) if own and atr else None
        r["sr_room"] = round(abs(opp[0]["price"] - price) / atr, 2) if opp and atr else None
        r["sr_strength"] = own[0]["strength"] if own else None
        k = (r["day"], r["side"])
        seen[k] = seen.get(k, 0) + 1
        r["nth_today"] = seen[k]
    return rows


def sr_reversal_events(cs, replayed, interval, symbol, variant="with_trend", touch_atr=0.25, max_break_atr=0.5):
    """Entries at a support (CE) / resistance (PE) REVERSAL, as the owner suggested:
      bar i's low came within `touch_atr` of a Medium/Strong support (or broke it by less than `max_break_atr`), and the bar CLOSED back
      above it, bullish (close > open, close in the upper half of the bar) - mirror for PE at resistance;
      inside the live entry windows; one trade per level per day and one trade at a time.
      variant 'with_trend': the engine must also lean the same side on that bar (score gate not required);
      variant 'any': the reversal candle alone."""
    rec = {r["i"]: r for r in replayed}
    cache, out, used, busy = {}, [], set(), -1
    for i in sorted(rec):
        r = rec[i]
        atr = r.get("atr")
        if not atr or i <= busy or i + 1 >= len(cs):
            continue
        c = cs[i]
        t = c["_dt"].astimezone(IST)
        if not _sf.in_entry_window(t.hour * 60 + t.minute + interval):
            continue
        sr = _sr_at(cs, i - 1, atr, interval, symbol, cache)        # levels known BEFORE this bar
        if not sr:
            continue
        rng = max(c["high"] - c["low"], 1e-9)
        below, above = _levels_around(sr, c["close"])
        for side in ("CE", "PE"):
            if variant == "with_trend" and r.get("side") != side:
                continue
            lv = [l for l in (below if side == "CE" else above) if l["strength"] in ("Strong", "Medium")]
            if not lv:
                continue
            L = lv[0]["price"]
            if side == "CE":
                ok = (c["low"] <= L + touch_atr * atr and c["low"] >= L - max_break_atr * atr and c["close"] > L
                      and c["close"] > c["open"] and (c["close"] - c["low"]) / rng >= 0.5)
            else:
                ok = (c["high"] >= L - touch_atr * atr and c["high"] <= L + max_break_atr * atr and c["close"] < L
                      and c["close"] < c["open"] and (c["high"] - c["close"]) / rng >= 0.5)
            key = (t.date(), side, round(L / max(1.0, 0.5 * atr)))
            if ok and key not in used:
                used.add(key)
                out.append({**r, "side": side, "level": L, "level_strength": lv[0]["strength"]})
                busy = i + 6                                  # one at a time (about the median trade length)
                break
    return out


def sr_reversal_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop, symbol):
    days = sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    base_tr = [r["strat"][TRAIL_NAME] for r in conf_rows if TRAIL_NAME in r.get("strat", {})]
    out = {"baseline_trail": stats(base_tr), "variants": []}
    for variant, label in (("with_trend", "Reversal at support / resistance, engine leaning the same side"),
                           ("any", "Reversal candle at support / resistance only (no trend filter)")):
        rows = evaluate(cs, sr_reversal_events(cs, replayed, interval, symbol, variant), interval, horizon, cost, target, stop, with_extras=True)
        tr = [r["strat"][TRAIL_NAME] for r in rows if TRAIL_NAME in r.get("strat", {})]
        v = {"variant": variant, "label": label, "trail": stats(tr), "points": stats([r["pnl"] for r in rows]),
             "fixed_atr": stats([r["atr_grid"]["1.5/1.5"] for r in rows if "1.5/1.5" in r.get("atr_grid", {})]),
             "trades": [{"day": r["day"], "time": r["hhmm"], "side": r["side"], "level": round(r["level"], 1), "strength": r["level_strength"],
                         "pts": round(r["strat"].get(TRAIL_NAME, 0), 1)} for r in rows][:40]}
        if cut:
            v["first"] = stats([r["strat"][TRAIL_NAME] for r in rows if r["day"] < cut and TRAIL_NAME in r.get("strat", {})])
            v["second"] = stats([r["strat"][TRAIL_NAME] for r in rows if r["day"] >= cut and TRAIL_NAME in r.get("strat", {})])
        e, f_, s_ = v["trail"], v.get("first") or {}, v.get("second") or {}
        both = bool(f_.get("n")) and bool(s_.get("n")) and f_.get("avg", 0) > 0 and s_.get("avg", 0) > 0
        better = e.get("n") and out["baseline_trail"].get("n") and e["avg"] > out["baseline_trail"]["avg"]
        v["verdict"] = ("no reversal entries in this period" if not e.get("n") else
                        "beats the current signals - positive in both halves with 15+ trades; paper-test it" if both and e["n"] >= 15 and better else
                        "positive in both halves but not better than the current signals" if both and e["n"] >= 15 else
                        "positive but not proven (need 15+ trades, positive in both halves)" if e.get("avg", 0) > 0 else
                        "loses - do not add it")
        out["variants"].append(v)
    return out


# ---------------------------------------------------------------- v1.20.10 entry window split by time slot
WINDOW_SLOTS = (("09:15-09:45 (before the morning window)", 0, 9 * 60 + 45), ("12:00-14:30 (midday gap)", 12 * 60, 14 * 60 + 30),
                ("15:00-15:20 (after the afternoon window)", 15 * 60, 24 * 60))
TRAIL_NAME = "ATR trail: stop 1.5, trail 0.75 once +1.2"


def window_slots(cs, near, interval, horizon, cost, target, stop):
    """Setups blocked ONLY by the entry time window, split by when they happened, so a slot can be judged on its own.
    Three exits: the points exit of the run, the fixed 1.5 / 1.5 ATR plan and the live trailing plan (with first / second half)."""
    evs = [e for e in near if e.get("gate") == "Entry time window (live app)"]
    rows = evaluate(cs, evs, interval, horizon, cost, target, stop, with_extras=True)
    days = sorted({r["day"] for r in rows})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    out = []
    for label, lo, hi in WINDOW_SLOTS:
        rs = [r for r in rows if lo <= r["minute"] + interval < hi]
        tr = [r["strat"][TRAIL_NAME] for r in rs if TRAIL_NAME in r.get("strat", {})]
        d = {"slot": label, "points": stats([r["pnl"] for r in rs]), "fixed_atr": stats([r["atr_grid"]["1.5/1.5"] for r in rs if "1.5/1.5" in r.get("atr_grid", {})]),
             "trail": stats(tr)}
        if cut:
            d["trail_first"] = stats([r["strat"][TRAIL_NAME] for r in rs if r["day"] < cut and TRAIL_NAME in r.get("strat", {})])
            d["trail_second"] = stats([r["strat"][TRAIL_NAME] for r in rs if r["day"] >= cut and TRAIL_NAME in r.get("strat", {})])
        f, s_ = d.get("trail_first") or {}, d.get("trail_second") or {}
        both = bool(f.get("n")) and bool(s_.get("n")) and f.get("avg", 0) > 0 and s_.get("avg", 0) > 0
        n = d["trail"].get("n", 0)
        d["verdict"] = ("no blocked setups" if not n else
                        "worth opening - positive in both halves with 15+ setups (paper-test first)" if both and n >= 15 and d["trail"]["avg"] > 0 else
                        "promising, too few setups (need 15+, positive in both halves)" if d["trail"].get("avg", 0) > 0 else
                        "keep closed - these setups lose")
        out.append(d)
    return out


# ---------------------------------------------------------------- v1.20.36 fade: take the OPPOSITE side of each confirmed signal
def fade_whatif(cs, confirmed, conf_rows, interval, horizon, cost, target, stop):
    """BANKNIFTY moved ~2.3 ATR against the pullback signals (6 Oct - 6 Nov). If that is systematic, buying the other side at the
    same moment would pay. Same bars, same exits, side flipped. One sample - treat a good result as a lead to re-test, not proof."""
    days = sorted({r["day"] for r in conf_rows})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    tr = lambda rows: [r["strat"][TRAIL_NAME] for r in rows if TRAIL_NAME in r.get("strat", {})]
    flipped = [{**e, "side": "PE" if e.get("side") == "CE" else "CE"} for e in confirmed if e.get("side")]
    rows = evaluate(cs, flipped, interval, horizon, cost, target, stop, with_extras=True)
    v = {"label": "Fade: buy the opposite side of each confirmed signal", "trail": stats(tr(rows)),
         "fixed_atr": stats([r["atr_grid"]["1.5/1.5"] for r in rows if "1.5/1.5" in r.get("atr_grid", {})]),
         "baseline": stats(tr(conf_rows))}
    if cut:
        v["first"] = stats(tr([r for r in rows if r["day"] < cut]))
        v["second"] = stats(tr([r for r in rows if r["day"] >= cut]))
    e, f_, s_, fx = v["trail"], v.get("first") or {}, v.get("second") or {}, v["fixed_atr"]
    both = bool(f_.get("n")) and bool(s_.get("n")) and f_.get("avg", 0) > 0 and s_.get("avg", 0) > 0
    solid = (e.get("se") or 0) > 0 and e.get("avg", 0) >= e["se"] and fx.get("avg", -1) > 0
    v["verdict"] = ("no trades" if not e.get("n") else
                    "works - positive in both halves, 15+ trades, beats its noise and wins with a fixed exit; worth a paper test"
                    if both and e["n"] >= 15 and solid else
                    "positive in both halves but not proven (needs 15+ trades, beating its noise, and a positive fixed exit)"
                    if both and e.get("avg", 0) > 0 else
                    "positive, not proven" if e.get("avg", 0) > 0 else "loses - fading does not pay either")
    return v


# ---------------------------------------------------------------- v1.20.34 rupees per day for 1 / 2 / 3 lots
PLAN_KEYS = {"t1.5": "1.5/1.5", "t2": "2/1.5", "t3": "3/1.5"}
DEFAULT_LOTS = {"NIFTY": 65, "BANKNIFTY": 30}       # fallback only, when the option chain does not give the lot size


def rupee_estimate(conf_rows, days, plan, cost, lot, delta, charges, lots=(1, 2, 3), goal=2000.0):
    """Estimated option profit in ₹ per trading day. Each confirmed trade's index points (before the cost allowance) x option delta x
    quantity, minus charges per lot. Days without a trade count as ₹0. Assumes the delta stays the same during the trade (in reality
    an ITM option's delta rises as it wins and falls as it loses), no slippage, and that you take every signal."""
    def pts(r):
        if plan == "trail":
            v = (r.get("strat") or {}).get(TRAIL_NAME)
        else:
            v = (r.get("atr_grid") or {}).get(PLAN_KEYS.get(plan, "1.5/1.5"))
        return None if v is None else v + cost
    per_day = {d: [] for d in days}
    for r in conf_rows:
        g = pts(r)
        if g is not None:
            per_day.setdefault(r["day"], []).append(g)
    out = []
    for n in lots:
        qty = lot * n
        daily = [sum(g * delta * qty - charges * n for g in v) for d, v in sorted(per_day.items())]
        if not daily:
            continue
        eq, peak, dd = 0.0, 0.0, 0.0
        for x in daily:
            eq += x
            peak = max(peak, eq)
            dd = min(dd, eq - peak)
        traded = [x for x in daily if x != 0]
        out.append({"lots": n, "qty": qty, "avg_day": round(sum(daily) / len(daily)), "total": round(sum(daily)),
                    "best_day": round(max(daily)), "worst_day": round(min(daily)), "max_drawdown": round(dd),
                    "days": len(daily), "trade_days": len(traded), "losing_days": sum(1 for x in traded if x < 0),
                    "goal_days": sum(1 for x in daily if x >= goal),
                    "avg_trade_rs": round(sum(traded) / max(1, sum(len(v) for v in per_day.values()))) if traded else 0})
    return {"plan": plan, "lot": lot, "delta": delta, "charges_per_lot": charges, "goal": goal, "rows": out,
            "trades": sum(len(v) for v in per_day.values())}


# ---------------------------------------------------------------- entry points
def run(raw_candles, symbol="NIFTY", interval=5, mode="scalp", htf_raw=None, htf_interval=None, volume_source=None,
        horizon=12, cost=4.0, target=30, stop=20, progress=None, score_min=60, live_window=True, pattern_minutes=None, live_plan=None, tune=None, money=None):
    cs, replayed = replay(raw_candles, symbol, interval, mode, htf_raw, htf_interval, volume_source, progress=progress, score_min=score_min,
                          live_window=live_window, pattern_minutes=pattern_minutes, tune=tune)
    confirmed, near = events_from(replayed)
    conf_rows = evaluate(cs, confirmed, interval, horizon, cost, target, stop, with_extras=True)
    near_rows = evaluate(cs, near, interval, horizon, cost, target, stop)
    days = sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    random_stats = random_entry_baseline(cs, interval, horizon, cost, target, stop)
    import chart_patterns as _cpm
    params = {"symbol": symbol, "interval": interval, "mode": mode, "horizon_bars": horizon, "cost": cost, "target": target, "stop": stop,
              "pattern_minutes": _cpm._clean(pattern_minutes or _cpm.PATTERN_MINUTES),
              "indicator_values": __import__("indicators").tune_values(tune), "indicator_changes": __import__("indicators").tune_changes(tune),
              "days": days, "bars_evaluated": len(replayed)}
    try:
        sr_features(cs, conf_rows, interval, symbol)
    except Exception:
        pass
    rep = build_report(conf_rows, near_rows, params, random_stats)
    try:                       # v1.19.5: how the live exit plan (stop >= 1.5 ATR, target per plan) did on these signals
        import option_plan as _op
        sl_m, _mp, tg_m, plan_key = _op.plan_levels("t1.5")      # the fixed 1.5 / 1.5 plan; the trailing plan is the "ATR trail" row
        k = f"{tg_m:g}/{sl_m:g}"
        days_ = sorted({r["day"] for r in conf_rows})
        cut = days_[len(days_) // 2] if len(days_) >= 4 else None
        vals = [r["atr_grid"][k] for r in conf_rows if k in r.get("atr_grid", {})]
        if vals:
            a_ = stats(vals)
            halves = ""
            if cut:
                h1 = stats([r["atr_grid"][k] for r in conf_rows if r["day"] < cut and k in r.get("atr_grid", {})])
                h2 = stats([r["atr_grid"][k] for r in conf_rows if r["day"] >= cut and k in r.get("atr_grid", {})])
                if h1.get("n") and h2.get("n"):
                    halves = f" First half {h1['win_rate']}% / {h1['avg']:+.1f}, second half {h2['win_rate']}% / {h2['avg']:+.1f}."
            rep["live_exit"] = {"plan": plan_key, "stop_atr": sl_m, "target_atr": tg_m, **a_}
            rep["verdict"].insert(0, {"level": "good" if a_["avg"] > 0 else "bad",
                                      "text": f"Fixed exit (stop {sl_m:g} ATR / target {tg_m:g} ATR, square-off 15:20): {a_['n']} trades, {a_['win_rate']}% winners, "
                                              f"{a_['avg']:+.1f} pts per trade ({a_['total']:+.0f} total).{halves}"})
            # v1.20.23: the live default is the trailing plan - report it as the live result, with both halves
            tv = [r["strat"][TRAIL_NAME] for r in conf_rows if TRAIL_NAME in (r.get("strat") or {})]
            if tv:
                t_ = stats(tv)
                th = ""
                if cut:
                    t1 = stats([r["strat"][TRAIL_NAME] for r in conf_rows if r["day"] < cut and TRAIL_NAME in (r.get("strat") or {})])
                    t2 = stats([r["strat"][TRAIL_NAME] for r in conf_rows if r["day"] >= cut and TRAIL_NAME in (r.get("strat") or {})])
                    if t1.get("n") and t2.get("n"):
                        th = f" First half {t1['win_rate']}% / {t1['avg']:+.1f}, second half {t2['win_rate']}% / {t2['avg']:+.1f}."
                rep["live_trail"] = {"plan": "trail", **t_}
                is_live = (live_plan or "trail") == "trail"
                rep["verdict"].insert(0, {"level": "good" if t_["avg"] > 0 else "bad",
                                          "text": ("Live exit plan" if is_live else "Trailing plan (not your live plan)")
                                                  + f" (stop 1.5 ATR, trail 0.75 ATR once +1.2 ATR, square-off 15:20): {t_['n']} trades, {t_['win_rate']}% winners, "
                                                    f"{t_['avg']:+.1f} pts per trade ({t_['total']:+.0f} total).{th}"
                                                  + ("" if is_live else " Your Signals page is set to a fixed exit - the trailing plan did better here.")})
            atr_st = [x for x in rep.get("strategies", []) if x["name"].startswith("ATR ") and x.get("n", 0) >= MIN_N]
            if atr_st:
                top = max(atr_st, key=lambda x: x["avg"])
                better = top["avg"] > a_["avg"] + 1.0
                rep["verdict"].insert(1, {"level": "action" if better else "info",
                                          "text": f"Same 1.5 ATR stop, managed exit: “{top['name']}” → {top['win_rate']}% winners, {top['avg']:+.1f} pts per trade "
                                                  f"(n={top['n']}) versus {a_['avg']:+.1f} for the fixed 1.5/1.5 ATR plan. "
                                                  + ("Letting winners run with a trailing stop beats the fixed target here — re-check on the next run before switching."
                                                     if better else "The fixed 1.5 ATR target is as good or better — keep it.")})
    except Exception:
        pass
    wf = winrate_finder(conf_rows)
    rep["winrate_finder"] = wf
    if wf.get("split"):
        if wf["cells"]:
            b = wf["cells"][0]
            unit = " ATR" if b["kind"] == "ATR" else " pts"
            rep["verdict"].insert(1, {"level": "good", "text": f"≥ 70 % winners in BOTH halves of the period: target {b['target']:g}{unit} / stop {b['stop']:g}{unit} → "
                                      f"{b['all']['win_rate']}% winners, {b['all']['avg']:+.1f} pts/trade overall (first half {b['first']['win_rate']}% / {b['first']['avg']:+.1f}, "
                                      f"second half {b['second']['win_rate']}% / {b['second']['avg']:+.1f})."
                                      + (" Its stop is tighter than the 1.5 ATR minimum the live plan keeps, so it is shown for comparison only." if (b['kind'] != 'ATR' and b['stop'] < 1.5 * (rep.get('median_atr') or 0)) or (b['kind'] == 'ATR' and b['stop'] < 1.5) else " You can set it on the Signals page (custom target / stop in ATR) if it holds on the next run.")})
        else:
            nb = wf["near"][0] if wf["near"] else None
            rep["verdict"].insert(1, {"level": "info", "text": "No exit reached 70 % winners with a positive average in BOTH halves of this period."
                                      + (f" Closest: target {nb['target']:g}{' ATR' if nb['kind'] == 'ATR' else ' pts'} / stop {nb['stop']:g}{' ATR' if nb['kind'] == 'ATR' else ' pts'} "
                                         f"— worst half {nb['worst_win']}% winners, {nb['worst_avg']:+.1f} pts/trade." if nb else "")
                                      + " A higher win rate here would only come from a smaller target than stop, which these signals do not pay for."})
    try:                       # v1.20.16: does the chart-pattern rule beat the last-candle / pullback-only rule?
        pr = pattern_rule_compare(cs, replayed, interval, horizon, cost, target, stop, mode)
        rep["pattern_rule"] = pr
        o, n_ = pr["old"]["trail"], pr["new"]["trail"]
        rep["verdict"].insert(0, {"level": "action" if pr["verdict"].startswith(("pattern rule better", "pattern rule worse")) else "info",
                                  "text": f"Chart-pattern rule (pattern breakout can confirm, opposite breakout blocks) vs old rule, live trailing exit: "
                                          f"{n_.get('n', 0)} trades {n_.get('avg', 0):+.1f} pts each ({n_.get('total', 0):+.0f} total) vs {o.get('n', 0)} trades "
                                          f"{o.get('avg', 0):+.1f} ({o.get('total', 0):+.0f}); added {pr['added']['trail'].get('n', 0)}, removed {pr['removed']['trail'].get('n', 0)}. "
                                          f"Verdict: {pr['verdict']}. Bars with a pattern breakout on the engine's side: {pr.get('pattern_bars', 0)}"
                                          + (f"; what else blocked them: " + ", ".join(f"{b['gate']} ({b['bars']})" for b in pr.get('pattern_blockers', [])[:3]) if pr.get("pattern_blockers") else "") + "."})
    except Exception as exc:
        rep["pattern_rule"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:                       # v1.20.22: Opening Range Breakout - a separate strategy, measured on the same days
        ob = orb_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop)
        rep["orb"] = ob
        best = max((v for v in ob["variants"] if v["trail"].get("n")), key=lambda v: v["trail"].get("total", 0), default=None)
        if best:
            rep["verdict"].insert(2, {"level": "action" if best["verdict"].startswith("works") else "info",
                                      "text": f"Opening Range Breakout (not used by the app) - best variant “{best['label']}”: {best['trail']['n']} trades, "
                                              f"{best['trail']['win_rate']}% winners, {best['trail']['avg']:+.1f} pts each, {best['trail']['total']:+.0f} total (live trailing exit) "
                                              f"vs current signals {ob['baseline'].get('avg', 0):+.1f} each. Verdict: {best['verdict']}."})
    except Exception as exc:
        rep["orb"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:                       # v1.20.20: leading triggers instead of the lagging pullback trigger
        ld = leading_compare(cs, replayed, interval, horizon, cost, target, stop, mode)
        rep["leading"] = ld
        best = max((k for k in ld["kinds"] if k["extra"].get("n")), key=lambda k: k["combined_total"], default=None)
        if best:
            rep["verdict"].insert(1, {"level": "action" if best["verdict"].startswith("adds") else "info",
                                      "text": f"Best leading trigger: {best['label']} ({best['variant']}) adds {best['extra']['n']} trades, "
                                              f"{best['extra']['win_rate']}% winners, {best['extra']['avg']:+.1f} pts each (live trailing exit); "
                                              f"total {best['combined_total']:+.0f} vs {ld['baseline_total']:+.0f} now. Verdict: {best['verdict']}."})
        else:
            rep["verdict"].insert(1, {"level": "info", "text": "Leading triggers (RSI divergence, Stochastic, MACD turn) added no entries in this period."})
    except Exception as exc:
        rep["leading"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:                       # v1.20.13: entries at support / resistance reversals (the owner's idea), measured
        sv = sr_reversal_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop, symbol)
        rep["sr_reversal"] = sv
        v0 = sv["variants"][0]
        if v0["trail"].get("n"):
            rep["verdict"].append({"level": "action" if v0["verdict"].startswith("beats") else "info",
                                   "text": f"What-if “{v0['label']}”: {v0['trail']['n']} trades, {v0['trail']['win_rate']}% winners, {v0['trail']['avg']:+.1f} pts each "
                                           f"(live trailing exit) vs {sv['baseline_trail'].get('avg', 0):+.1f} for the current signals. Verdict: {v0['verdict']}."})
        g = rep["groups"].get("Signal of the day (same side)") or []
        later = next((x for x in g if x["label"] == "2nd or later"), None)
        first = next((x for x in g if x["label"] == "1st"), None)
        if later and first and later.get("n", 0) >= 3:
            rep["verdict"].append({"level": "action" if later["avg"] < 0 < first["avg"] else "info",
                                   "text": f"Same-side signals of the day: 1st {first['n']} trades {first['win_rate']}% / {first['avg']:+.1f} pts; "
                                           f"2nd or later {later['n']} trades {later['win_rate']}% / {later['avg']:+.1f} pts."
                                           + (" Later signals of the day are losing - a 'one trade per side per day' limit is worth testing." if later["avg"] < 0 < first["avg"] else "")})
    except Exception as exc:
        rep["sr_reversal"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:                       # v1.20.10: which part of the entry-window block costs / saves money
        ws = window_slots(cs, near, interval, horizon, cost, target, stop)
        rep["window_slots"] = ws
        parts = [f"{w['slot'].split(' ')[0]}: {w['trail']['n']} setups, {w['trail']['avg']:+.1f} pts ({w['verdict'].split(' - ')[0]})" for w in ws if w["trail"].get("n")]
        if parts:
            rep["verdict"].append({"level": "action" if any(w["verdict"].startswith("worth") for w in ws) else "info",
                                   "text": "Entry window by time slot (setups blocked only by the window, live trailing exit): " + "; ".join(parts) + "."})
    except Exception as exc:
        rep["window_slots"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:                       # v1.20.7 what-if (Diagnostics only - live signals unchanged)
        td = trend_day_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop)
        rep["trend_day"] = td
        b = td["variants"][0]
        if b["extra"].get("n"):
            e = b["extra"]
            rep["verdict"].append({"level": "action" if b["verdict"].startswith("promising") else "info",
                                   "text": f"What-if “{b['label']}”: {e['n']} extra trades, {e['win_rate']}% winners, {e['avg']:+.1f} pts each at the live "
                                           f"1.5 / 1.5 ATR exit (signals + extra: {b['combined']['n']} trades, {b['combined']['avg']:+.1f} pts each). Verdict: {b['verdict']}."})
    except Exception as exc:
        rep["trend_day"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:                       # v1.20.27 what-if (Diagnostics only - live signals unchanged)
        stw = strong_trend_whatif(cs, replayed, conf_rows, interval, horizon, cost, target, stop)
        rep["strong_trend"] = stw
        for v in stw["variants"]:
            e = v["trail"]
            if e.get("n"):
                rep["verdict"].append({"level": "action" if v["verdict"].startswith("works") else "info",
                                       "text": f"What-if “{v['label']}”: {e['n']} extra trades, {e['win_rate']}% winners, {e['avg']:+.1f} pts each "
                                               f"(live trailing exit; fixed 1.5/1.5 ATR {v['fixed_atr'].get('avg', 0):+.1f}) vs current signals "
                                               f"{stw['baseline'].get('avg', 0):+.1f}. Verdict: {v['verdict']}."})
            else:
                rep["verdict"].append({"level": "info", "text": f"What-if “{v['label']}”: no extra trades in this period (no strong-trend bar was blocked only by RSI)."})
    except Exception as exc:
        rep["strong_trend"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    mm = missed_moves(cs, replayed, horizon=horizon)
    rep["missed_moves"] = mm
    if mm["moves"]:
        top = mm["blockers"][0] if mm["blockers"] else None
        rep["verdict"].insert(1, {"level": "action" if top and mm["missed"] >= 3 else "info",
                                  "text": f"Big moves: {mm['moves']} clean moves of ≥ 2.5 ATR in this period; the engine confirmed {mm['caught']} in time and missed {mm['missed']}."
                                  + (f" The gate that blocked the most missed moves: “{top['gate']}” ({top['moves']} of {mm['missed']}, {top['pct']}%). "
                                     "Check the near-miss table below before relaxing it: if the setups it alone blocked lost money, it is protecting you." if top else "")})
    try:                       # v1.20.36 what-if: fade the confirmed signals
        fd = fade_whatif(cs, confirmed, conf_rows, interval, horizon, cost, target, stop)
        rep["fade"] = fd
        e = fd["trail"]
        if e.get("n"):
            rep["verdict"].append({"level": "action" if fd["verdict"].startswith("works") else "info",
                                   "text": f"What-if “{fd['label']}”: {e['n']} trades, {e['win_rate']}% winners, {e['avg']:+.1f} pts each (live trailing exit; "
                                           f"fixed 1.5/1.5 ATR {fd['fixed_atr'].get('avg', 0):+.1f}; first half {(fd.get('first') or {}).get('avg', 0):+.1f}, "
                                           f"second half {(fd.get('second') or {}).get('avg', 0):+.1f}) vs the signals themselves {fd['baseline'].get('avg', 0):+.1f}. Verdict: {fd['verdict']}."})
    except Exception as exc:
        rep["fade"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    try:                       # v1.20.34: what the signals mean in rupees per day, for 1 / 2 / 3 lots
        m = money or {}
        lot = int(m.get("lot") or DEFAULT_LOTS.get(symbol, 65))
        delta = float(m.get("delta") or 0.65)
        charges = float(m.get("charges") or 60.0)
        plan = live_plan if live_plan in ("trail", "t1.5", "t2", "t3") else "trail"
        ru = rupee_estimate(conf_rows, days, plan, cost, lot, delta, charges, goal=float(m.get("goal") or 2000))
        ru["assumed"] = [k for k in ("lot", "delta") if not m.get(k)]
        rep["rupees"] = ru
        if ru["rows"]:
            parts = [f"{x['lots']} lot{'s' if x['lots'] > 1 else ''}: avg ₹{x['avg_day']:,}/day, worst day ₹{x['worst_day']:,}, "
                     f"₹{ru['goal']:,.0f}+ on {x['goal_days']} of {x['days']} days" for x in ru["rows"]]
            rep["verdict"].insert(0, {"level": "info",
                                      "text": f"Rupees per day (estimate, {ru['trades']} trades in {ru['rows'][0]['days']} days, exit plan {plan}, "
                                              f"lot {lot}{' (assumed)' if 'lot' in ru['assumed'] else ''}, delta {delta:g}{' (assumed)' if 'delta' in ru['assumed'] else ''}, "
                                              f"₹{charges:,.0f} charges per lot): " + "; ".join(parts) + ". Profit and loss both grow with lots."})
    except Exception as exc:
        rep["rupees"] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    ch = rep["params"].get("indicator_changes") or {}
    if ch:                     # v1.20.33: this run used indicator values other than the tested defaults - say so first
        names = {"rsi_ce_min": "CE RSI min", "rsi_ce_max": "CE RSI max", "rsi_pe_min": "PE RSI min", "rsi_pe_max": "PE RSI max",
                 "adx_min": "ADX min", "chase_atr": "Not chasing (ATR)", "rsi_slope": "RSI direction rule"}
        rep["verdict"].insert(0, {"level": "info", "text": "Indicator values in this run differ from the tested defaults: "
                                  + ", ".join(f"{names.get(k, k)} {v}" for k, v in ch.items())
                                  + ". Compare with a run on the default values before applying them to live signals."})
    return rep


def _load(path):
    with open(path) as f:
        data = json.load(f)
    return data.get("candles", data) if isinstance(data, dict) else data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python diagnostics.py candles.json [scalp|standard] [interval] [cost_points]\n"
              "candles.json = the 'candles' array from /api/candles?symbol=NIFTY&interval=5&days=20")
        sys.exit(1)
    mode = sys.argv[2] if len(sys.argv) > 2 else "scalp"
    interval = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    cost = float(sys.argv[4]) if len(sys.argv) > 4 else 4.0
    rep = run(_load(sys.argv[1]), mode=mode, interval=interval, cost=cost)
    for v in rep["verdict"]:
        print(f"[{v['level'].upper()}] {v['text']}")
