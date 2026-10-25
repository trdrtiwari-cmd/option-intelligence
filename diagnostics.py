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

from indicators import IST, analyze, normalize_candles

TARGET_GRID = (15, 20, 25, 30, 40, 60)
STOP_GRID = (10, 15, 20, 25, 30, 40)
HOUR_BUCKETS = (("09:15–09:45", 555, 585), ("09:45–10:30", 585, 630), ("10:30–12:00", 630, 720),
                ("12:00–13:30", 720, 810), ("13:30–14:30", 810, 870), ("14:30–15:30", 870, 931))
MIN_N = 8          # fewer samples than this are shown but never used to draw a conclusion


# ---------------------------------------------------------------- replay
def replay(raw_candles, symbol="NIFTY", interval=5, mode="scalp", htf_raw=None, htf_interval=None, volume_source=None, first_bar=40,
           window=None, progress=None):
    """One record per closed bar from `first_bar` on: the technical signal as it stood when that bar closed."""
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
                    volume_source=volume_source, now=close_dt, mode=mode)
        if a.get("status") != "ok":
            continue
        side, atr = a.get("side"), a.get("atr")
        gates = a.get("gates") or []
        failed = [g["label"] for g in gates if not g["pass"]]
        trig = next((g for g in gates if g["label"].startswith(("Entry trigger", "Pullback-and-resume"))), None)
        trigger = None
        if trig and trig["pass"]:
            trigger = "pause break" if trig["detail"].startswith("Pause break") else "EMA 9 retest"
        stretch = None
        if side and atr:
            stretch = ((a["ema9"] - a["last_close"]) if side == "PE" else (a["last_close"] - a["ema9"])) / atr
        rsi = a.get("rsi")
        out.append({"i": i, "signal": a["signal"], "side": side, "score": a["score"], "regime": a.get("regime_label"), "phase": a.get("regime_phase"),
                    "failed": failed, "trigger": trigger, "stretch_atr": stretch, "atr": atr,
                    "rsi_ext": (rsi if side == "PE" else 100 - rsi) if (rsi is not None and side) else None})
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
        if cs[j]["_dt"].astimezone(IST).date() != day:
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


def first_touch_be(path, entry, d, target, stop, be_at):
    """Like first_touch, but once price has gone `be_at` points your way the stop moves to breakeven (entry). The move applies
    from the NEXT candle, which is slightly optimistic for the trail (a same-candle reversal is not punished), so if the trail
    still loses to no-trail here, it will lose more live."""
    cur = float(stop)
    for c in path:
        adverse = (entry - c["low"]) if d > 0 else (c["high"] - entry)
        fav = (c["high"] - entry) if d > 0 else (entry - c["low"])
        if adverse >= cur:
            return -cur, "stop"
        if fav >= target:
            return float(target), "target"
        if fav >= be_at:
            cur = 0.0
    return d * (path[-1]["close"] - entry), "time"


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


def evaluate(cs, events, interval, horizon, cost, target, stop):
    """Outcome record for every event (adds pnl at the baseline exit, excursions and the whole target x stop grid)."""
    rows = []
    day_open = {}
    for c in cs:
        day_open.setdefault(c["_dt"].astimezone(IST).date(), c["open"])
    for ev in events:
        entry, path = _path(cs, ev, interval, horizon)
        if path is None:
            continue
        d = 1 if ev["side"] == "CE" else -1
        move = cs[ev["i"]]["close"] - day_open[cs[ev["i"]]["_dt"].astimezone(IST).date()]
        mfe, mae = excursions(path, entry, d)
        pnl, why = first_touch(path, entry, d, target, stop)
        grid = {f"{t}/{s}": first_touch(path, entry, d, t, s)[0] - cost for t in TARGET_GRID for s in STOP_GRID}
        dt = cs[ev["i"]]["_dt"].astimezone(IST)
        rows.append({**ev, "day": dt.strftime("%Y-%m-%d"), "minute": dt.hour * 60 + dt.minute, "hhmm": dt.strftime("%H:%M"),
                     "entry": entry, "mfe": mfe, "mae": mae, "pnl": pnl - cost, "exit": why, "grid": grid, "bars": len(path),
                     "with_day": None if abs(move) < 1e-9 else (move * d > 0), "_path": path, "_d": d})
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


def build_report(rows_conf, rows_near, params, random_stats=None, random_fn=None):
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
        be = report["best_exit"]
        if be:
            key = f"{be['target']}/{be['stop']}"
            days = sorted({r["day"] for r in rows_conf})
            if len(days) >= 4:
                mid = days[len(days) // 2]
                report["halves"] = {"split_day": mid, "first": stats([r["grid"][key] for r in rows_conf if r["day"] < mid]),
                                    "second": stats([r["grid"][key] for r in rows_conf if r["day"] >= mid])}
            report["trail_check"] = [{"be_at": None, **stats([r["grid"][key] for r in rows_conf])}] + [
                {"be_at": b, **stats([first_touch_be(r["_path"], r["entry"], r["_d"], be["target"], be["stop"], b)[0] - cost for r in rows_conf])}
                for b in (10, 15, 20, 30) if b < be["target"]]
            if random_fn:
                report["random_at_best"] = random_fn(be["target"], be["stop"])
    report["groups"] = {
        "Time of day (IST)": _bucket(rows_conf, _hour, [h[0] for h in HOUR_BUCKETS]),
        "Side": _bucket(rows_conf, lambda r: r["side"]),
        "Entry trigger": _bucket(rows_conf, lambda r: r["trigger"] or "n/a"),
        "Regime phase": _bucket(rows_conf, lambda r: r["phase"] or r["regime"] or "n/a"),
        "Score": _bucket(rows_conf, lambda r: _band(r["score"], (80, 90), ("70–79", "80–89", "90–100")), ["70–79", "80–89", "90–100"]),
        "RSI extremity (low = exhausted)": _bucket(rows_conf, lambda r: _band(r["rsi_ext"], (20, 30, 40), ("< 20", "20–30", "30–40", "≥ 40")), ["< 20", "20–30", "30–40", "≥ 40"]),
        "Stretch beyond EMA 9": _bucket(rows_conf, lambda r: _band(r["stretch_atr"], (0.5, 1.0, 2.0, 3.5), ("< 0.5 ATR", "0.5–1", "1–2", "2–3.5", "> 3.5 ATR")), ["< 0.5 ATR", "0.5–1", "1–2", "2–3.5", "> 3.5 ATR"]),
        "With / against the session's move (open → signal)": _bucket(rows_conf, lambda r: None if r["with_day"] is None else "with the day's move" if r["with_day"] else "against the day's move",
                                                                    ["with the day's move", "against the day's move"]),
        "Day": _bucket(rows_conf, lambda r: r["day"]),
    }
    gates = {}
    for r in rows_near:
        gates.setdefault(r["gate"], []).append(r["pnl"])
    report["random_entries"] = random_stats or {"n": 0}
    report["ablation"] = sorted(({"gate": g, **stats(v)} for g, v in gates.items()), key=lambda x: -x["n"])
    report["verdict"] = verdict(report)
    return report


BEST_Z = 3.0       # bar for any result that was selected as the best of many (the best-exit cell)
SIG_Z = 2.0        # chance alone produces |z| > 1.5 about 1 time in 8; 2.0 keeps false alarms near 1 in 20 per comparison


def _sig(s, ref=0.0, min_n=15):
    """True when a mean differs from `ref` by more than SIG_Z standard errors (and the sample is not tiny)."""
    return bool(s.get("se") and s.get("n", 0) >= min_n and abs(s["avg"] - ref) > SIG_Z * s["se"])


def _diff_sig(a, b):
    """Two independent samples: is a's mean above b's by more than SIG_Z combined standard errors?"""
    if not (a.get("se") and b.get("se")) or a.get("n", 0) < MIN_N or b.get("n", 0) < MIN_N:
        return False
    return (a["avg"] - b["avg"]) > SIG_Z * math.sqrt(a["se"] ** 2 + b["se"] ** 2)


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
        lvl = "good" if edge > SIG_Z * se and base["n"] >= 30 else "bad" if edge < 0 and abs(edge) > SIG_Z * se else "info"
        out.append({"level": lvl, "text": f"Versus random entries: random CE/PE entries with the same exit made {rnd['avg']:+.1f} points per trade (n={rnd['n']}); the signals made {base['avg']:+.1f}. "
                    f"Signal edge = {edge:+.1f} points per trade" + (" — statistically real." if lvl == "good" else " — the signals are WORSE than guessing." if lvl == "bad"
                    else " — indistinguishable from random entries in this sample, so the direction/timing logic is not adding value yet; results are being driven by the exit rule and cost.")})
    if c.get("median_mfe") is not None:
        out.append({"level": "info", "text": f"After a confirmed signal the market typically went {c['median_mfe']} points your way at best and {c['median_mae']} points against you "
                    f"before the horizon. {c['pct_reach_20']}% reached +20, {c['pct_reach_30']}% reached +30, {c['pct_reach_40']}% reached +40."
                    + (f" A stop tighter than about {c['median_mae']} points is inside the normal noise of these entries and will be hit before the move develops." if c["median_mae"] else "")})
    be = rep.get("best_exit")
    if be and base.get("n"):
        cur = next((x for row in rep["grid"] for x in row if x["target"] == p["target"] and x["stop"] == p["stop"]), None)
        if be["avg"] > (cur["avg"] if cur else base["avg"]) + 0.5:
            ncells = sum(len(row) for row in rep["grid"])
            out.append({"level": "action", "text": f"Best tested exit: target {be['target']} / stop {be['stop']} → {be['avg']:+.1f} points per trade (n={be['n']}), "
                        f"versus {cur['avg']:+.1f} at {p['target']}/{p['stop']}. It is the best of {ncells} pairs, so expect less than this going forward; "
                        f"trust it more when neighbouring cells (same target, nearby stops) are also positive. Changing the paper-trade stop/target rule toward this is the first correction to test."})
        h = rep.get("halves")
        if h and h["first"].get("n", 0) >= 5 and h["second"].get("n", 0) >= 5:
            both = h["first"]["avg"] > 0 and h["second"]["avg"] > 0
            out.append({"level": "good" if both else "bad", "text": f"Stability of {be['target']}/{be['stop']}: days before {h['split_day']} {h['first']['avg']:+.1f} pts/trade (n={h['first']['n']}), "
                        f"from {h['split_day']} on {h['second']['avg']:+.1f} (n={h['second']['n']})." + (" Positive in both halves." if both else " Not positive in both halves — this exit may be fitted to one stretch of market; do not rely on it.")})
        rb = rep.get("random_at_best") or {}
        if rb.get("n", 0) >= 50 and base.get("n", 0) >= MIN_N:
            edge = be["avg"] - rb["avg"]
            se = math.sqrt((be["se"] or 0) ** 2 + (rb["se"] or 0) ** 2)
            real = edge > BEST_Z * se and be["n"] >= 30         # stricter bar: this exit was picked as the best of many, so a plain 2-SE test would flatter it
            out.append({"level": "good" if real else "info",
                        "text": f"At that same exit, random entries made {rb['avg']:+.1f} pts/trade; the signals made {be['avg']:+.1f} → gap {edge:+.1f}"
                                + (f" (more than {BEST_Z:g} standard errors, even after allowing for picking the best exit)." if real
                                   else f" (not convincing: because this exit was picked as the best of many, the gap must exceed {BEST_Z:g} standard errors to count).")})
        tc = rep.get("trail_check") or []
        if len(tc) > 1 and tc[0].get("n"):
            best_t = max(tc[1:], key=lambda x: x["avg"])
            if best_t["avg"] < tc[0]["avg"] - 0.5:
                out.append({"level": "action", "text": f"Trailing to breakeven hurts: with no trail {tc[0]['avg']:+.1f} pts/trade, best breakeven trigger (+{best_t['be_at']} pts) {best_t['avg']:+.1f}. "
                            f"Turn the paper-trade trail off for this exit (the test is slightly generous to the trail, so live results would be worse)."})
            elif best_t["avg"] > tc[0]["avg"] + 0.5:
                out.append({"level": "info", "text": f"A breakeven trail after +{best_t['be_at']} pts would have helped slightly: {best_t['avg']:+.1f} vs {tc[0]['avg']:+.1f} with no trail (within noise unless the sample is large)."})
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
            if _diff_sig(a, base):
                out.append({"level": "action", "text": f"Gate too strict? “{a['gate']}” was the ONLY thing blocking {a['n']} setups that averaged {a['avg']:+.1f} points per trade "
                            f"(confirmed signals: {ref:+.1f}) — a difference larger than chance. Relaxing this gate would have added profitable entries."})
            else:
                out.append({"level": "info", "text": f"“{a['gate']}” blocked {a['n']} setups averaging {a['avg']:+.1f} (confirmed: {ref:+.1f}), but that gap is within noise — leave the gate alone until a second period agrees."})
        elif a["avg"] < 0 and _sig(a, min_n=10):
            out.append({"level": "good", "text": f"Gate earning its keep: “{a['gate']}” blocked {a['n']} setups that would have lost {a['avg']:+.1f} points per trade. Keep it."})
    if not any(v["level"] == "action" for v in out):
        out.append({"level": "info", "text": "No single correction stands out with enough data. Use more days, or try the other signal mode and compare."})
    return out


# ---------------------------------------------------------------- entry points
def run(raw_candles, symbol="NIFTY", interval=5, mode="scalp", htf_raw=None, htf_interval=None, volume_source=None,
        horizon=12, cost=4.0, target=60, stop=20, progress=None):
    cs, replayed = replay(raw_candles, symbol, interval, mode, htf_raw, htf_interval, volume_source, progress=progress)
    confirmed, near = events_from(replayed)
    conf_rows = evaluate(cs, confirmed, interval, horizon, cost, target, stop)
    near_rows = evaluate(cs, near, interval, horizon, cost, target, stop)
    days = sorted({cs[r["i"]]["_dt"].astimezone(IST).strftime("%Y-%m-%d") for r in replayed})
    random_stats = random_entry_baseline(cs, interval, horizon, cost, target, stop)
    random_fn = lambda t, sp: random_entry_baseline(cs, interval, horizon, cost, t, sp)
    params = {"symbol": symbol, "interval": interval, "mode": mode, "horizon_bars": horizon, "cost": cost, "target": target, "stop": stop,
              "days": days, "bars_evaluated": len(replayed)}
    return build_report(conf_rows, near_rows, params, random_stats, random_fn)


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
