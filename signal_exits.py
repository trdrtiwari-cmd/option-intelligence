"""Auto-exit engine for the Daily Signal Log.

Every logged CONFIRMED BUY CE / BUY PE setup is a paper trade measured in INDEX POINTS (not option premium, not rupees).
A trade is closed automatically by whichever happens first, checked on every price update the server sees:

  1. STOP      - index crosses the stop-loss.        Filled at the observed price (a gap through the stop is a real loss).
  2. TARGET    - index reaches the "safe target".    Filled at the target level (a resting limit order).
  3. SQUAREOFF - 15:20 IST (or after the session).   Filled at the observed price.
  4. EXPIRED   - trade is from an earlier session and was never settled (server asleep). No P/L is invented.

If stop and target are both crossed in the same observation the STOP wins (pessimistic: the order of events is unknown).
Net P/L = gross points - round-trip cost allowance (SIGNAL_LOG_COST_POINTS, default 4 index points).

Environment:
  SIGNAL_LOG_TARGET        T1 | T2 | T3   which ATR target is the auto-exit target (default T1 = 1.5 ATR, the "safe" one)
  SIGNAL_LOG_COST_POINTS   round-trip cost allowance in index points (default 4)
  SIGNAL_LOG_SQUAREOFF     HH:MM IST time-stop for same-day trades (default 15:20)
"""
import os
import sqlite3
from datetime import datetime, time as dtime

TARGET_NAMES = ("T1", "T2", "T3")
EXTRA_COLUMNS = [("trade_status", "TEXT DEFAULT 'OPEN'"), ("exit_price", "REAL"), ("gross_pnl_points", "REAL"),
                 ("net_pnl_points", "REAL"), ("exit_time", "TEXT"), ("exit_reason", "TEXT"), ("target_name", "TEXT"),
                 ("peak_points", "REAL"), ("worst_points", "REAL"), ("levels_backfilled", "INTEGER DEFAULT 0")]


def config():
    target = os.getenv("SIGNAL_LOG_TARGET", "T1").strip().upper()
    if target not in TARGET_NAMES:
        target = "T1"
    try:
        cost = float(os.getenv("SIGNAL_LOG_COST_POINTS", "4"))
    except ValueError:
        cost = 4.0
    raw = os.getenv("SIGNAL_LOG_SQUAREOFF", "15:20").strip()
    try:
        hh, mm = raw.split(":")
        square = dtime(int(hh), int(mm))
    except Exception:
        square = dtime(15, 20)
    return {"target": target, "cost": cost, "squareoff": square}


def ensure_columns(db):
    existing = {r[1] for r in db.execute("PRAGMA table_info(signal_log)").fetchall()}
    for col, typ in EXTRA_COLUMNS:
        if col not in existing:
            db.execute(f"ALTER TABLE signal_log ADD COLUMN {col} {typ}")


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def pick_levels(levels, price, target_name=None):
    """Turn dashboard_data.trade_levels() output into (entry, stop, target, target_name).

    The old logger looked for keys that trade_levels() never returns (entry / stop_loss / target), so every row was saved
    without stop or target and could never exit. trade_levels() returns entry_index, sl_index and targets[].index.
    """
    target_name = (target_name or config()["target"]).upper()
    levels = levels or {}
    entry = _num(levels.get("entry_index")) or _num(price)
    stop = _num(levels.get("sl_index"))
    target = None
    for t in levels.get("targets") or []:
        if str(t.get("name", "")).upper() == target_name:
            target = _num(t.get("index"))
    return entry, stop, target, target_name


def backfill_points(levels_fn, atr, target_name):
    """Stop / target distances in index points for a given ATR, using the same rules as the live signal."""
    if not atr or atr <= 0:
        return None
    lv = levels_fn(atr)
    if not lv:
        return None
    pts = {str(t["name"]).upper(): _num(t.get("points")) for t in lv.get("targets") or []}
    sl = _num(lv.get("sl_points"))
    tp = pts.get(target_name.upper())
    if not sl or not tp:
        return None
    return {"sl_points": sl, "target_points": tp}


def settle_open_signals(db_path, symbol, price, now, atr_backfill=None, lock=None):
    """Close every OPEN trade of `symbol` that hit stop / target / square-off at `price`.

    now: timezone-aware datetime in IST.  atr_backfill: {"sl_points", "target_points"} used only for old rows saved
    without levels (entry price is the logged index price). Returns a list of dicts describing trades closed now.
    """
    price = _num(price)
    cfg = config()
    closed = []
    today = now.date().isoformat()

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            ensure_columns(db)
            rows = db.execute("SELECT id,session_date,side,entry,last_close,stop_loss,target,target_name,peak_points,worst_points "
                              "FROM signal_log WHERE symbol=? AND COALESCE(trade_status,'OPEN')='OPEN'", (symbol,)).fetchall()
            for rid, sdate, side, entry, last_close, sl, tp, tname, peak, worst in rows:
                sign = 1 if str(side).upper() == "CE" else -1
                if str(sdate) < today:
                    db.execute("UPDATE signal_log SET trade_status='EXPIRED',exit_time=?,exit_reason=? WHERE id=?",
                               (now.isoformat(timespec="seconds"),
                                "Session ended before the exit was observed (server asleep or no price). No P/L recorded.", rid))
                    closed.append({"id": rid, "status": "EXPIRED", "net": None})
                    continue
                if price is None:
                    continue
                entry = _num(entry) or _num(last_close)
                if entry is None:
                    continue
                sl, tp = _num(sl), _num(tp)
                backfilled = 0
                if (sl is None or tp is None) and atr_backfill:
                    sl = entry - sign * atr_backfill["sl_points"]
                    tp = entry + sign * atr_backfill["target_points"]
                    tname = cfg["target"]
                    backfilled = 1
                    db.execute("UPDATE signal_log SET entry=?,stop_loss=?,target=?,target_name=?,levels_backfilled=1 WHERE id=?",
                               (entry, round(sl, 2), round(tp, 2), tname, rid))
                move = (price - entry) * sign
                peak = move if peak is None else max(_num(peak) or move, move)
                worst = move if worst is None else min(_num(worst) or move, move)
                reason = status = None
                exit_px = price
                if sl is not None and ((sign == 1 and price <= sl) or (sign == -1 and price >= sl)):
                    status, reason = "STOP", "Stop-loss crossed"
                elif tp is not None and ((sign == 1 and price >= tp) or (sign == -1 and price <= tp)):
                    status, reason, exit_px = "TARGET", f"{tname or cfg['target']} safe target reached", tp
                elif now.time() >= cfg["squareoff"] or (now.time() > dtime(15, 30)):
                    status, reason = "SQUAREOFF", f"Time exit at {cfg['squareoff'].strftime('%H:%M')} IST"
                if status:
                    gross = (exit_px - entry) * sign
                    net = gross - cfg["cost"]
                    db.execute("UPDATE signal_log SET trade_status=?,exit_price=?,gross_pnl_points=?,net_pnl_points=?,exit_time=?,"
                               "exit_reason=?,peak_points=?,worst_points=? WHERE id=?",
                               (status, round(exit_px, 2), round(gross, 2), round(net, 2), now.isoformat(timespec="seconds"),
                                reason, round(max(peak, gross), 2), round(worst, 2), rid))
                    closed.append({"id": rid, "status": status, "exit_price": exit_px, "gross": gross, "net": net,
                                   "backfilled": bool(backfilled)})
                else:
                    db.execute("UPDATE signal_log SET peak_points=?,worst_points=? WHERE id=?", (round(peak, 2), round(worst, 2), rid))
            db.commit()

    if lock:
        with lock:
            run()
    else:
        run()
    return closed


def open_trade_count(db_path, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            return db.execute("SELECT symbol, COUNT(*) FROM signal_log WHERE COALESCE(trade_status,'OPEN')='OPEN' GROUP BY symbol").fetchall()
    try:
        if lock:
            with lock:
                return dict(run())
        return dict(run())
    except sqlite3.Error:
        return {}
