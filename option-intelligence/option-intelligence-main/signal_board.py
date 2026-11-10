"""Signal board (v1.19.5): every option signal the app creates, recorded automatically, with what the option did afterwards.

The Daily Signal Log keeps only the BUY trades. The board keeps every stage of every option signal, so the rules can be judged
on all of them - including the ones that were NOT bought:

  WATCH    a CE / PE setup is forming; the option 2 strikes ITM was picked
  SETUP    every gate passed, the reconfirmation hold started
  BUY      confirmed and the profit check passed (also in the Daily Signal Log)
  NO BUY   confirmed but the profit check failed (reason recorded)
  FAILED   the setup did not hold through the reconfirmation

For each row, once 60 minutes have passed (or the session ended) the option's own 1-minute candles are read back and the board
records the premium after 30 / 60 minutes, the best and worst premium in that hour, and what a trade with the row's stop /
target (or, for WATCH / SETUP / FAILED rows, a 1.5 ATR stop and 1.5 ATR target on the index) would have done.
Pure sqlite / Python: no web framework.
"""
import sqlite3
from datetime import datetime, timedelta

STAGES = ("WATCH", "SETUP", "BUY", "NO BUY", "FAILED")
COLUMNS = [("created_at", "TEXT"), ("session_date", "TEXT"), ("symbol", "TEXT"), ("interval", "INTEGER"), ("mode", "TEXT"), ("side", "TEXT"),
           ("stage", "TEXT"), ("episode", "TEXT"), ("option_name", "TEXT"), ("option_key", "TEXT"), ("strike", "REAL"), ("expiry", "TEXT"),
           ("option_label", "TEXT"), ("option_ltp", "REAL"), ("index_price", "REAL"), ("atr", "REAL"), ("score", "REAL"), ("regime", "TEXT"),
           ("reason", "TEXT"), ("idx_sl", "REAL"), ("idx_target", "REAL"), ("opt_entry", "REAL"), ("opt_sl", "REAL"), ("opt_target", "REAL"),
           ("rr", "REAL"), ("opt_30", "REAL"), ("opt_60", "REAL"), ("opt_max_60", "REAL"), ("opt_min_60", "REAL"), ("best_pct_60", "REAL"),
           ("worst_pct_60", "REAL"), ("test_result", "TEXT"), ("test_points", "REAL"), ("outcome_at", "TEXT"), ("outcome_note", "TEXT"),
           ("blocked_by", "TEXT"), ("chart_pattern", "TEXT"),
           # v1.20.57: derivatives filters at the signal - the mode in force, what Option A would say, the Option B score, each check
           ("deriv_mode", "TEXT"), ("deriv_a", "TEXT"), ("deriv_b_score", "REAL"), ("deriv_tech_score", "REAL"), ("deriv_checks", "TEXT"),
           ("deriv_detail", "TEXT")]     # v1.20.6: the exact rules that failed (technical gates + entry window / dashboard / reconfirm / profit check)
FIELDS = [c for c, _ in COLUMNS]


def init(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS option_signals (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, session_date TEXT NOT NULL, "
                   "symbol TEXT NOT NULL, interval INTEGER, mode TEXT, side TEXT, stage TEXT NOT NULL, episode TEXT, option_key TEXT, "
                   "UNIQUE(symbol, interval, mode, side, stage, option_key, episode))")
        have = {r[1] for r in db.execute("PRAGMA table_info(option_signals)").fetchall()}
        for col, typ in COLUMNS:
            if col not in have:
                db.execute(f"ALTER TABLE option_signals ADD COLUMN {col} {typ}")
        db.commit()


def stage_of(final, rc):
    """Board stage for a fused final signal (or None when it is not an option signal worth recording)."""
    final, rc = final or {}, rc or {}
    lvl = final.get("level")
    if lvl == "CONFIRMED" and final.get("buy"):
        return "BUY"
    if final.get("profit_rejected"):
        return "NO BUY"
    if lvl == "PENDING":
        return "SETUP"
    if rc.get("state") == "FAILED" and lvl == "WATCH":
        return "FAILED"
    if lvl == "WATCH":
        return "WATCH"
    return None


def blocked_by(a, final):
    """Names of every rule that stopped this signal from being a BUY (short labels, ' · ' separated, max 400 chars)."""
    out = []
    for g in (a or {}).get("gates") or []:
        if isinstance(g, dict) and not g.get("pass") and g.get("label"):
            out.append(str(g["label"]))
    for b in (final or {}).get("blockers") or []:
        parts = str(b).split(": ")
        lab = (": ".join(parts[:2]) if str(b).startswith("Profit check:") else parts[0]).strip()
        if lab and lab not in out:
            out.append(lab)
    return " · ".join(out)[:400]


def make_row(now, symbol, interval, mode, a, final, rc, pick, timing):
    """Row dict for the board, or None. `now` is an aware IST datetime."""
    stage = stage_of(final, rc)
    if not stage or not pick or not pick.get("instrument_key"):
        return None
    rc, timing = rc or {}, timing or {}
    # one row per stage per signal "episode": the reconfirmation attempt, else the start of the technical signal run, else the bar
    episode = str(rc.get("since") or timing.get("generated_at") or a.get("last_closed_bar") or now.strftime("%Y-%m-%d %H:%M"))
    if stage == "WATCH":
        episode = str(timing.get("lean_since_text") or timing.get("generated_at") or a.get("last_closed_bar") or episode)
    plan = final.get("buy") or (a.get("buy_plan") if stage == "NO BUY" else None) or {}
    ix, op = plan.get("index") or {}, plan.get("option") or {}
    reason = final.get("headline") or ""
    if stage in ("NO BUY", "BUY"):
        reason = plan.get("summary") or reason
    elif stage == "FAILED":
        reason = rc.get("reason") or reason
    return {"created_at": now.isoformat(timespec="seconds"), "session_date": now.date().isoformat(), "symbol": symbol, "interval": int(interval),
            "mode": mode, "side": final.get("side") or a.get("side"), "stage": stage, "episode": episode,
            "option_name": pick.get("name") or f"{symbol} {pick.get('strike')} {pick.get('type')}", "option_key": pick.get("instrument_key"),
            "strike": pick.get("strike"), "expiry": pick.get("expiry"), "option_label": pick.get("label"),
            "option_ltp": op.get("entry") or pick.get("ask") or pick.get("ltp"), "index_price": ix.get("entry") or a.get("last_close"),
            "atr": a.get("atr"), "score": a.get("score"), "regime": a.get("regime_label") or a.get("regime"), "reason": str(reason)[:400],
            "idx_sl": ix.get("stop"), "idx_target": ix.get("target"), "opt_entry": op.get("entry"), "opt_sl": op.get("stop"),
            "opt_target": op.get("target"), "rr": ix.get("rr"), "blocked_by": blocked_by(a, final) if stage != "BUY" else "",
            "chart_pattern": (lambda cp: f"{cp.get('name')} - {cp.get('detail')}"[:200] if cp and cp.get("name") not in (None, "None") else "")(a.get("chart_pattern")),
            **deriv_fields(a.get("derivatives"), final.get("side") or a.get("side"))}


def deriv_fields(d, side):
    """Board columns for the derivatives summary (empty when the filters are off)."""
    if not d:
        return {}
    import json
    want, other = ("bull", "bear") if side == "CE" else ("bear", "bull")
    per = {}
    for c in d.get("checks") or []:
        k = str(c.get("key", ""))[6:]
        if not c.get("ok"):
            per[k] = "na"
        elif k in ("vol_calm", "no_decay"):
            per[k] = "ok" if c.get(want) else "warn"
        else:
            per[k] = "agree" if c.get(want) else "against" if c.get(other) else "neutral"
    sb_, st_ = d.get("score_b") or {}, d.get("score_tech") or {}
    return {"deriv_mode": d.get("mode"), "deriv_a": "BLOCK" if d.get("would_block_a") else "PASS",
            "deriv_b_score": sb_.get("bull") if side == "CE" else sb_.get("bear"),
            "deriv_tech_score": st_.get("bull") if side == "CE" else st_.get("bear"),
            "deriv_checks": json.dumps(per, separators=(",", ":")), "deriv_detail": str(d.get("verdict") or "")[:400]}


def insert(db_path, row, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            names = [k for k in FIELDS if k in row]
            cur = db.execute(f"INSERT OR IGNORE INTO option_signals ({','.join(names)}) VALUES ({','.join('?' * len(names))})", [row[k] for k in names])
            db.commit()
            return bool(cur.rowcount)
    if lock:
        with lock:
            return run()
    return run()


def pending(db_path, now, limit=8, lock=None):
    """Rows whose 60-minute window is over (or whose session ended) and that have no outcome yet."""
    cutoff = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
    today = now.date().isoformat()
    session_over = now.hour * 60 + now.minute >= 15 * 60 + 31

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            q = ("SELECT * FROM option_signals WHERE outcome_at IS NULL AND (created_at <= ? OR session_date < ?" + (" OR session_date = ?" if session_over else "")
                 + ") ORDER BY created_at LIMIT ?")
            args = [cutoff, today] + ([today] if session_over else []) + [limit]
            return [dict(r) for r in db.execute(q, args).fetchall()]
    if lock:
        with lock:
            return run()
    return run()


def _after(bars, start, minutes, ist):
    end = start + timedelta(minutes=minutes)
    day = start.astimezone(ist).date()
    return [b for b in bars if start <= b["_dt"] < end and b["_dt"].astimezone(ist).date() == day
            and b["_dt"].astimezone(ist).hour * 60 + b["_dt"].astimezone(ist).minute < 15 * 60 + 30]


def outcome(row, opt_bars, idx_bars, ist, squareoff_first_exit=None):
    """Outcome fields for one board row. opt_bars / idx_bars: normalised 1-minute candles (aware datetimes)."""
    try:
        created = datetime.fromisoformat(str(row["created_at"]))
    except ValueError:
        return {"outcome_note": "bad time"}
    start = created.replace(second=0, microsecond=0) + timedelta(minutes=1)
    out = {}
    o60 = _after(opt_bars or [], start, 60, ist)
    p0 = row.get("opt_entry") or row.get("option_ltp")
    if o60 and p0:
        o30 = _after(opt_bars, start, 30, ist)
        out.update(opt_30=round(o30[-1]["close"], 2) if o30 else None, opt_60=round(o60[-1]["close"], 2),
                   opt_max_60=round(max(b["high"] for b in o60), 2), opt_min_60=round(min(b["low"] for b in o60), 2))
        out["best_pct_60"] = round((out["opt_max_60"] - p0) / p0 * 100, 1)
        out["worst_pct_60"] = round((out["opt_min_60"] - p0) / p0 * 100, 1)
    i60 = _after(idx_bars or [], start, 60, ist)
    side = str(row.get("side") or "").upper()
    entry, atr = row.get("index_price"), row.get("atr")
    if i60 and entry and side in ("CE", "PE") and squareoff_first_exit:
        sign = 1 if side == "CE" else -1
        sl, tp = row.get("idx_sl"), row.get("idx_target")
        if (sl is None or tp is None) and atr:
            sl, tp = entry - sign * max(1.5 * atr, 20), entry + sign * 1.5 * atr       # the default plan, for rows that had none
        if sl is not None and tp is not None:
            path = [dict(b, _dt=b["_dt"].astimezone(ist)) for b in i60]
            from datetime import time as dtime
            status, px, _bar, _p, _w = squareoff_first_exit(path, sign, entry, sl, tp, dtime(15, 20))
            if status is None:
                status, px = "OPEN 60 min", path[-1]["close"]
            out["test_result"] = status
            out["test_points"] = round((px - entry) * sign, 1)
    out["outcome_at"] = datetime.now(ist).isoformat(timespec="seconds")
    if not out.get("opt_60") and not out.get("test_result"):
        out["outcome_note"] = "no candles for this option / time"
    return out


def save_outcome(db_path, rid, fields, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            ks = [k for k in fields if k in FIELDS]
            db.execute(f"UPDATE option_signals SET {','.join(k + '=?' for k in ks)} WHERE id=?", [fields[k] for k in ks] + [rid])
            db.commit()
    if lock:
        with lock:
            return run()
    return run()


def query(db_path, start="", end="", symbol="", stage="", limit=5000, lock=None):
    clauses, params = [], []
    if start:
        clauses.append("session_date >= ?"); params.append(start)
    if end:
        clauses.append("session_date <= ?"); params.append(end)
    if symbol:
        clauses.append("symbol = ?"); params.append(symbol.upper())
    if stage:
        clauses.append("stage = ?"); params.append(stage.upper())
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM option_signals" + where + " ORDER BY created_at DESC LIMIT ?",
                                                params + [max(1, min(int(limit), 20000))]).fetchall()]
    if lock:
        with lock:
            return run()
    return run()


def summary(rows):
    """Per stage: how many, how many have an outcome, how often the premium rose >= 20 % within the hour, the test-trade result."""
    out = []
    for st in STAGES:
        rs = [r for r in rows if r.get("stage") == st]
        done = [r for r in rs if r.get("best_pct_60") is not None]
        tests = [r for r in rs if r.get("test_result")]
        tgt = sum(1 for r in tests if r["test_result"] == "TARGET")
        stp = sum(1 for r in tests if r["test_result"] == "STOP")
        pts = [r["test_points"] for r in tests if r.get("test_points") is not None]
        out.append({"stage": st, "count": len(rs), "with_outcome": len(done),
                    "pct_up20": round(100.0 * sum(1 for r in done if r["best_pct_60"] >= 20) / len(done), 1) if done else None,
                    "avg_best_pct": round(sum(r["best_pct_60"] for r in done) / len(done), 1) if done else None,
                    "avg_worst_pct": round(sum(r["worst_pct_60"] for r in done if r.get("worst_pct_60") is not None) / len(done), 1) if done else None,
                    "test_target": tgt, "test_stop": stp, "test_open": len(tests) - tgt - stp,
                    "test_win_rate": round(100.0 * tgt / (tgt + stp), 1) if (tgt + stp) else None,
                    "test_avg_points": round(sum(pts) / len(pts), 1) if pts else None})
    return out


# ---- v1.20.57: effectiveness of each derivatives-filter option, from the signal board's own test outcomes ----------------------
DERIV_CHECK_LABELS = {"fut_oi": "Futures price + OI buildup", "atm_oi": "Option writers near ATM", "pcr_trend": "PCR trend",
                      "heavy": "Heavyweights vs VWAP", "vol_calm": "Volatility calm (IV / VIX)", "no_decay": "Not a premium-decay range"}


def _stats(name, rs, note=""):
    tests = [r for r in rs if r.get("test_result")]
    tgt = sum(1 for r in tests if r["test_result"] == "TARGET")
    stp = sum(1 for r in tests if r["test_result"] == "STOP")
    pts = [float(r["test_points"]) for r in tests if r.get("test_points") is not None]
    best = [r["best_pct_60"] for r in rs if r.get("best_pct_60") is not None]
    return {"group": name, "note": note, "signals": len(rs), "tested": len(tests), "target": tgt, "stop": stp,
            "win_rate": round(100.0 * tgt / (tgt + stp), 1) if (tgt + stp) else None,
            "avg_points": round(sum(pts) / len(pts), 1) if pts else None, "net_points": round(sum(pts), 1) if pts else None,
            "avg_best_pct": round(sum(best) / len(best), 1) if best else None}


def one_per_setup(rows, minutes=15):
    """One row per setup: board rows of the same index + side within `minutes` of the first are the same setup (WATCH -> SETUP -> BUY)."""
    keep, last = [], {}
    for r in sorted(rows, key=lambda r: str(r.get("created_at"))):
        try:
            t = datetime.fromisoformat(str(r["created_at"]))
        except (ValueError, KeyError):
            continue
        k = (r.get("symbol"), r.get("side"), r.get("session_date"))
        if k in last and (t - last[k]).total_seconds() < minutes * 60:
            continue
        last[k] = t
        keep.append(r)
    return keep


def deriv_study(rows, score_min=60):
    """Per option: Off (every signal) / Option A pass vs block / Option B score >= score_min vs below, and every check on its own.
    Only rows recorded with the filters on (record / gates / score) carry the readings."""
    import json
    rows = one_per_setup([r for r in rows if r.get("side") in ("CE", "PE")])
    rec = [r for r in rows if r.get("deriv_a")]
    out = {"setups": len(rows), "with_readings": len(rec), "score_min": score_min,
           "by_mode_recorded": {m: sum(1 for r in rec if r.get("deriv_mode") == m) for m in ("record", "gates", "score")},
           "options": [], "checks": []}
    out["options"].append(_stats("Off - every signal (baseline)", rec, "what you get without the filters"))
    a_pass = [r for r in rec if r.get("deriv_a") == "PASS"]
    out["options"].append(_stats("Option A - signals it lets through", a_pass, "trades you would take with Option A"))
    out["options"].append(_stats("Option A - signals it blocks", [r for r in rec if r.get("deriv_a") == "BLOCK"], "good filter: these lose"))
    b_pass = [r for r in rec if r.get("deriv_b_score") is not None and r["deriv_b_score"] >= score_min]
    out["options"].append(_stats(f"Option B - score ≥ {score_min} with derivatives", b_pass, "trades you would take with Option B"))
    out["options"].append(_stats(f"Option B - score drops below {score_min}", [r for r in rec if r.get("deriv_b_score") is not None and r["deriv_b_score"] < score_min],
                                 "good filter: these lose"))
    out["options"].append(_stats("Option A and B both pass", [r for r in a_pass if r in b_pass], "strictest combination"))
    for k, lab in DERIV_CHECK_LABELS.items():
        groups = {}
        for r in rec:
            try:
                v = (json.loads(r.get("deriv_checks") or "{}") or {}).get(k, "na")
            except ValueError:
                v = "na"
            groups.setdefault(v, []).append(r)
        order = ("ok", "warn", "na") if k in ("vol_calm", "no_decay") else ("agree", "neutral", "against", "na")
        names = {"agree": "agrees with the trade", "neutral": "neutral", "against": "against the trade", "na": "no data",
                 "ok": "calm / no range", "warn": "spike / range"}
        out["checks"].append({"check": k, "label": lab, "rows": [_stats(names[v], groups.get(v, [])) for v in order]})
    out["verdict"] = _deriv_verdict(out)
    return out


def _deriv_verdict(st, min_tests=15):
    base = st["options"][0]
    if (base["tested"] or 0) < min_tests:
        return f"Not enough tested signals yet ({base['tested']} of {min_tests}). Keep the filters on Record only and check again later."
    best = None
    for o in st["options"][1:]:
        if "lets through" in o["group"] or "≥" in o["group"] or "both pass" in o["group"]:
            if (o["tested"] or 0) >= max(5, min_tests // 3) and o["avg_points"] is not None and base["avg_points"] is not None:
                gain = o["avg_points"] - base["avg_points"]
                if best is None or gain > best[1]:
                    best = (o["group"], gain, o)
    if not best or best[1] <= 0:
        return "No option beats taking every signal yet - leave the filters on Record only."
    o = best[2]
    return (f"Best so far: {best[0]} - {o['win_rate']}% win rate, {o['avg_points']:+.1f} pts per trade vs {base['win_rate']}% / "
            f"{base['avg_points']:+.1f} for every signal ({o['tested']} of {base['tested']} tested signals). Small samples can mislead.")
