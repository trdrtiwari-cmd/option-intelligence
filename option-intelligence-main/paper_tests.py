"""v1.20.41: paper-only strategy tests that run beside the live signals - logged and scored, never a BUY, never an alert.

macd_loose  "MACD histogram turn (loose)" - the rule Diagnostics measured as leading_compare(kind="macd_turn", variant="loose"):
            the engine leans to a side, the MACD histogram turns that way on the last closed candle (leading.fires), the side's score
            passes the minimum, and the candle closed inside the live entry window. Every other engine rule (trend, chasing,
            sideways, RSI band ...) is ignored on purpose - that is what makes it 'loose'.
            30-day NIFTY replay (7 Oct - 6 Nov 2026, 5 min, score 60): 26 extra trades, 50% winners, +5.1 pts each with the live
            trailing exit, positive in both halves - promising but thin, so it is forward-tested here before it may ever BUY.

detect(...) records at most one entry per closed candle; results(...) scores the logged entries on candles exactly like Diagnostics
(entry = open of the next candle, live trailing exit: stop 1.5 ATR, trail 0.75 ATR once +1.2 ATR, square-off 15:20, 4 pts cost).
"""
import sqlite3
from datetime import datetime, timedelta

from indicators import IST

STRATEGIES = {"macd_loose": "MACD histogram turn (loose) - paper only",
              # v1.20.75: the two other "more signals" candidates of the 30-day Diagnostics (13 Oct - 12 Nov 2026), forward-tested the same way
              "stoch_loose": "Stochastic cross (loose) - paper only",
              "early_trend": "Early trend entry (Priority score) - paper only"}
SHORT = {"macd_loose": "MACD turn", "stoch_loose": "Stochastic", "early_trend": "Early trend"}
LEADING_KIND = {"macd_loose": "macd_turn", "stoch_loose": "stoch"}
MODES = {"off": (), "on": ("macd_loose",), "all": ("macd_loose", "stoch_loose", "early_trend")}   # the Signals-page setting
EARLY_MAX_PER_DAY = 3                 # same limits as diagnostics.strong_trend_events
SYMBOLS = ("NIFTY",)                  # tested on NIFTY only
HORIZON, COST = 12, 4.0               # same as the Diagnostics defaults


def init(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
            strategy TEXT NOT NULL, symbol TEXT NOT NULL, interval INTEGER NOT NULL, day TEXT NOT NULL, bar_time TEXT NOT NULL,
            side TEXT NOT NULL, signal_close REAL, atr REAL, score INTEGER, created_at TEXT,
            PRIMARY KEY (strategy, symbol, interval, day, bar_time, side))""")
        db.commit()


def _bar_minute(c):
    t = c["_dt"].astimezone(IST)
    return t.hour * 60 + t.minute


def check_macd_loose(a, cs_closed, interval, in_window):
    """The entry (dict) when the loose MACD rule fires on the last closed candle, else None.
    a: the engine's analysis of exactly these candles; in_window(minute) -> bool is the live entry window."""
    return check_leading_loose(a, cs_closed, interval, in_window, "macd_loose")


def check_leading_loose(a, cs_closed, interval, in_window, strategy):
    """v1.20.75: the loose rule for any leading trigger (macd_loose, stoch_loose) - engine side, score passing, entry window,
    trigger fires on the last closed candle. Exactly what diagnostics.leading_compare measures as variant 'loose'."""
    import leading as _ld
    kind = LEADING_KIND[strategy]
    side = a.get("side")
    if side not in ("CE", "PE") or not cs_closed or not a.get("atr"):
        return None
    score_gate = next((g for g in a.get("gates") or [] if str(g.get("label", "")).startswith("Score")), None)
    if not score_gate or not score_gate.get("pass"):
        return None
    last = cs_closed[-1]
    close_min = _bar_minute(last) + interval
    if not in_window(close_min):
        return None
    if not _ld.fires(cs_closed, len(cs_closed) - 1, side, kind):
        return None
    t = last["_dt"].astimezone(IST)
    return {"strategy": strategy, "side": side, "day": t.date().isoformat(), "bar_time": t.strftime("%H:%M"),
            "signal_close": float(last["close"]), "atr": float(a["atr"]), "score": int(a.get("score") or 0)}


def check_early_trend(pa, cs_closed, interval, in_window, today_rows=()):
    """v1.20.75: the entry when the Early trend rule (trend_entry.check, variant 'early') allows this closed candle, else None.
    pa: the analysis of exactly these candles with the PRIORITY score model and strong-trend entries off - the same inputs
    diagnostics.strong_trend_events uses. Not on a bar that is already a confirmed signal, only inside the entry window, at most
    EARLY_MAX_PER_DAY a day and not within HORIZON candles of the previous paper entry (one trade at a time)."""
    import trend_entry as _te
    side, atr = pa.get("side"), pa.get("atr")
    if side not in ("CE", "PE") or not atr or not cs_closed or str(pa.get("signal") or "").startswith("CONFIRMED"):
        return None
    last = cs_closed[-1]
    close_min = _bar_minute(last) + interval
    if not in_window(close_min):
        return None
    t = last["_dt"].astimezone(IST)
    mine = [r for r in today_rows if r.get("day") == t.date().isoformat()]
    if len(mine) >= EARLY_MAX_PER_DAY:
        return None
    for r in mine:
        try:
            hh, mm = str(r["bar_time"]).split(":")
            if 0 <= _bar_minute(last) - (int(hh) * 60 + int(mm)) < HORIZON * interval:
                return None
        except Exception:
            continue
    failed = [g["label"] for g in pa.get("gates") or [] if not g.get("pass")]
    px, e9 = pa.get("last_close"), pa.get("ema9")
    stretch = (((e9 - px) if side == "PE" else (px - e9)) / atr) if (px is not None and e9 is not None) else None
    vwap = pa.get("vwap") if pa.get("vwap") is not None else pa.get("vwap_approx")
    if not _te.check(cs_closed, side, atr, pa.get("rsi"), vwap, stretch, failed, "early"):
        return None
    return {"strategy": "early_trend", "side": side, "day": t.date().isoformat(), "bar_time": t.strftime("%H:%M"),
            "signal_close": float(last["close"]), "atr": float(atr), "score": int(pa.get("score") or 0)}


def record(db_path, symbol, interval, entry, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            cur = db.execute("INSERT OR IGNORE INTO paper_trades (strategy, symbol, interval, day, bar_time, side, signal_close, atr, score, created_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (entry["strategy"], symbol, interval, entry["day"], entry["bar_time"], entry["side"], entry["signal_close"],
                              entry["atr"], entry["score"], datetime.now(IST).isoformat(timespec="seconds")))
            db.commit()
            return bool(cur.rowcount)
    if lock:
        with lock:
            return run()
    return run()


def rows(db_path, strategy, symbol, start, end, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(
                "SELECT * FROM paper_trades WHERE strategy=? AND symbol=? AND day>=? AND day<=? ORDER BY day, bar_time",
                (strategy, symbol, start, end))]
    if lock:
        with lock:
            return run()
    return run()


def results(logged, raw_candles, interval, now=None):
    """Score logged entries on the candles (same rules as Diagnostics). Returns totals, halves and the trade list."""
    import diagnostics as dg
    from indicators import normalize_candles
    cs = normalize_candles(raw_candles or [])
    idx = {(c["_dt"].astimezone(IST).date().isoformat(), c["_dt"].astimezone(IST).strftime("%H:%M")): i for i, c in enumerate(cs)}
    now = now or datetime.now(IST)
    trades, events = [], []
    for r in logged:
        if int(r.get("interval") or interval) != interval:
            continue
        i = idx.get((r["day"], r["bar_time"]))
        if i is None:
            trades.append({**_pub(r), "status": "no candles", "pts": None})
            continue
        events.append({"i": i, "side": r["side"], "atr": r["atr"], "_row": r})
    for ev in dg.evaluate(cs, events, interval, HORIZON, COST, 30, 20, with_extras=True):
        r = ev["_row"]
        pts = ev["strat"].get(dg.TRAIL_NAME)
        last_dt = cs[min(len(cs) - 1, ev["i"] + ev["bars"])]["_dt"] + timedelta(minutes=interval)
        open_ = ev["bars"] < HORIZON and r["day"] == now.date().isoformat() and now.hour * 60 + now.minute < 15 * 60 + 20 \
            and last_dt.astimezone(IST) >= now - timedelta(minutes=2 * interval)
        trades.append({**_pub(r), "entry": round(ev["entry"], 2), "status": "open - so far" if open_ else "closed",
                       "pts": round(pts, 1) if pts is not None else None, "mfe": round(ev["mfe"], 1), "mae": round(ev["mae"], 1)})
    done = [t for t in trades if t["status"] == "closed" and t["pts"] is not None]
    days = sorted({t["day"] for t in done})
    cut = days[len(days) // 2] if len(days) >= 4 else None
    out = {"all": dg.stats([t["pts"] for t in done]), "days": len(days),
           "trades": sorted(trades, key=lambda t: (t["day"], t["bar_time"]), reverse=True)}
    if cut:
        out["first"] = dg.stats([t["pts"] for t in done if t["day"] < cut])
        out["second"] = dg.stats([t["pts"] for t in done if t["day"] >= cut])
    out["verdict"] = verdict(out)
    return out


def verdict(res):
    a, f, s = res.get("all") or {}, res.get("first") or {}, res.get("second") or {}
    n = a.get("n", 0)
    if n < 15:
        return f"collecting - {n} closed paper trades so far (needs 15+ before any judgement)"
    both = f.get("avg", 0) > 0 and s.get("avg", 0) > 0
    se = a.get("se") or 0
    if a.get("avg", 0) <= 0:
        return "loses - keep it paper only (or switch it off)"
    if both and a["avg"] > 2 * se:
        return "works so far - positive in both halves and beyond its noise; re-check with Diagnostics before it may BUY"
    return "positive, not proven yet (needs both halves positive and the average beyond its noise)"


def _pub(r):
    return {k: r.get(k) for k in ("day", "bar_time", "side", "signal_close", "atr", "score")}


FIELDS = ("strategy", "symbol", "interval", "day", "bar_time", "side", "signal_close", "atr", "score", "created_at")


def dump(db_path, keep_days=90, lock=None):
    """All paper rows of the last `keep_days` as JSON-able lists (mirrored to the accounts database: Render's disk is wiped on restart)."""
    since = (datetime.now(IST).date() - timedelta(days=keep_days)).isoformat()

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            return [list(r) for r in db.execute(f"SELECT {','.join(FIELDS)} FROM paper_trades WHERE day>=? ORDER BY day, bar_time", (since,))]
    if lock:
        with lock:
            return run()
    return run()


def load(db_path, data, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            n = 0
            for r in data or []:
                if len(r) == len(FIELDS):
                    n += db.execute(f"INSERT OR IGNORE INTO paper_trades ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})", r).rowcount
            db.commit()
            return n
    if lock:
        with lock:
            return run()
    return run()
