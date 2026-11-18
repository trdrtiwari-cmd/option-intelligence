"""Strategy monitor (v1.20.0): live discrepancy checks, why moves were missed, what caused losses, and suggested changes.

Everything that FINDS a problem is plain code here (fast, free, repeatable). Claude is only asked to EXPLAIN what was found and
to rank improvement ideas, and only when there is something new - see claude_call(). Nothing in this module places, changes or
blocks a trade: suggestions are stored for the owner to accept or ignore.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))     # v1.20.2: stamps in IST (the server runs in UTC, which showed 05:31 for 11:01)

MIN_SL_ATR = 1.5

# ---------------------------------------------------------------- settings (env defaults, owner overrides from the Signals page)
SETTINGS = {
    "enabled": os.getenv("MONITOR_ENABLED", "1") != "0",
    "claude": os.getenv("MONITOR_CLAUDE", "1") != "0",
    "push": os.getenv("MONITOR_PUSH", "1") != "0",
    "model": os.getenv("MONITOR_MODEL", "claude-haiku-5-5"),
    "review_model": os.getenv("REVIEW_MODEL", "claude-sonnet-5-5"),
    "max_calls": int(os.getenv("MONITOR_MAX_CALLS_PER_DAY", "40")),
    "review_every_min": int(os.getenv("MONITOR_REVIEW_MIN", "30")),
    # v1.20.1: which AI writes the notes / reviews. auto = Claude if ANTHROPIC_API_KEY is set, else Gemini if GEMINI_API_KEY is set.
    "provider": os.getenv("MONITOR_PROVIDER", "auto") if os.getenv("MONITOR_PROVIDER", "auto") in ("auto", "claude", "gemini", "off") else "auto",
    "gemini_model": os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest"),
}
PROVIDERS = ("auto", "claude", "gemini", "off")
GEMINI_FALLBACK = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-flash-latest")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


def api_key():
    v = os.getenv("ANTHROPIC_API_KEY", "").strip().strip('"').strip("'")
    if not v:
        for n in ("CLAUDE_API_KEY", "ANTHROPIC_KEY"):
            v = os.getenv(n, "").strip().strip('"').strip("'")
            if v:
                break
    if not v:                                       # a Claude key pasted into the Gemini variable
        g = os.getenv("GEMINI_API_KEY", "").strip()
        v = g if g.startswith("sk-ant-") else ""
    return v


GEMINI_ENV_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_KEY", "GOOGLE_AI_API_KEY", "GOOGLE_GENAI_API_KEY")
CLAUDE_ENV_NAMES = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "ANTHROPIC_KEY")


def _env_any(names):
    """First non-empty variable among `names` (v1.20.3: also tolerates spaces / lower case / quotes in the Render variable)."""
    for n in names:
        v = os.getenv(n, "").strip().strip('"').strip("'")
        if v:
            return v, n
    want = {n.replace("_", "").upper() for n in names}
    for k, v in os.environ.items():
        if k.replace("_", "").replace(" ", "").replace("-", "").upper() in want and v.strip():
            return v.strip().strip('"').strip("'"), k
    return "", None


def gemini_key():
    v, _ = _env_any(GEMINI_ENV_NAMES)
    # a Google AI Studio key starts with "AIza"; a Claude key put in the Gemini variable is ignored here (used as Claude below)
    return "" if v.startswith("sk-ant-") else v


def key_report():
    """What the server actually sees (names only, never the values)."""
    g, gn = _env_any(GEMINI_ENV_NAMES)
    c, cn = _env_any(CLAUDE_ENV_NAMES)
    near = sorted(k for k in os.environ if any(w in k.upper() for w in ("GEMINI", "ANTHROPIC", "CLAUDE", "GOOGLE")))
    return {"gemini_var": gn, "gemini_looks_valid": g.startswith("AIza") if g else None, "claude_var": cn,
            "claude_looks_valid": c.startswith("sk-ant-") if c else None, "similar_variables": near[:10],
            "provider_setting": SETTINGS.get("provider"), "ai_switch_on": SETTINGS.get("claude", True), "in_use": provider()}


def provider():
    """The AI actually in use: 'claude', 'gemini' or None (code findings only)."""
    p = SETTINGS.get("provider", "auto")
    if p == "off" or not SETTINGS.get("claude", True):
        return None
    if p == "claude":
        return "claude" if api_key() else None
    if p == "gemini":
        return "gemini" if gemini_key() else None
    return "claude" if api_key() else "gemini" if gemini_key() else None


def model_for(kind):
    prov = provider()
    if prov == "gemini":
        return SETTINGS["gemini_model"]
    if prov == "claude":
        return SETTINGS["review_model"] if kind == "eod" else SETTINGS["model"]
    return None


# ---------------------------------------------------------------- storage
EVENT_COLS = [("created_at", "TEXT"), ("day", "TEXT"), ("symbol", "TEXT"), ("kind", "TEXT"), ("severity", "TEXT"), ("title", "TEXT"),
              ("detail", "TEXT"), ("dedupe", "TEXT"), ("claude_note", "TEXT"), ("note_model", "TEXT")]
REVIEW_COLS = [("created_at", "TEXT"), ("day", "TEXT"), ("symbol", "TEXT"), ("kind", "TEXT"), ("model", "TEXT"), ("text", "TEXT"),
               ("suggestions", "TEXT"), ("context", "TEXT")]
KINDS = ("FEED", "RULE", "OPTION", "LOSS", "MISSED", "DRIFT", "SUGGESTION")
SEVERITIES = ("high", "medium", "info")


def init(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS monitor_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   + ", ".join(f"{c} {t}" for c, t in EVENT_COLS) + ", UNIQUE(day, dedupe))")
        db.execute("CREATE TABLE IF NOT EXISTS monitor_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                   + ", ".join(f"{c} {t}" for c, t in REVIEW_COLS) + ")")
        db.execute("CREATE INDEX IF NOT EXISTS monitor_events_day ON monitor_events(day)")
        # v1.20.6: board findings used to be keyed by the board row id, which changes when the board is restored after a restart,
        # and the same setup is recorded once per mode / timeframe - so one setup showed up 2-3 times. Keep the first of each.
        db.execute("DELETE FROM monitor_events WHERE kind='MISSED' AND dedupe LIKE 'board-missed-%' AND id NOT IN "
                   "(SELECT MIN(id) FROM monitor_events WHERE kind='MISSED' AND dedupe LIKE 'board-missed-%' GROUP BY day, title)")
        db.commit()


def _conn(db_path):
    db = sqlite3.connect(db_path, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def add_event(db_path, ev, lock=None):
    """Insert one finding. The same (day, dedupe) is stored once, so a problem that persists is not repeated. Returns the new id or None."""
    row = {c: ev.get(c) for c, _ in EVENT_COLS}
    row["created_at"] = row["created_at"] or datetime.now(IST).isoformat(timespec="seconds")
    if isinstance(row["detail"], (dict, list)):
        row["detail"] = json.dumps(row["detail"], default=str)

    def run():
        with _conn(db_path) as db:
            cur = db.execute(f"INSERT OR IGNORE INTO monitor_events ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
            db.commit()
            return cur.lastrowid if cur.rowcount else None
    if lock:
        with lock:
            return run()
    return run()


def set_note(db_path, event_id, note, model, lock=None):
    def run():
        with _conn(db_path) as db:
            db.execute("UPDATE monitor_events SET claude_note=?, note_model=? WHERE id=?", (note, model, event_id))
            db.commit()
    if lock:
        with lock:
            return run()
    return run()


def add_review(db_path, rv, lock=None):
    row = {c: rv.get(c) for c, _ in REVIEW_COLS}
    row["created_at"] = row["created_at"] or datetime.now(IST).isoformat(timespec="seconds")
    for k in ("suggestions", "context"):
        if isinstance(row[k], (dict, list)):
            row[k] = json.dumps(row[k], default=str)

    def run():
        with _conn(db_path) as db:
            cur = db.execute(f"INSERT INTO monitor_reviews ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
            db.commit()
            return cur.lastrowid
    if lock:
        with lock:
            return run()
    return run()


def _loads(v):
    try:
        return json.loads(v) if v else None
    except ValueError:
        return v


def events(db_path, start="", end="", kind="", limit=500, lock=None):
    sql, args = "SELECT * FROM monitor_events WHERE 1=1", []
    if start:
        sql += " AND day >= ?"; args.append(start)
    if end:
        sql += " AND day <= ?"; args.append(end)
    if kind:
        sql += " AND kind = ?"; args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))

    def run():
        with _conn(db_path) as db:
            out = [dict(r) for r in db.execute(sql, args).fetchall()]
        for r in out:
            r["detail"] = _loads(r["detail"])
        return out
    if lock:
        with lock:
            return run()
    return run()


def reviews(db_path, start="", end="", limit=50, lock=None):
    sql, args = "SELECT * FROM monitor_reviews WHERE 1=1", []
    if start:
        sql += " AND day >= ?"; args.append(start)
    if end:
        sql += " AND day <= ?"; args.append(end)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))

    def run():
        with _conn(db_path) as db:
            out = [dict(r) for r in db.execute(sql, args).fetchall()]
        for r in out:
            r["suggestions"] = _loads(r["suggestions"])
            r.pop("context", None)
        return out
    if lock:
        with lock:
            return run()
    return run()


def calls_today(db_path, day, lock=None):
    def run():
        with _conn(db_path) as db:
            a = db.execute("SELECT COUNT(*) FROM monitor_events WHERE day=? AND note_model IS NOT NULL AND note_model != ''", (day,)).fetchone()[0]
            b = db.execute("SELECT COUNT(*) FROM monitor_reviews WHERE day=? AND model IS NOT NULL AND model != ''", (day,)).fetchone()[0]
            return a + b
    if lock:
        with lock:
            return run()
    return run()


# ---------------------------------------------------------------- 1. live feed checks (every few seconds while the market is open)
def check_feed(state):
    """state: {symbol, index_tick_age, analysis_age, bars_behind, stream_running, token_ok, signal_active}. Returns findings.
    Thresholds: a tick older than 5 s, an analysis older than 10 s or candles 2+ bars behind make the live signal untrustworthy."""
    sym, out = state.get("symbol", "NIFTY"), []
    act = " while a signal is active" if state.get("signal_active") else ""
    sev = "high" if state.get("signal_active") else "medium"
    if state.get("token_ok") is False:
        out.append({"kind": "FEED", "severity": "high", "title": f"{sym}: Upstox token not valid - no live data",
                    "detail": {"check": "token"}, "dedupe": "feed-token"})
        return out
    if not state.get("stream_running"):
        out.append({"kind": "FEED", "severity": sev, "title": f"{sym}: live price stream not running{act}",
                    "detail": {"check": "stream"}, "dedupe": "feed-stream"})
    age = state.get("index_tick_age")
    if age is not None and age > 5:
        out.append({"kind": "FEED", "severity": sev, "title": f"{sym}: index ticks {age:.0f} s old{act}",
                    "detail": {"check": "tick_age", "seconds": round(age, 1)}, "dedupe": f"feed-tick-{int(age // 60)}"})
    an = state.get("analysis_age")
    if an is not None and an > 10:
        out.append({"kind": "FEED", "severity": sev, "title": f"{sym}: analysis {an:.0f} s old - score / decision are lagging",
                    "detail": {"check": "analysis_age", "seconds": round(an, 1)}, "dedupe": f"feed-analysis-{int(an // 60)}"})
    bb = state.get("bars_behind")
    if bb is not None and bb >= 2:
        out.append({"kind": "FEED", "severity": sev, "title": f"{sym}: candle feed {bb} bars behind the live price",
                    "detail": {"check": "bars_behind", "bars": bb}, "dedupe": f"feed-bars-{bb}"})
    return out


# ---------------------------------------------------------------- 2. recorded trades against the rules
def _f(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def check_trades(rows, min_sl_atr=MIN_SL_ATR, squareoff="15:20"):
    """Each recorded BUY against its own plan: stop >= 1.5 ATR, exit not after the square-off, fill not far beyond the stop,
    option premium behaving like its delta said. Returns findings (dedupe per trade and check)."""
    out = []
    for r in rows:
        rid, name = r.get("id"), r.get("option_name") or f"{r.get('symbol')} {r.get('side')}"
        t = str(r.get("generated_at") or "")[11:16]
        atr, slm = _f(r.get("atr_at_entry")), _f(r.get("sl_atr_mult"))
        entry, sl, ex = _f(r.get("entry")), _f(r.get("stop_loss")), _f(r.get("exit_price"))
        sign = 1 if r.get("side") == "CE" else -1
        if slm is not None and slm < min_sl_atr - 0.01:
            out.append({"kind": "RULE", "severity": "medium", "title": f"{name} {t}: stop only {slm:.2f} ATR (rule: ≥ {min_sl_atr} ATR)",
                        "detail": {"trade_id": rid, "sl_atr_mult": slm}, "dedupe": f"rule-sl-{rid}"})
        et = str(r.get("exit_time") or "")[11:16]
        if et and et > squareoff and r.get("trade_status") not in ("OPEN", None):
            out.append({"kind": "RULE", "severity": "medium", "title": f"{name} {t}: closed at {et}, after the {squareoff} square-off",
                        "detail": {"trade_id": rid, "exit_time": r.get("exit_time")}, "dedupe": f"rule-late-{rid}"})
        if r.get("trade_status") == "STOP" and None not in (entry, sl, ex) and atr:
            slip = (sl - ex) * sign
            if slip > 0.3 * atr:
                out.append({"kind": "RULE", "severity": "medium",
                            "title": f"{name} {t}: stop filled {slip:.1f} pts beyond the stop ({slip / atr:.2f} ATR) - gap or late detection",
                            "detail": {"trade_id": rid, "slippage_pts": round(slip, 1)}, "dedupe": f"rule-slip-{rid}"})
        oe, ox, dl = _f(r.get("option_entry")), _f(r.get("option_exit")), _f(r.get("option_delta"))
        if None not in (oe, ox, dl, entry, ex) and r.get("trade_status") not in ("OPEN", None):
            idx_move = (ex - entry) * sign
            expected = abs(dl) * idx_move
            actual = ox - oe
            if abs(idx_move) >= 5 and abs(actual - expected) > max(5.0, 0.5 * abs(expected)):
                out.append({"kind": "OPTION", "severity": "info",
                            "title": f"{name} {t}: premium moved {actual:+.1f} vs {expected:+.1f} expected from delta {abs(dl):.2f}",
                            "detail": {"trade_id": rid, "index_move": round(idx_move, 1), "premium_move": round(actual, 1), "expected": round(expected, 1)},
                            "dedupe": f"opt-delta-{rid}"})
    sym = {r.get("id"): r.get("symbol") for r in rows}
    for f in out:
        f["symbol"] = sym.get(f["detail"].get("trade_id"))
    return out


# ---------------------------------------------------------------- 3. why a trade lost
def loss_causes(r):
    """Plain-code reasons for one closed losing trade, from what the price did after entry (peak / worst excursion vs ATR),
    the time of entry and the option fill. Returns a list of {cause, why} (most important first)."""
    atr = _f(r.get("atr_at_entry")) or 0
    peak, worst = _f(r.get("peak_points")), _f(r.get("worst_points"))
    causes = []
    if atr and peak is not None:
        if peak >= 1.0 * atr:
            causes.append({"cause": "Gave back a winner", "why": f"price went {peak:.0f} pts ({peak / atr:.1f} ATR) your way first, then reversed to the stop - "
                                                                 "a trailing stop / break-even after +1 ATR would have kept part of it"})
        elif peak < 0.3 * atr:
            causes.append({"cause": "Wrong from the first bar", "why": f"price never went more than {max(peak, 0):.0f} pts your way - entry direction or timing was wrong "
                                                                      "(often a late entry after the move or a fake breakout)"})
        else:
            causes.append({"cause": "No follow-through", "why": f"best move only {peak:.0f} pts ({peak / atr:.1f} ATR) - momentum faded right after entry"})
    t = str(r.get("generated_at") or "")[11:16]
    if t and ("12:00" <= t < "14:30"):
        causes.append({"cause": "Midday entry", "why": f"entered at {t}; 12:00-14:30 was the losing window in every diagnostics file"})
    if r.get("trade_status") == "SQUAREOFF":
        causes.append({"cause": "Ran out of time", "why": "closed at the 15:20 square-off before reaching the target"})
    oe, ox, ex, entry, dl = _f(r.get("option_entry")), _f(r.get("option_exit")), _f(r.get("exit_price")), _f(r.get("entry")), _f(r.get("option_delta"))
    if None not in (oe, ox, ex, entry, dl):
        sign = 1 if r.get("side") == "CE" else -1
        exp = abs(dl) * (ex - entry) * sign
        if (ox - oe) < exp - max(5.0, 0.5 * abs(exp)):
            causes.append({"cause": "Option lost more than the index move explains", "why": f"premium {ox - oe:+.1f} vs {exp:+.1f} expected - spread, IV drop or theta"})
    if worst is not None and atr and r.get("trade_status") == "STOP" and abs(worst) > 1.6 * atr:
        causes.append({"cause": "Volatile entry bar", "why": f"worst excursion {abs(worst):.0f} pts ({abs(worst) / atr:.1f} ATR) - the stop was hit in a spike"})
    return causes


def losses(rows):
    out = []
    for r in rows:
        net = _f(r.get("net_pnl_points"))
        if r.get("trade_status") in ("STOP", "TRAIL", "SQUAREOFF", "TIME", "EXPIRED") and net is not None and net < 0:
            c = loss_causes(r)
            name = r.get("option_name") or f"{r.get('symbol')} {r.get('side')}"
            t = str(r.get("generated_at") or "")[11:16]
            out.append({"kind": "LOSS", "severity": "medium", "symbol": r.get("symbol"), "title": f"Loss {net:+.1f} pts: {name} {t} - " + (c[0]["cause"] if c else "see detail"),
                        "detail": {"trade_id": r.get("id"), "net_points": net, "net_rs": _f(r.get("net_pnl_rs")), "causes": c,
                                   "peak": _f(r.get("peak_points")), "worst": _f(r.get("worst_points")), "atr": _f(r.get("atr_at_entry")),
                                   "score": r.get("score"), "regime": r.get("regime"), "pattern": r.get("pattern")},
                        "dedupe": f"loss-{r.get('id')}"})
    return out


# ---------------------------------------------------------------- 4. missed moves / missed signals
def missed_from_replay(rep, day):
    """Today's big moves the engine did not confirm, from a diagnostics replay over recent days, with the gates that blocked them."""
    out = []
    for ex in (rep.get("missed_moves") or {}).get("examples", []):
        if ex.get("day") != day:
            continue
        gates = ex.get("blocked_by") or []
        out.append({"kind": "MISSED", "severity": "medium",
                    "title": f"Missed {ex['side']} move at {ex['time']}: +{ex['points']:.0f} pts ({ex['atr_move']} ATR) - blocked by {gates[0] if gates else 'score'}"
                             + (f" (+{len(gates) - 1} more)" if len(gates) > 1 else ""),
                    "detail": ex, "dedupe": f"missed-{ex['time']}-{ex['side']}"})
    return out


def _bought_soon(r, board_rows, minutes=15):
    """v1.20.91: True when the app BOUGHT the same index and side within `minutes` after this setup (or while it was open -
    'Same-side re-entry'). Such a setup is the same move as the trade, not a missed one (17 Nov: the 09:50 BANKNIFTY and 10:50 NIFTY
    SETUPs were bought at 09:51 / 10:57 but were still listed as missed, and 'Reconfirmed' looked like it blocked winners)."""
    def mins(x):
        s_ = str(x or "")
        try:
            return int(s_[11:13]) * 60 + int(s_[14:16]), s_[:10]
        except ValueError:
            return None, None
    m0, d0 = mins(r.get("created_at"))
    if m0 is None:
        return False
    if "Same-side re-entry" in str(r.get("blocked_by") or ""):
        return True
    for b in board_rows:
        if b.get("stage") != "BUY" or b.get("symbol") != r.get("symbol") or b.get("side") != r.get("side"):
            continue
        m1, d1 = mins(b.get("created_at"))
        if m1 is not None and d1 == d0 and 0 <= m1 - m0 <= minutes:
            return True
    return False


def missed_from_board(board_rows):
    """Signal-board setups that were NOT bought but whose option then paid (target reached in the 60-min test, or +30 % premium)."""
    out, seen = [], set()
    for r in board_rows:
        if r.get("stage") not in ("WATCH", "SETUP", "NO BUY", "FAILED"):
            continue
        if _bought_soon(r, board_rows):
            continue
        best = _f(r.get("best_pct_60"))
        # v1.20.2: a +4 % premium is not a missed trade - need the test target AND a real premium gain, or +30 %
        if best is not None and ((r.get("test_result") == "TARGET" and best >= 15) or best >= 30):
            t = str(r.get("created_at") or "")[11:16]
            k = (r.get("symbol"), r.get("option_name"), str(r.get("created_at") or "")[:16], r.get("stage"))
            if k in seen:
                continue
            seen.add(k)
            gates = [g for g in str(r.get("blocked_by") or "").split(" · ") if g]
            out.append({"kind": "MISSED", "severity": "info", "symbol": r.get("symbol"),
                        "title": f"{r.get('stage')} {r.get('option_name') or r.get('side')} {t} not bought - option then went +{best or 0:.0f}% in 60 min",
                        "detail": {"board_id": r.get("id"), "stage": r.get("stage"), "reason": r.get("reason"), "score": r.get("score"),
                                   "best_pct_60": best, "test_result": r.get("test_result"), "blocked_by": gates},
                        # stable across restarts and across modes / timeframes recording the same setup
                        "dedupe": f"board-missed-{r.get('symbol')}-{r.get('option_name')}-{str(r.get('created_at') or '')[:16]}-{r.get('stage')}"})
    return out


def board_gate_stats(board_rows):
    """For every rule that blocked today's not-bought setups: how many of them the 1.5 ATR test trade would have won / lost.
    One row per setup (duplicates across modes / timeframes merged). This is the evidence for relaxing or keeping a rule."""
    seen, per = set(), {}
    for r in board_rows:
        if r.get("stage") not in ("WATCH", "SETUP", "NO BUY", "FAILED") or not r.get("test_result"):
            continue
        if _bought_soon(r, board_rows):          # v1.20.91: part of a move the app did buy - not evidence against a rule
            continue
        k = (r.get("symbol"), r.get("option_name"), str(r.get("created_at") or "")[:16])
        if k in seen:
            continue
        seen.add(k)
        for g in [g for g in str(r.get("blocked_by") or "").split(" · ") if g] or ["(not recorded - before v1.20.6)"]:
            d = per.setdefault(g, {"gate": g, "setups": 0, "target": 0, "stop": 0, "other": 0, "points": 0.0})
            d["setups"] += 1
            res = r.get("test_result")
            d["target" if res == "TARGET" else "stop" if res == "STOP" else "other"] += 1
            d["points"] += _f(r.get("test_points")) or 0.0
    out = sorted(per.values(), key=lambda x: -x["setups"])
    for d in out:
        closed = d["target"] + d["stop"]
        d["win_rate"] = round(100.0 * d["target"] / closed, 1) if closed else None
        d["avg_points"] = round(d["points"] / d["setups"], 1) if d["setups"] else None
        d.pop("points")
    return out


# ---------------------------------------------------------------- 5. drift and suggestions from the recent replay
def drift(today_stats, rep):
    """Today's live result versus the recent replay. Only flagged with 3+ closed trades today."""
    le = rep.get("live_trail") or rep.get("live_exit") or {}
    n, win = today_stats.get("closed", 0), today_stats.get("win_rate")
    if n < 3 or win is None or not le.get("n"):
        return []
    if win < le.get("win_rate", 0) - 25:
        return [{"kind": "DRIFT", "severity": "medium",
                 "title": f"Today {win:.0f}% winners on {n} trades vs {le['win_rate']}% in the last {len((rep.get('params') or {}).get('days') or [])} days",
                 "detail": {"today": today_stats, "replay": le}, "dedupe": f"drift-{n}"}]
    return []


def suggestions(rep, min_n=8):
    """Concrete, data-backed improvement ideas from a diagnostics replay. Each has the evidence and a caution, so it can be judged."""
    out = []
    base = (rep.get("confirmed") or {}).get("stats") or {}
    base_avg = base.get("avg")
    # a) gates that, alone, blocked setups which then made money -> candidates to relax
    for g in rep.get("ablation") or []:
        if g.get("n", 0) >= min_n and g.get("avg", 0) > 3 and g.get("win_rate", 0) >= 60:
            out.append({"area": "entry", "title": f"Consider relaxing “{g['gate']}”",
                        "evidence": f"{g['n']} setups blocked ONLY by this gate: {g['win_rate']}% winners, {g['avg']:+.1f} pts/trade"
                                    + (f" (taken signals: {base_avg:+.1f})" if base_avg is not None else ""),
                        "caution": "test it on the next diagnostics run before changing; small samples flip easily"})
        elif g.get("n", 0) >= min_n and g.get("avg", 0) < 0:
            out.append({"area": "entry", "title": f"Keep “{g['gate']}” - it is protecting you",
                        "evidence": f"{g['n']} setups it alone blocked would have lost ({g['win_rate']}% winners, {g['avg']:+.1f} pts/trade)",
                        "caution": ""})
    # b) losing buckets among taken signals -> candidates to filter out
    for name, rows in (rep.get("groups") or {}).items():
        if name.startswith("Cutoff what-if") or name == "Day":
            continue
        for b in rows:
            if b.get("n", 0) >= 5 and b.get("avg", 0) < -2:
                out.append({"area": "filter", "title": f"Losing group: {name} = {b['label']}",
                            "evidence": f"{b['n']} signals, {b['win_rate']}% winners, {b['avg']:+.1f} pts/trade",
                            "caution": "only act if it also loses on the next run"})
    # c) exit management
    le = rep.get("live_exit") or {}
    atr_st = [x for x in rep.get("strategies") or [] if x.get("name", "").startswith("ATR ") and x.get("n", 0) >= min_n]
    if le.get("n") and atr_st:
        top = max(atr_st, key=lambda x: x["avg"])
        if top["avg"] > le.get("avg", 0) + 1:
            out.append({"area": "exit", "title": f"Exit: “{top['name']}” beat the fixed 1.5 ATR target",
                        "evidence": f"{top['avg']:+.1f} vs {le['avg']:+.1f} pts/trade over {top['n']} trades",
                        "caution": "same 1.5 ATR stop; switch only if it holds on two runs"})
    # d) missed big moves: the gate behind most of them
    mm = rep.get("missed_moves") or {}
    if mm.get("missed", 0) >= 5 and mm.get("blockers"):
        b = mm["blockers"][0]
        abl = next((g for g in rep.get("ablation") or [] if g.get("gate") == b["gate"]), None)
        verdict = (f"setups it alone blocked: {abl['n']} trades, {abl['avg']:+.1f} pts/trade" if abl else "no setups were blocked by it alone")
        out.append({"area": "missed", "title": f"Most missed moves were blocked by “{b['gate']}” ({b['moves']} of {mm['missed']})",
                    "evidence": verdict, "caution": "a big move after the fact is not a tradable signal; relax only if the near-miss trades pay"})
    return out[:12]


# ---------------------------------------------------------------- 6. Claude
# v1.20.27: rules every AI reply must follow (6 Nov: the free model misread a 20-bar move as the day's close, quoted the
# signal board as "near-miss data", invented a cause for past losses and used a 'blocked winners' number as a reason to keep a rule)
STRICT_RULES = ("\nSTRICT RULES: (a) Quote only numbers that appear in the data, with their label; never estimate or make up a cause, a figure "
                "or a past result. If the data does not say, write 'not in the data'. (b) Read labels exactly: 'N ATR move up in 20 bars' is the "
                "last 20 candles, not the day; 'today_blocking_rules' and the signal board are TODAY only; 'near_miss_by_gate' is the multi-day "
                "replay. (c) Setups from the same day and the same move are ONE piece of evidence, not many: say 'one day / one rally' and never "
                "relax a rule on one day's evidence when the multi-day replay disagrees. (d) A rule is protecting you when the setups it alone "
                "blocked LOST in the replay; blocking big moves is not, by itself, evidence for or against it. (e) Under 30 trades is not proven. "
                "(f) Read 'checks' first: they are computed by code and are facts - mention every one that is a problem.")
SYSTEM_NOTE = ("You monitor an intraday NIFTY / BANKNIFTY options signal app for its owner. You get one finding produced by code "
               "(with numbers). In 2-4 short sentences say what most likely caused it and what to check or change. Be concrete and use the "
               "numbers given. Do not invent data. Never tell the user to place or exit a trade; this is analysis only." + STRICT_RULES)
SYSTEM_REVIEW = ("You are reviewing an intraday options signal strategy (NIFTY/BANKNIFTY, ITM options 2 strikes in the money, stop at least "
                 "1.5 ATR, entry windows 09:45-12:00 and 14:30-15:00, square-off 15:20). You get JSON with today's live trades, losses with "
                 "code-detected causes, missed moves with the gates that blocked them, signal-board setups that were not bought, "
                 "today_blocking_rules (for each rule: setups it blocked and how many of those the 1.5 ATR test trade won / lost), "
                 "'replay_vs_live' (each signal the replay confirmed today next to what the live app did then: stage reached and the rules "
                 "that blocked it - when the replay bought and live did not, name that rule first in 'Why moves were missed'), "
                 "'terminal' (app settings, the live analysis of each symbol / timeframe in use: score, regime, failed gates, buy plan, S/R, option, "
                 "feed state, today's log and board summary) and, when sent from the terminal, 'page' (chart trendlines, S/R map, selected option). "
                 "'paper_tests': rules that are only tested on paper (logged and scored, never bought, never an alert) - for each one whether it is switched on, its closed trades, win rate, points and its own verdict. "
                 "Check that the settings are consistent with the evidence and point out anything in the live state that looks wrong. Feed/rule "
                 "discrepancies, and a replay of the last days with code-generated suggestions. Write for the owner, in plain English:\n"
                 "0. Problems the app found ('checks'): one short line each with what to do; skip if empty.\n"
                 "1. What happened today (2-3 sentences, numbers).\n2. Why moves were missed (which gates, and whether relaxing them would "
                 "have paid according to the near-miss data).\n3. What caused the losses (patterns, not single trades).\n4. Suggested changes, "
                 "ranked, max 5. For each: the exact setting or rule, the evidence, and the risk (sample size, overfitting). Prefer 'keep' when "
                 "the evidence is weak.\n5. Data / feed problems, if any.\n6. Paper tests: one line each (trades, win rate, points, verdict; "
                 "say 'switched off' or 'no closed trades yet' when that is the case). Never suggest making a paper test live under 15 closed "
                 "trades or when it is not positive in both halves.\nIn 'replay.verdict' the lines starting 'Indicator test' say what each scoring "
                 "indicator contributes; they come from a few days only, so report them but do not suggest changing a score weight from them.\n"
                 "Keep the stop at least 1.5 ATR (owner's rule). Do not promise "
                 "profits. Use only the data given. Under 450 words." + STRICT_RULES)


def consistency_checks(ctx):
    """v1.20.27: facts computed by code for the reviewer (AI or human), so they do not depend on the model reading the JSON right."""
    out = []
    t = (ctx or {}).get("terminal") or {}
    st = t.get("settings") or {}
    ba, sc = st.get("background_alerts") or {}, st.get("signals_config") or {}
    combos = [c for c in (t.get("combos_in_use") or []) if isinstance(c, (list, tuple)) and len(c) >= 4]
    if ba and combos:
        other = sorted({(c[2], c[3]) for c in combos} - {(ba.get("mode"), ba.get("score_min"))})
        if other:
            out.append(f"SETTINGS MISMATCH: phone alerts use mode {ba.get('mode')} / score {ba.get('score_min')} but a page is using "
                       + ", ".join(f"{m} / {s_}" for m, s_ in other) + " - alerts and the screen can disagree. Use the same mode and score (Render ALERT_MODE / ALERT_SCORE_MIN).")
    rep = (ctx or {}).get("replay") or {}
    le, verdicts = rep.get("live_exit") or {}, " ".join(rep.get("verdict") or [])
    import re as _re
    m = _re.search(r"Live exit plan \(stop 1\.5 ATR, trail.*?\): (\d+) trades, [\d.]+% winners, ([+-][\d.]+) pts", verdicts)
    if m and le.get("avg") is not None and sc.get("exit_plan") == "trail":
        tr = float(m.group(2))
        if le["avg"] > tr + 3:
            out.append(f"EXIT PLAN: in this replay the fixed 1.5/1.5 ATR exit made {le['avg']:+.1f} pts per trade vs {tr:+.1f} for your live trailing plan "
                       f"(n={m.group(1)}). Small sample - compare with the 30-day Diagnostics before switching.")
    uu = t.get("upstox_usage") or {}
    for fam, u in sorted((uu.get("by_api") or {}).items()):
        if (u.get("rate_limited_today") or 0) > 0:
            out.append(f"UPSTOX RATE LIMIT: {fam} answered 'too many requests' {u['rate_limited_today']} time(s) today - the app paused it each time. "
                       "Close extra terminal tabs / devices; repeated limits can get API access suspended for a while.")
        elif (u.get("peak_share_pct") or 0) >= 80:
            out.append(f"UPSTOX USAGE HIGH: {fam} is at {u['peak_share_pct']:g}% of a published limit ({u.get('last_min')} calls in the last minute, "
                       f"{u.get('last_30min')} in 30 min; limits {(uu.get('limits') or {}).get('per_min')}/min, {(uu.get('limits') or {}).get('per_30min')}/30 min).")
            if fam.endswith("/intraday") and uu.get("intraday_by_kind"):
                out[-1] += " Intraday calls in 30 min by kind: " + ", ".join(f"{k} {v.get('last_30min')}" for k, v in sorted(uu["intraday_by_kind"].items())) + "."
    if sc.get("indicator_changes"):
        out.append("INDICATOR VALUES changed from the tested defaults for live signals: "
                   + ", ".join(f"{k} {v}" for k, v in sc["indicator_changes"].items()) + " - check they were tested in Diagnostics first.")
    if sc.get("one_per_side") == "off":
        out.append("ONE TRADE PER SIDE is off (Signals page). Earlier 30-day replays: 1st same-side signal of the day won, later ones lost - small sample.")
    pg = (ctx or {}).get("page") or {}
    so = pg.get("selected_option") or {}
    if so and pg.get("chart_view") == "option":
        lean = next((x.get("analysis", {}).get("side") for x in (t.get("live_analyses") or [])
                     if x.get("symbol") == pg.get("symbol") and x.get("mode") == pg.get("mode")), None)
        if lean and so.get("type") and lean != so.get("type"):
            out.append(f"CHART ON THE WRONG SIDE: the terminal shows {so.get('name')} ({so.get('type')}) while the engine leans {lean}.")
    # v1.20.31: live-state problems (feeds, token, stale analyses, AI, version, strong trend with the trend setting off)
    mk = t.get("market") or {}
    if t.get("token_state") and t.get("token_state") != "VALID":
        out.append(f"UPSTOX TOKEN: state {t.get('token_state')} - live data and signals stop until it is renewed.")
    for sym_, f in (t.get("feeds") or {}).items():
        if mk.get("open") and not f.get("stream_running"):
            out.append(f"FEED {sym_}: the live stream is not running during market hours.")
        elif mk.get("open") and (f.get("tick_age_s") or 0) > 60:
            out.append(f"FEED {sym_}: last tick {f.get('tick_age_s'):.0f} s old during market hours - prices on screen are stale.")
        rs_ = f.get("restarts_today") or []
        if rs_:                      # v1.20.63: the stream watchdog had to restart this feed
            out.append(f"FEED {sym_}: live stream restarted {len(rs_)} time(s) today by the watchdog - last at {rs_[-1].get('at', '')[11:19]} "
                       f"({rs_[-1].get('reason', '')}). Repeated restarts mean the Upstox connection keeps dropping.")
    for x in t.get("live_analyses") or []:
        an = x.get("analysis") or {}
        name = f"{x.get('symbol')} {x.get('interval')}m {x.get('mode')}"
        if mk.get("open") and (x.get("age_s") or 0) > 300:
            out.append(f"STALE ANALYSIS {name}: last computed {x['age_s'] / 60:.0f} min ago during market hours.")
        if an.get("status") and an.get("status") != "ok":
            out.append(f"ANALYSIS {name}: status {an.get('status')}.")
        note = str(an.get("regime_note") or "")
        import re as _re2
        mm_ = _re2.search(r"([\d.]+) ATR move (up|down) in 20 bars \(efficiency ([\d.]+)\)", note)
        if mm_ and float(mm_.group(1)) >= 4 and float(mm_.group(3)) >= 0.55 and sc.get("trend_entry", "off") == "off":
            out.append(f"STRONG TREND {name}: {mm_.group(1)} ATR {mm_.group(2)} in 20 bars (efficiency {mm_.group(3)}) but 'Strong-trend entries' is Off - "
                       "the pullback rules cannot buy this kind of move (check the Diagnostics strong-trend table before switching it on).")
        fin = an.get("final") or {}
        if fin.get("level") in ("WATCH", "PENDING", "CONFIRMED") and not an.get("option_pick"):
            out.append(f"NO OPTION PICKED {name}: signal is {fin.get('level')} but no option was chosen (option chain unavailable?).")
    ms = t.get("monitor_status") or {}
    if ms.get("error"):
        out.append(f"AI MONITOR ERROR: {str(ms.get('error'))[:160]}")
    pg_ = (ctx or {}).get("page") or {}
    if pg_.get("page_version") and t.get("app_version") and pg_["page_version"] != t["app_version"]:
        out.append(f"OLD PAGE: the terminal runs page version {pg_['page_version']} but the server is {t['app_version']} - close and reopen the app.")
    for it in pg_.get("issues") or []:
        if it.get("level") in ("high", "medium"):
            out.append(f"PAGE ({it['level']}): {it.get('text')}")
    if pg_.get("js_errors"):
        out.append(f"PAGE ERRORS: {len(pg_['js_errors'])} - last: {str(pg_['js_errors'][-1].get('msg'))[:120]}")
    if pg_.get("failed_requests"):
        fr = pg_["failed_requests"][-1]
        out.append(f"FAILED REQUESTS: {len(pg_['failed_requests'])} - last: {fr.get('url')} → {str(fr.get('error'))[:100]}")
    blk = {b.get("gate"): b for b in (ctx or {}).get("today_blocking_rules") or []}
    if blk:
        out.append("ONE DAY: today_blocking_rules and the signal board come from today only - setups in the same move are one piece of evidence.")
    nm = {g.get("gate"): g for g in rep.get("near_miss_by_gate") or []}
    for gname, b in blk.items():
        w3 = lambda x: " ".join(str(x or "").lower().split()[:3])
        n_ = next((v for k, v in nm.items() if k and gname and w3(k) == w3(gname)), None)
        if b.get("setups", 0) >= 5 and (b.get("avg_points") or 0) > 0 and n_ and n_.get("n") and (n_.get("avg") or 0) <= 0:
            out.append(f"KEEP '{gname}': today it blocked {b['setups']} setups that would have paid, but in the multi-day replay the setups it alone "
                       f"blocked made {n_['avg']:+.1f} pts (n={n_['n']}).")
        if b.get("setups", 0) >= 3 and (b.get("avg_points") or 0) < 0:
            out.append(f"PROTECTING: '{gname}' blocked {b['setups']} setups today that would have lost ({b['avg_points']:+.1f} pts each).")
    return out


async def claude_call(system, user_text, model, max_tokens=700, timeout=40.0):
    """One Messages API call. Returns (text, error)."""
    key = api_key()
    if not key:
        return None, "ANTHROPIC_API_KEY not set"
    try:
        import httpx
    except ImportError:
        return None, "httpx not installed"
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user_text}]}
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(API_URL, json=body, headers=headers)
        if r.status_code != 200:
            try:
                msg = (r.json().get("error") or {}).get("message")
            except Exception:
                msg = None
            return None, f"Claude API {r.status_code}: {(msg or r.text)[:200]}"
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        return text or None, None if text else "empty reply"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"


GEMINI_THINK_ROOM = int(os.getenv("GEMINI_THINK_ROOM", "3000"))     # output tokens left for the model's own thinking


def gemini_output_limit(max_tokens):
    return int(max_tokens) + max(0, GEMINI_THINK_ROOM)


def gemini_reply(data):
    """(text, finishReason) of a generateContent response; thinking parts are never part of the text."""
    cand = (data.get("candidates") or [{}])[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
    return text, cand.get("finishReason")


async def gemini_call(system, user_text, model, max_tokens=700, timeout=40.0):
    """One Gemini generateContent call (Google AI Studio key; the free tier is rate-limited and Google may use the content
    to improve its products). Returns (text, error)."""
    key = gemini_key()
    if not key:
        return None, "GEMINI_API_KEY not set"
    try:
        import httpx
    except ImportError:
        return None, "httpx not installed"
    # v1.20.72: the newer Flash models "think" first and the thinking is counted inside maxOutputTokens, so a limit sized for the
    # answer alone cut every reply mid-sentence (12 Nov report). The limit now leaves room for the thinking (the prompt itself keeps
    # the answer short), and a reply the model still cut off (finishReason MAX_TOKENS) is asked for once more with double the room.
    limit = gemini_output_limit(max_tokens)
    try:
        text, reason = None, None
        for attempt in (0, 1):
            body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user_text}]}],
                    "generationConfig": {"maxOutputTokens": limit, "temperature": 0.3}}
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": key, "content-type": "application/json"})
            if r.status_code != 200:
                if text:
                    break                                   # keep the first (cut) reply rather than nothing
                try:
                    msg = (r.json().get("error") or {}).get("message")
                except Exception:
                    msg = None
                hint = " (free-tier limit reached - try later or lower the calls/day)" if r.status_code == 429 else ""
                return None, f"Gemini API {r.status_code}: {(msg or r.text)[:200]}{hint}"
            text, reason = gemini_reply(r.json())
            if reason != "MAX_TOKENS" or attempt:
                break
            limit *= 2
        if text and reason == "MAX_TOKENS":
            text += " [reply cut off by the model's length limit]"
        return text or None, None if text else ("empty reply (the model used its whole output limit before answering)" if reason == "MAX_TOKENS" else "empty reply")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:160]}"


_GEMINI_GATE = {"lock": None, "last": 0.0, "cooldown_until": 0.0}
GEMINI_MIN_GAP = float(os.getenv("GEMINI_MIN_GAP_SEC", "6"))      # free tier: ~10-15 requests/minute -> keep calls apart


def gemini_models(primary):
    """Models to try in order (v1.20.10): the configured one, then the other free Flash aliases. GEMINI_MODELS overrides."""
    env = [m.strip() for m in os.getenv("GEMINI_MODELS", "").split(",") if m.strip()]
    chain = env or [primary, GEMINI_FALLBACK, "gemini-flash-lite-latest", "gemini-2.5-flash-lite", "gemini-2.5-flash"]
    out = []
    for m in chain:
        if m and m not in out:
            out.append(m)
    return out


_GEMINI_FOUND = {"at": 0.0, "models": [], "good": None}


def _suggested_model(err):
    """Google's 404 says which model to use instead ("... use models/gemini-x-flash ..."): take that name."""
    import re as _re
    m = _re.search(r"models/([A-Za-z0-9._-]+)", (err or "").split("no longer available", 1)[-1])
    return m.group(1) if m else None


async def gemini_discover(force=False):
    """v1.20.25: ask Google which models this key can call (ListModels) and keep the Flash text models, newest first.
    Cached for 6 hours. Model names are retired often; this keeps the monitor working without a code change."""
    import time as _t
    if not force and _GEMINI_FOUND["models"] and _t.time() - _GEMINI_FOUND["at"] < 6 * 3600:
        return _GEMINI_FOUND["models"]
    key = gemini_key()
    if not key:
        return []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 200},
                                 headers={"x-goog-api-key": key})
        if r.status_code != 200:
            return _GEMINI_FOUND["models"]
        names = []
        for mdl in r.json().get("models") or []:
            n = str(mdl.get("name") or "").replace("models/", "")
            if "generateContent" not in (mdl.get("supportedGenerationMethods") or []) or "flash" not in n:
                continue
            if any(x in n for x in ("image", "tts", "audio", "live", "embedding", "native", "exp")):
                continue
            names.append(n)
        import re as _re

        def rank(n):
            v = _re.search(r"(\d+(?:\.\d+)?)", n)
            return (0 if "preview" in n else 1, 1 if n.endswith("-latest") else 0, float(v.group(1)) if v else 0.0, 1 if "lite" in n else 0)
        names.sort(key=rank, reverse=True)
        _GEMINI_FOUND.update(at=_t.time(), models=names)
        return names
    except Exception:
        return _GEMINI_FOUND["models"]


def _transient(e):
    e = (e or "").lower()
    return any(k in e for k in (" 503", " 429", " 500", "overloaded", "unavailable", "high demand", "timeout", "readtimeout", "connecterror"))


async def _gemini_robust(system, user_text, model, max_tokens, timeout):
    """v1.20.10: one call at a time, at least GEMINI_MIN_GAP seconds apart (free-tier rate limit), retry busy models with a back-off,
    then walk down the free model list. A 404 (model name retired) moves straight to the next model. After everything failed with
    'busy' errors, calls pause for 2 minutes instead of burning the daily allowance."""
    import asyncio
    import time as _t
    if _GEMINI_GATE["lock"] is None:
        _GEMINI_GATE["lock"] = asyncio.Lock()
    if _t.time() < _GEMINI_GATE["cooldown_until"]:
        return None, "Gemini busy - paused for a minute after repeated 'high demand' replies; will retry automatically", None
    async with _GEMINI_GATE["lock"]:
        last_err = None
        chain = gemini_models(model)
        if _GEMINI_FOUND["models"]:                 # models Google listed for this key earlier: right after the configured one
            chain = chain[:1] + [m for m in _GEMINI_FOUND["models"][:3] if m not in chain[:1]] + [m for m in chain[1:] if m not in _GEMINI_FOUND["models"][:3]]
        if _GEMINI_FOUND["good"]:
            chain = [_GEMINI_FOUND["good"]] + [m for m in chain if m != _GEMINI_FOUND["good"]]
        tried, k = set(), -1
        while True:
            pending = [m for m in chain if m not in tried]
            if not pending:
                # v1.20.25: every listed name failed as "not found" - add Google's suggested name and the models this key can use
                if last_err and (" 404" in last_err or " 400" in last_err) and not _GEMINI_FOUND.get("_looked"):
                    _GEMINI_FOUND["_looked"] = True
                    extra = ([_suggested_model(last_err)] if _suggested_model(last_err) else []) + (await gemini_discover(force=True))[:5]
                    chain += [m for m in extra if m and m not in chain]
                    pending = [m for m in chain if m not in tried]
                if not pending:
                    break
            m = pending[0]
            tried.add(m)
            k += 1
            for attempt in range(2 if k == 0 else 1):
                wait = GEMINI_MIN_GAP - (_t.time() - _GEMINI_GATE["last"])
                if wait > 0:
                    await asyncio.sleep(wait)
                _GEMINI_GATE["last"] = _t.time()
                t, e = await gemini_call(system, user_text, m, max_tokens, timeout)
                if t:
                    _GEMINI_GATE["cooldown_until"] = 0.0
                    _GEMINI_FOUND["good"] = m                  # remember the working model: tried first next time
                    _GEMINI_FOUND.pop("_looked", None)
                    return t, None, m
                last_err = e
                if "api key" in (e or "").lower() or "permission" in (e or "").lower():
                    return None, e, None                    # wrong / blocked key: every model would fail the same way
                if " 404" in (e or "") or " 400" in (e or ""):
                    if _GEMINI_FOUND["good"] == m:
                        _GEMINI_FOUND["good"] = None
                    break                                   # this model name is not available: next model
                if not _transient(e):
                    return None, e, None                    # e.g. bad key - retrying will not help
                if attempt == 0 and k == 0:
                    await asyncio.sleep(4)
        _GEMINI_FOUND.pop("_looked", None)
        if _transient(last_err):
            _GEMINI_GATE["cooldown_until"] = _t.time() + 120
        if last_err and " 404" in last_err:
            last_err = f"No Gemini model available to this key (tried {len(tried)}: {', '.join(list(tried)[:6])}). Last: {last_err[:160]}"
        return None, last_err, None


async def ai_call(system, user_text, kind="note", max_tokens=700, timeout=40.0):
    """Route to the configured provider. Returns (text, error, model) - model None when nothing was called."""
    prov, model = provider(), model_for(kind)
    if prov == "claude":
        t, e = await claude_call(system, user_text, model, max_tokens, timeout)
    elif prov == "gemini":
        t, e, used = await _gemini_robust(system, user_text, model, max_tokens, timeout)
        model = used or model
    else:
        return None, None, None
    return t, (None if t else e), (model if t else None)


def ai_label():
    prov = provider()
    return {"claude": "Claude", "gemini": "Gemini (free tier)"}.get(prov)


def stored_context(db_path, day, lock=None):
    """The review data saved with the latest review of `day` (for the download), or None."""
    def run():
        with _conn(db_path) as db:
            r = db.execute("SELECT context FROM monitor_reviews WHERE day=? AND context IS NOT NULL ORDER BY id DESC LIMIT 1", (day,)).fetchone()
            return _loads(r[0]) if r else None
    if lock:
        with lock:
            return run()
    return run()


def chat_bundle(ctx, period="day"):
    """File to upload into a Claude chat: the instructions plus the data, so the subscription does the review at no API cost."""
    if period in ("week", "month"):
        return {"how_to_use": f"Upload this file in a Claude chat and write: review this {period}. Claude follows 'instructions' using only 'data'.",
                "instructions": SYSTEM_PERIOD_REVIEW, "data": ctx}
    return {"how_to_use": "Upload this file in a Claude chat and write: review this day. Claude follows 'instructions' using only 'data'.",
            "instructions": SYSTEM_REVIEW, "data": ctx}


# v1.20.37: weekly / monthly review file
SYSTEM_PERIOD_REVIEW = ("You are reviewing a WEEK or MONTH of an intraday options signal strategy (NIFTY / BANKNIFTY, ITM options 2 strikes in "
                        "the money, stop at least 1.5 ATR, entry windows 09:45-12:00 and 14:30-15:00, square-off 15:20; each index has its own "
                        "strategy: Pullback, Opening Range Breakout 30 min or Off). The data holds: 'summary' (all closed trades: points, ₹, win "
                        "rate, drawdown), 'by_day', 'by_symbol', 'by_side', 'by_time', 'by_setup' (timeframe / mode), the trades, the monitor's "
                        "findings and AI reviews of each day, the rules that blocked setups on the signal board over the period, the latest "
                        "Diagnostics run if one was made, and the app's current settings. "
                        "'paper_tests': rules that are only tested on paper (logged and scored, never bought, never an alert) - for each one whether it is switched on, its closed trades, win rate, points and its own verdict. "
                        "'latest_diagnostics.indicator_test' (also the verdict lines starting 'Indicator test'): for each scoring indicator its "
                        "vote at the start of the big moves (with / against), what the score and the signals would be without it, indicators "
                        "that vote alike, and 'combined' - several removed together. "
                        "Write for the owner, in plain English, under 650 words:\n"
                        "0. Problems the app found ('checks'): one line each, skip if empty.\n"
                        "1. Results: trades, win rate, net points and ₹, best and worst day, biggest drawdown, how many days made the owner's "
                        "₹2,000 goal. Is the profit spread over many days or carried by one or two?\n"
                        "2. Where the losses came from (symbol, side, time of day, exit type, strategy) - patterns that repeat on several days.\n"
                        "3. Missed opportunities: which rules blocked setups that would have paid, and whether that held on several days.\n"
                        "4. Suggested changes, ranked, max 5: exact setting, evidence (with numbers from the data), risk. Prefer 'keep' when the "
                        "evidence is one or two days or under 30 trades.\n"
                        "5. What to watch next week / month.\n"
                        "6. Paper tests: one line each (trades, win rate, points, first half / second half, verdict). A paper test may be "
                        "suggested for live use only with 15+ closed trades AND positive in both halves; otherwise say 'keep collecting'.\n"
                        "7. Indicator test: name the indicators the run marks as late, as duplicates, or as safe to remove. Suggest changing a "
                        "score weight only when the data shows the same result in two different 30-day runs; with one run say 'repeat first'. "
                        "Skip 6 or 7 when the data has nothing for it.\n"
                        "Keep the stop at least 1.5 ATR. Do not promise profits." + STRICT_RULES)


def _slot(t):
    t = str(t or "")[11:16]
    return ("09:15-09:45" if t < "09:45" else "09:45-10:30" if t < "10:30" else "10:30-12:00" if t < "12:00"
            else "12:00-14:30" if t < "14:30" else "14:30-15:30") if t else "?"


def period_context(period, start, end, rows, summarize, events_, reviews_, gate_stats, board_missed, diag=None):
    """Compact JSON for a weekly / monthly review. `summarize` is signal_exits.summarize (results per day / group + total)."""
    def group_by(key):
        g = {}
        for r in rows:
            g.setdefault(key(r), []).append(r)
        return {k: summarize(v, "day")["total"] for k, v in sorted(g.items(), key=lambda x: str(x[0]))}
    per_day = summarize(rows, "day")["periods"] if rows else []
    day_rs = [p.get("net_rs") for p in per_day if p.get("net_rs") is not None]
    kinds = {}
    for e in events_:
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    missed = sorted([e for e in events_ if e.get("kind") == "MISSED"], key=lambda e: -float(((e.get("detail") or {}).get("points") or 0)))
    eod = {}
    for rv in sorted(reviews_, key=lambda r: str(r.get("created_at") or "")):
        if rv.get("text") and (rv.get("kind") == "eod" or rv.get("day") not in eod):
            eod[rv.get("day")] = {"kind": rv.get("kind"), "model": rv.get("model"), "text": str(rv.get("text"))[:1500]}
    tr = lambda r: {k: r.get(k) for k in ("session_date", "generated_at", "symbol", "interval", "mode", "side", "option_name", "score",
                                          "trade_status", "net_pnl_points", "net_pnl_rs", "peak_points", "worst_points", "exit_reason")}
    return {
        "period": period, "start": start, "end": end,
        "summary": summarize(rows, "day")["total"] if rows else {"signals": 0},
        "rupees": {"days_with_trades": len(day_rs), "total": round(sum(day_rs), 0) if day_rs else None,
                   "best_day": round(max(day_rs), 0) if day_rs else None, "worst_day": round(min(day_rs), 0) if day_rs else None,
                   "days_at_2000": sum(1 for x in day_rs if x >= 2000), "losing_days": sum(1 for x in day_rs if x < 0)},
        "by_day": per_day[:31],
        "by_symbol": group_by(lambda r: r.get("symbol")),
        "by_side": group_by(lambda r: r.get("side")),
        "by_time": group_by(lambda r: _slot(r.get("generated_at"))),
        "by_setup": group_by(lambda r: f"{r.get('symbol')} {r.get('interval')}m {r.get('mode')}"),
        "by_weekday": group_by(lambda r: __import__("datetime").date.fromisoformat(str(r.get("session_date"))).strftime("%a") if r.get("session_date") else "?"),
        "trades": [tr(r) for r in rows][:250],
        "findings_count": kinds,
        "biggest_missed_moves": [{"day": e.get("day"), "title": e.get("title"), "blocked_by": (e.get("detail") or {}).get("blocked_by")} for e in missed[:25]],
        "losses_and_causes": [{"day": e.get("day"), "title": e.get("title")} for e in events_ if e.get("kind") == "LOSS"][:40],
        "blocking_rules": (gate_stats or [])[:15],
        "not_bought_but_paid": [{"title": m["title"], "blocked_by": (m.get("detail") or {}).get("blocked_by")} for m in board_missed][:25],
        "daily_reviews": eod,
        "latest_diagnostics": diag,
    }


def note_prompt(ev):
    return json.dumps({"finding": ev.get("title"), "kind": ev.get("kind"), "severity": ev.get("severity"), "detail": ev.get("detail")},
                      default=str, ensure_ascii=False)


def review_context(day, symbol, trades, findings, board_missed, rep, sugg, gate_stats=None):
    """Compact JSON for the review (keeps the request small and cheap)."""
    def tr(r):
        return {k: r.get(k) for k in ("generated_at", "side", "option_name", "score", "regime", "pattern", "entry", "stop_loss", "target",
                                     "trade_status", "net_pnl_points", "net_pnl_rs", "peak_points", "worst_points", "atr_at_entry", "exit_reason")}
    rep = rep or {}
    return {
        "day": day, "symbol": symbol,
        "live_trades": [tr(r) for r in trades][:30],
        "findings": [{"kind": f.get("kind"), "title": f.get("title"), "detail": f.get("detail")} for f in findings
                     if f.get("kind") != "MISSED" or not str(f.get("dedupe") or "").startswith("board-missed")][:40],
        "not_bought_but_paid": [{"title": m["title"], "blocked_by": (m.get("detail") or {}).get("blocked_by")} for m in board_missed][:15],
        "today_blocking_rules": (gate_stats or [])[:12],
        "replay": {"params": {k: (rep.get("params") or {}).get(k) for k in ("mode", "interval", "days")},
                   "live_exit": rep.get("live_exit"), "confirmed": (rep.get("confirmed") or {}).get("stats"),
                   "missed_moves": {k: (rep.get("missed_moves") or {}).get(k) for k in ("moves", "caught", "missed", "blockers")},
                   "near_miss_by_gate": (rep.get("ablation") or [])[:10],
                   "score_model": rep.get("score_model"),     # v1.20.72: Classic vs Priority on the same days
                   "early_priority": {k: (rep.get("early_priority") or {}).get(k) for k in ("trail", "first", "second", "verdict")},
                   "verdict": [v.get("text") for v in (rep.get("verdict") or [])][:10],
                   "trend_day_whatif": [{k: v.get(k) for k in ("label", "extra", "first", "second", "combined", "verdict")}
                                        for v in ((rep.get("trend_day") or {}).get("variants") or [])]},
        "code_suggestions": sugg,
    }


def today_stats(rows):
    closed = [r for r in rows if r.get("trade_status") not in ("OPEN", None) and _f(r.get("net_pnl_points")) is not None]
    wins = [r for r in closed if _f(r.get("net_pnl_points")) > 0]
    return {"signals": len(rows), "closed": len(closed), "wins": len(wins),
            "win_rate": round(100.0 * len(wins) / len(closed), 1) if closed else None,
            "net_points": round(sum(_f(r.get("net_pnl_points")) for r in closed), 1) if closed else 0.0}
