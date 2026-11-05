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
  SIGNAL_LOG_TARGET        T1 | T2 | T3   which ATR target is the auto-exit target (default T2 = 3 ATR; T1 = 1.5 ATR lost money in the replays)
  SIGNAL_LOG_COST_POINTS   round-trip cost allowance in index points (default 4)
  SIGNAL_LOG_SQUAREOFF     HH:MM IST time-stop for same-day trades (default 15:20)
"""
import os
import sqlite3
from datetime import datetime, time as dtime

TARGET_NAMES = ("T1", "T2", "T3")
EXTRA_COLUMNS = [("trade_status", "TEXT DEFAULT 'OPEN'"), ("exit_price", "REAL"), ("gross_pnl_points", "REAL"),
                 ("net_pnl_points", "REAL"), ("exit_time", "TEXT"), ("exit_reason", "TEXT"), ("target_name", "TEXT"),
                 ("peak_points", "REAL"), ("worst_points", "REAL"), ("levels_backfilled", "INTEGER DEFAULT 0"),
                 ("atr_at_entry", "REAL"), ("sl_atr_mult", "REAL"), ("target_atr_mult", "REAL"),
                 # v1.19: the option that was bought, its premium plan, and the money result
                 ("option_key", "TEXT"), ("option_name", "TEXT"), ("option_strike", "REAL"), ("option_expiry", "TEXT"),
                 ("option_entry", "REAL"), ("option_sl", "REAL"), ("option_target", "REAL"), ("option_delta", "REAL"),
                 ("option_exit", "REAL"), ("option_exit_source", "TEXT"), ("option_pnl_points", "REAL"),
                 ("lot_size", "INTEGER"), ("qty", "INTEGER"), ("charges_rs", "REAL"), ("net_pnl_rs", "REAL"),
                 ("target_basis", "TEXT"), ("rr", "REAL"), ("exit_basis", "TEXT"),
                 # v1.20.9 trailing exit plan: start trailing once the trade is +trail_start_pts, stop trail_pts behind the best price
                 ("exit_plan", "TEXT"), ("trail_start_pts", "REAL"), ("trail_pts", "REAL")]


# Runtime overrides (v1.18.7). Set from the Signals page / stored in the database, so ATR multiples can be changed without a
# redeploy. Only NEW signals use them; trades already logged keep the stop and target they were recorded with.
OVERRIDES = {}


def set_overrides(d):
    OVERRIDES.clear()
    for k in ("sl_atr", "target_atr", "cost", "target"):
        v = (d or {}).get(k)
        if v not in (None, ""):
            OVERRIDES[k] = v


def _ov_float(key, lo, hi):
    try:
        v = float(OVERRIDES.get(key))
        return v if lo <= v <= hi else None
    except (TypeError, ValueError):
        return None


def custom_levels(side, price, atr, sl_mult, target_mult, min_sl_points=0.0):
    """Stop and target from the ATR at the moment of the signal: stop = max(sl_mult x ATR, min points), target = target_mult x ATR.
    Returns (entry, stop, target, sl_points, target_points) or None."""
    price, atr = _num(price), _num(atr)
    if not price or not atr or atr <= 0 or side not in ("CE", "PE"):
        return None
    d = 1 if side == "CE" else -1
    sl_pts = max(sl_mult * atr, min_sl_points)
    tp_pts = target_mult * atr
    return (round(price, 2), round(price - d * sl_pts, 2), round(price + d * tp_pts, 2), round(sl_pts, 2), round(tp_pts, 2))


def config():
    target = str(OVERRIDES.get("target") or os.getenv("SIGNAL_LOG_TARGET", "T2")).strip().upper()
    if target not in TARGET_NAMES:
        target = "T2"
    try:
        cost = float(OVERRIDES["cost"]) if _ov_float("cost", 0, 200) is not None else float(os.getenv("SIGNAL_LOG_COST_POINTS", "4"))
    except ValueError:
        cost = 4.0
    raw = os.getenv("SIGNAL_LOG_SQUAREOFF", "15:20").strip()
    try:
        hh, mm = raw.split(":")
        square = dtime(int(hh), int(mm))
    except Exception:
        square = dtime(15, 20)
    return {"target": target, "cost": cost, "squareoff": square,
            "sl_atr": _ov_float("sl_atr", 0.2, 5.0), "target_atr": _ov_float("target_atr", 0.3, 10.0)}


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
            rows = db.execute("SELECT id,session_date,side,entry,last_close,stop_loss,target,target_name,peak_points,worst_points,trail_start_pts,trail_pts "
                              "FROM signal_log WHERE symbol=? AND COALESCE(trade_status,'OPEN')='OPEN'", (symbol,)).fetchall()
            for rid, sdate, side, entry, last_close, sl, tp, tname, peak, worst, tr_start, tr_pts in rows:
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
                prev_peak = _num(peak)
                peak = move if peak is None else max(_num(peak) or move, move)
                sl0 = sl
                if _num(tr_pts):                               # trailing stop from the best move seen BEFORE this price
                    sl = trail_stop(sl, sign, entry, prev_peak, (_num(tr_start), _num(tr_pts)))
                worst = move if worst is None else min(_num(worst) or move, move)
                reason = status = None
                exit_px = price
                if sl is not None and ((sign == 1 and price <= sl) or (sign == -1 and price >= sl)):
                    status, reason = ("TRAIL", "Trailing stop crossed") if sl != sl0 else ("STOP", "Stop-loss crossed")
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


def trail_stop(sl, sign, entry, peak, trail):
    """Effective stop: the initial stop, raised (CE) / lowered (PE) to best price - trail once the best move reached the start."""
    if not trail or trail[0] is None or trail[1] is None or peak is None or peak < trail[0] or sl is None:
        return sl
    t = entry + sign * (peak - trail[1])
    return max(sl, t) if sign == 1 else min(sl, t)


def _first_exit(path, sign, entry, sl, tp, squareoff, trail=None):
    """Walk 1-minute bars (oldest first). Stop is checked before target inside a bar (pessimistic). A bar that OPENS beyond a level
    fills at its open (a real gap); otherwise the level itself is the fill. A bar that starts at / after the square-off time exits at
    its open. Returns (status, price, bar, peak, worst) or (None, None, None, peak, worst)."""
    peak = worst = None
    sl0 = sl
    for c in path:
        t = c["_dt"]
        if t.time() >= squareoff:
            px = c["open"]
            mv = (px - entry) * sign
            return "SQUAREOFF", px, c, max(peak if peak is not None else mv, mv), min(worst if worst is not None else mv, mv)
        sl = trail_stop(sl0, sign, entry, peak, trail)     # trailing uses the best price of EARLIER bars only (conservative)
        trailed = sl is not None and sl0 is not None and sl != sl0
        hi_mv, lo_mv = (c["high"] - entry) * sign, (c["low"] - entry) * sign
        fav, adv = max(hi_mv, lo_mv), min(hi_mv, lo_mv)
        peak = fav if peak is None else max(peak, fav)
        worst = adv if worst is None else min(worst, adv)
        o = c["open"]
        if sl is not None:
            if (sign == 1 and o <= sl) or (sign == -1 and o >= sl):
                return ("TRAIL" if trailed else "STOP"), o, c, peak, worst
            if (sign == 1 and c["low"] <= sl) or (sign == -1 and c["high"] >= sl):
                return ("TRAIL" if trailed else "STOP"), sl, c, peak, worst
        if tp is not None:
            if (sign == 1 and o >= tp) or (sign == -1 and o <= tp):
                return "TARGET", o, c, peak, worst
            if (sign == 1 and c["high"] >= tp) or (sign == -1 and c["low"] <= tp):
                return "TARGET", tp, c, peak, worst
    return None, None, None, peak, worst


def settle_with_candles(db_path, symbol, candles_1m, now, lock=None):
    """Settle OPEN trades of `symbol` from the 1-minute index path since each entry (v1.19).

    Why: the old exit watcher only looked at the live price every few seconds WHILE THE SERVER WAS AWAKE. If it slept, or the feed
    stalled, the first price it saw afterwards was used as the fill - e.g. a 15-pt stop recorded as a -74-pt loss. Walking the
    1-minute candles finds the FIRST bar that touched the stop / target and fills at that level (or at the bar's open on a gap).
    `candles_1m`: indicators.normalize_candles output (aware datetimes). now: aware IST datetime. Returns the list of closed trades."""
    from datetime import timedelta
    cfg = config()
    closed = []
    if not candles_1m:
        return closed
    ist = now.tzinfo

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            ensure_columns(db)
            rows = db.execute("SELECT id,session_date,generated_at,side,entry,last_close,stop_loss,target,target_name,trail_start_pts,trail_pts "
                              "FROM signal_log WHERE symbol=? AND COALESCE(trade_status,'OPEN')='OPEN'", (symbol,)).fetchall()
            for rid, sdate, gen, side, entry, last_close, sl, tp, tname, tr_start, tr_pts in rows:
                entry = _num(entry) or _num(last_close)
                try:
                    gen_dt = datetime.fromisoformat(str(gen))
                except ValueError:
                    continue
                if entry is None or gen_dt.tzinfo is None:
                    continue
                sign = 1 if str(side).upper() == "CE" else -1
                start = gen_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)   # first full minute after the entry
                day = gen_dt.astimezone(ist).date()
                path = [c for c in candles_1m if c["_dt"] >= start and c["_dt"].astimezone(ist).date() == day]
                path = [dict(c, _dt=c["_dt"].astimezone(ist)) for c in path]
                if not path:
                    continue
                trail = (_num(tr_start), _num(tr_pts)) if _num(tr_pts) else None
                status, px, bar, peak, worst = _first_exit(path, sign, entry, _num(sl), _num(tp), cfg["squareoff"], trail)
                session_over = day < now.date() and path[-1]["_dt"].time() >= dtime(15, 29)
                if status is None and session_over:          # earlier session, never hit anything: close at its last bar
                    bar = path[-1]
                    status, px = "SQUAREOFF", bar["close"]
                if status is None:
                    if peak is not None:
                        db.execute("UPDATE signal_log SET peak_points=MAX(COALESCE(peak_points,?),?),worst_points=MIN(COALESCE(worst_points,?),?) WHERE id=?",
                                   (peak, round(peak, 2), worst, round(worst, 2), rid))
                    continue
                gross = (px - entry) * sign
                net = gross - cfg["cost"]
                when = bar["_dt"]
                reason = {"STOP": "Stop-loss touched", "TARGET": f"{tname or cfg['target']} target touched",
                          "TRAIL": f"Trailing stop touched (locked {gross:+.1f} pts)",
                          "SQUAREOFF": f"Time exit at {cfg['squareoff'].strftime('%H:%M')} IST"}[status]
                if status in ("STOP", "TARGET") and abs(px - (_num(sl) if status == "STOP" else _num(tp))) > 1e-6:
                    reason += " (gapped through: filled at the bar's open)"
                db.execute("UPDATE signal_log SET trade_status=?,exit_price=?,gross_pnl_points=?,net_pnl_points=?,exit_time=?,exit_reason=?,"
                           "peak_points=?,worst_points=?,exit_basis=? WHERE id=?",
                           (status, round(px, 2), round(gross, 2), round(net, 2), when.isoformat(timespec="seconds"), reason,
                            round(max(peak if peak is not None else gross, gross), 2), round(min(worst if worst is not None else gross, gross), 2),
                            "1-minute candles", rid))
                closed.append({"id": rid, "status": status, "exit_price": px, "gross": gross, "net": net, "exit_time": when.isoformat(timespec="seconds")})
            db.commit()

    if lock:
        with lock:
            run()
    else:
        run()
    return closed


def estimate_option_exit(option_entry, delta, index_move):
    """Premium after an index move, from delta only (used when no option price exists for the exit moment)."""
    oe, dl, mv = _num(option_entry), _num(delta), _num(index_move)
    if oe is None or dl is None or mv is None:
        return None
    return round(max(0.05, oe + dl * mv), 2)


def set_option_exit(db_path, rid, price, source, lock=None):
    """Store the option's exit premium and the money result: (exit - entry) x qty - charges."""
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            ensure_columns(db)
            r = db.execute("SELECT option_entry, qty, charges_rs FROM signal_log WHERE id=?", (rid,)).fetchone()
            if not r or _num(r[0]) is None or _num(price) is None:
                return None
            pts = float(price) - float(r[0])
            rs = pts * int(r[1]) - (_num(r[2]) or 0) if _num(r[1]) else None
            db.execute("UPDATE signal_log SET option_exit=?,option_exit_source=?,option_pnl_points=?,net_pnl_rs=? WHERE id=?",
                       (round(float(price), 2), source, round(pts, 2), None if rs is None else round(rs, 2), rid))
            db.commit()
            return rs
    if lock:
        with lock:
            return run()
    return run()


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


CLOSED = ("TARGET", "STOP", "TRAIL", "SQUAREOFF")


def _stats(rows):
    closed = [r for r in rows if r.get("trade_status") in CLOSED and _num(r.get("net_pnl_points")) is not None]
    nets = [float(r["net_pnl_points"]) for r in closed]
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x <= 0]
    cum = peak = dd_max = 0.0
    for x in nets:
        cum += x
        peak = max(peak, cum)
        dd_max = max(dd_max, peak - cum)
    gross = sum(float(r.get("gross_pnl_points") or 0) for r in closed)
    net = sum(nets)
    by_sym = {}
    for r in closed:
        by_sym[r.get("symbol")] = round(by_sym.get(r.get("symbol"), 0.0) + float(r["net_pnl_points"]), 2)
    cnt = lambda st: sum(1 for r in closed if r.get("trade_status") == st)
    side = lambda s_: sum(1 for r in rows if str(r.get("side") or "").upper() == s_)
    return {"signals": len(rows), "ce": side("CE"), "pe": side("PE"), "open": sum(1 for r in rows if (r.get("trade_status") or "OPEN") == "OPEN"),
            "expired": sum(1 for r in rows if r.get("trade_status") == "EXPIRED"), "closed": len(closed),
            "target_exits": cnt("TARGET"), "stop_exits": cnt("STOP"), "time_exits": cnt("SQUAREOFF"), "trail_exits": cnt("TRAIL"),
            "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "gross_points": round(gross, 2), "cost_points": round(gross - net, 2), "net_points": round(net, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if wins and sum(losses) < 0 else None,
            "expectancy": round(net / len(closed), 2) if closed else None, "best": round(max(nets), 2) if nets else None,
            "worst": round(min(nets), 2) if nets else None, "max_drawdown": round(dd_max, 2), "by_symbol": by_sym,
            "net_rs": round(sum(float(r["net_pnl_rs"]) for r in closed if _num(r.get("net_pnl_rs")) is not None), 2)
                      if any(_num(r.get("net_pnl_rs")) is not None for r in closed) else None,
            "rs_trades": sum(1 for r in closed if _num(r.get("net_pnl_rs")) is not None)}


def summarize(rows, group="day"):
    """Strategy results per day or per month (index points, net of the cost allowance) plus an all-period total.
    Trades are taken in exit order so the drawdown is the real running one."""
    size = 7 if group == "month" else 10
    ordered = sorted(rows, key=lambda r: (str(r.get("exit_time") or r.get("generated_at") or "")))
    buckets = {}
    for r in ordered:
        buckets.setdefault(str(r.get("session_date") or "")[:size], []).append(r)
    periods = [dict(period=k, **_stats(v)) for k, v in sorted(buckets.items(), reverse=True)]
    return {"group": group, "periods": periods, "total": _stats(ordered)}
