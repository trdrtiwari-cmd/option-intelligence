"""Auto-exit engine for the Daily Signal Log.

Every logged CONFIRMED BUY CE / BUY PE setup is a paper trade measured in INDEX POINTS (not option premium, not rupees).
A trade is closed automatically by whichever happens first, checked on every price update the server sees:

  1. STOP      - index crosses the stop-loss.        Filled at the observed price (a gap through the stop is a real loss).
  2. TARGET    - index reaches the "safe target".    Filled at the target level (a resting limit order).
  3. SQUAREOFF - 15:20 IST (or after the session).   Filled at the observed price.
  4. EXPIRED   - trade is from an earlier session and was never settled (server asleep) AND no candles cover it. No P/L is invented.

v1.19 - CANDLE-ACCURATE exits. The old engine only looked at the price it happened to observe, so a server that slept (or a 3-second
gap in the feed) booked a "stop" at whatever price it woke up to: a 15-pt stop was logged as a -74-pt loss. When candles are supplied the
engine now walks every bar after the entry bar: the stop / target is filled AT its level (a bar that opened beyond the stop is filled
at the open - a real gap), stop first when one bar touches both, and an earlier session's trade is reconstructed from its candles to the
15:20 square-off instead of being thrown away. Each closed trade also gets its OPTION premium result (buy price, exit, net Rs per lot).

If stop and target are both crossed in the same observation the STOP wins (pessimistic: the order of events is unknown).
Net P/L = gross points - round-trip cost allowance (SIGNAL_LOG_COST_POINTS, default 4 index points).

Environment:
  SIGNAL_LOG_TARGET        T1 | T2 | T3   which ATR target is the auto-exit target (default T2 = 3 ATR; T1 = 1.5 ATR lost money in the replays)
  SIGNAL_LOG_COST_POINTS   round-trip cost allowance in index points (default 4)
  SIGNAL_LOG_SQUAREOFF     HH:MM IST time-stop for same-day trades (default 15:20)
"""
import os
import sqlite3
from datetime import datetime, time as dtime, timedelta

import option_plan as op
from indicators import IST, normalize_candles

TARGET_NAMES = ("T1", "T2", "T3")
EXTRA_COLUMNS = [("trade_status", "TEXT DEFAULT 'OPEN'"), ("exit_price", "REAL"), ("gross_pnl_points", "REAL"),
                 ("net_pnl_points", "REAL"), ("exit_time", "TEXT"), ("exit_reason", "TEXT"), ("target_name", "TEXT"),
                 ("peak_points", "REAL"), ("worst_points", "REAL"), ("levels_backfilled", "INTEGER DEFAULT 0"),
                 ("atr_at_entry", "REAL"), ("sl_atr_mult", "REAL"), ("target_atr_mult", "REAL"),
                 # v1.19 option leg: the contract bought, its premium entry / stop / target / exit, and the result per lot
                 ("contract", "TEXT"), ("opt_key", "TEXT"), ("opt_entry", "REAL"), ("opt_sl", "REAL"), ("opt_target", "REAL"),
                 ("opt_exit", "REAL"), ("opt_delta", "REAL"), ("lot", "INTEGER"), ("opt_gross", "REAL"), ("opt_net", "REAL"),
                 ("net_rs", "REAL"), ("opt_basis", "TEXT"), ("buy_basis", "TEXT"), ("rr", "REAL")]


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


def walk_candles(candles, entry_dt, interval, side, entry, sl, tp, until=None):
    """Replay the bars AFTER the entry bar against a trade. candles = normalize_candles() output. Returns
    {"status": "STOP"|"TARGET"|None, "fill": price, "at": datetime, "peak": pts, "worst": pts, "last_close": price, "last_at": dt}.
    The entry bar itself is skipped (its range includes prices from before the signal). Stop first when one bar touches both."""
    sign = 1 if str(side).upper() == "CE" else -1
    step = timedelta(minutes=interval or 5)
    first_start = None
    for c in candles:                                   # start of the bar the signal arrived in
        if c["_dt"] <= entry_dt < c["_dt"] + step:
            first_start = c["_dt"] + step
            break
    if first_start is None:
        first_start = entry_dt
    out = {"status": None, "fill": None, "at": None, "peak": 0.0, "worst": 0.0, "last_close": None, "last_at": None}
    for c in candles:
        if c["_dt"] < first_start:
            continue
        if until is not None and c["_dt"] >= until:
            break
        hi, lo, op_, cl = c["high"], c["low"], c["open"], c["close"]
        fav = (hi - entry) if sign == 1 else (entry - lo)
        adv = (lo - entry) if sign == 1 else (entry - hi)
        stop_hit = sl is not None and ((sign == 1 and lo <= sl) or (sign == -1 and hi >= sl))
        tgt_hit = tp is not None and ((sign == 1 and hi >= tp) or (sign == -1 and lo <= tp))
        if stop_hit:
            gap = sl is not None and ((sign == 1 and op_ <= sl) or (sign == -1 and op_ >= sl))
            out.update(status="STOP", fill=op_ if gap else sl, at=c["_dt"], worst=min(out["worst"], adv))
            return out
        if tgt_hit:
            out.update(status="TARGET", fill=tp, at=c["_dt"], peak=max(out["peak"], fav))
            return out
        out["peak"], out["worst"] = max(out["peak"], fav), min(out["worst"], adv)
        out["last_close"], out["last_at"] = cl, c["_dt"] + step
    return out


def settle_open_signals(db_path, symbol, price, now, atr_backfill=None, lock=None, candles=None, interval=None, opt_ltp=None):
    """Close every OPEN trade of `symbol` that hit stop / target / square-off.

    price: latest index price (may be None when only candles are available).  now: timezone-aware datetime in IST.
    atr_backfill: {"sl_points", "target_points"} used only for old rows saved without levels.
    candles: raw candle rows (any interval) for exact, candle-accurate fills - see the module docstring.
    opt_ltp: callable(instrument_key) -> latest option premium or None, used as the exit premium for exits seen live.
    Returns a list of dicts describing trades closed now."""
    price = _num(price)
    cfg = config()
    closed = []
    today = now.date().isoformat()
    cs = normalize_candles(candles) if candles else []
    square_dt = datetime.combine(now.date(), cfg["squareoff"], tzinfo=IST)

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            ensure_columns(db)
            rows = db.execute("SELECT id,session_date,side,entry,last_close,stop_loss,target,target_name,peak_points,worst_points,generated_at,"
                              "opt_entry,opt_delta,lot,opt_key FROM signal_log WHERE symbol=? AND COALESCE(trade_status,'OPEN')='OPEN'", (symbol,)).fetchall()
            for rid, sdate, side, entry, last_close, sl, tp, tname, peak, worst, gen_at, o_entry, o_delta, lot, o_key in rows:
                sign = 1 if str(side).upper() == "CE" else -1
                entry = _num(entry) or _num(last_close)
                sl, tp = _num(sl), _num(tp)
                backfilled = 0
                if entry is not None and (sl is None or tp is None) and atr_backfill:
                    sl = entry - sign * atr_backfill["sl_points"]
                    tp = entry + sign * atr_backfill["target_points"]
                    tname = cfg["target"]
                    backfilled = 1
                    db.execute("UPDATE signal_log SET entry=?,stop_loss=?,target=?,target_name=?,levels_backfilled=1 WHERE id=?",
                               (entry, round(sl, 2), round(tp, 2), tname, rid))
                past = str(sdate) < today
                status = reason = None
                exit_px = None
                exit_dt = now
                live_exit = False
                cw = None
                until = None
                if cs and entry is not None:
                    try:
                        e_dt = datetime.fromisoformat(str(gen_at)) if gen_at else None
                    except ValueError:
                        e_dt = None
                    if e_dt is not None:
                        if e_dt.tzinfo is None:
                            e_dt = e_dt.replace(tzinfo=IST)
                        step = interval or (max(1.0, round((cs[1]["_dt"] - cs[0]["_dt"]).total_seconds() / 60.0)) if len(cs) > 1 else 5)
                        day_sq = datetime.combine(e_dt.astimezone(IST).date(), cfg["squareoff"], tzinfo=IST)
                        until = day_sq if (past or now >= square_dt) else None
                        if past and not any(c["_dt"].astimezone(IST).date().isoformat() == str(sdate) for c in cs):
                            cw = None
                        else:
                            cw = walk_candles(cs, e_dt, step, side, entry, sl, tp, until=until)
                if cw and cw["status"]:
                    status, exit_px, exit_dt = cw["status"], cw["fill"], cw["at"].astimezone(IST)
                    reason = ("Stop-loss crossed" if status == "STOP" else f"{tname or cfg['target']} safe target reached") + " (candle-accurate fill)"
                    peak, worst = max(_num(peak) or 0.0, cw["peak"]), min(_num(worst) or 0.0, cw["worst"])
                elif cw and until is not None and cw["last_close"] is not None:
                    # held to the square-off with neither level touched: exit at the close of the last bar before 15:20
                    status, exit_px, exit_dt = "SQUAREOFF", cw["last_close"], cw["last_at"].astimezone(IST)
                    reason = f"Time exit at {cfg['squareoff'].strftime('%H:%M')} IST (candle close)" + (" - reconstructed after the server missed it" if past else "")
                    peak, worst = max(_num(peak) or 0.0, cw["peak"]), min(_num(worst) or 0.0, cw["worst"])
                elif past:
                    db.execute("UPDATE signal_log SET trade_status='EXPIRED',exit_time=?,exit_reason=? WHERE id=?",
                               (now.isoformat(timespec="seconds"),
                                "Session ended before the exit was observed (server asleep or no price). No P/L recorded.", rid))
                    closed.append({"id": rid, "status": "EXPIRED", "net": None})
                    continue
                else:
                    if price is None or entry is None:
                        continue
                    move = (price - entry) * sign
                    peak = move if peak is None else max(_num(peak) or move, move)
                    worst = move if worst is None else min(_num(worst) or move, move)
                    if cw:
                        peak, worst = max(peak, cw["peak"]), min(worst, cw["worst"])
                    exit_px = price
                    live_exit = True
                    if sl is not None and ((sign == 1 and price <= sl) or (sign == -1 and price >= sl)):
                        status, reason = "STOP", "Stop-loss crossed"
                    elif tp is not None and ((sign == 1 and price >= tp) or (sign == -1 and price <= tp)):
                        status, reason, exit_px = "TARGET", f"{tname or cfg['target']} safe target reached", tp
                    elif now.time() >= cfg["squareoff"] or (now.time() > dtime(15, 30)):
                        status, reason = "SQUAREOFF", f"Time exit at {cfg['squareoff'].strftime('%H:%M')} IST"
                if status:
                    gross = (exit_px - entry) * sign
                    net = gross - cfg["cost"]
                    live = None
                    if live_exit and opt_ltp and o_key and status != "TARGET":
                        try: live = opt_ltp(o_key)
                        except Exception: live = None
                    pp = op.premium_pnl(o_entry, o_delta, gross, cfg["cost"], lot, live)
                    db.execute("UPDATE signal_log SET trade_status=?,exit_price=?,gross_pnl_points=?,net_pnl_points=?,exit_time=?,"
                               "exit_reason=?,peak_points=?,worst_points=?,opt_exit=?,opt_gross=?,opt_net=?,net_rs=?,opt_basis=? WHERE id=?",
                               (status, round(exit_px, 2), round(gross, 2), round(net, 2), exit_dt.isoformat(timespec="seconds"),
                                reason, round(max(_num(peak) or 0.0, gross), 2), round(worst if worst is not None else 0.0, 2),
                                pp["exit"], pp["gross"], pp["net"], pp["net_rs"], pp["basis"], rid))
                    closed.append({"id": rid, "status": status, "exit_price": exit_px, "gross": gross, "net": net,
                                   "backfilled": bool(backfilled)})
                elif peak is not None:
                    db.execute("UPDATE signal_log SET peak_points=?,worst_points=? WHERE id=?", (round(_num(peak) or 0.0, 2), round(_num(worst) or 0.0, 2), rid))
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


CLOSED = ("TARGET", "STOP", "SQUAREOFF")


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
            "target_exits": cnt("TARGET"), "stop_exits": cnt("STOP"), "time_exits": cnt("SQUAREOFF"),
            "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "gross_points": round(gross, 2), "cost_points": round(gross - net, 2), "net_points": round(net, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if wins and sum(losses) < 0 else None,
            "expectancy": round(net / len(closed), 2) if closed else None, "best": round(max(nets), 2) if nets else None,
            "worst": round(min(nets), 2) if nets else None, "max_drawdown": round(dd_max, 2), "by_symbol": by_sym,
            "net_rs": round(sum(float(r["net_rs"]) for r in closed if _num(r.get("net_rs")) is not None)) if any(_num(r.get("net_rs")) is not None for r in closed) else None}


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
