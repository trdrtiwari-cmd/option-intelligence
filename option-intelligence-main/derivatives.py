"""Derivatives filters (v1.20.57): option-chain OI, PCR trend, futures price + OI buildup, heavyweight stocks, IV / India VIX,
ATM straddle decay, event days and a max-BUYs-per-day cap. Pure Python, no web framework.

Why: the technical score is built only from price indicators. These checks add what option writers / positioning say, so a
signal that fires into heavy call writing (CE) or put writing (PE), or into an IV spike before an event, can be blocked or
down-scored.

Modes (Signals page "Derivatives filters", or DERIV_MODE in the environment):
  off     nothing is fetched or shown (exactly v1.20.56 behaviour)
  record  everything is computed and recorded on the signal board, but the score and the signal are NOT changed (default)
  gates   Option A - the technical score is unchanged; the checks are extra pass / fail gates
  score   Option B - the checks are added to the technical score with their own weights (the 0-100 score changes)

Missing data never blocks: a check without data is "unavailable" (left out of the score, a gate passes and says so).
Diagnostics replays cannot test these checks (Upstox has no history of option-chain OI / IV), so judge them from the
"record" rows on the signal board.
"""
import os
from collections import deque
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
MODES = ("off", "record", "gates", "score")
MODE_LABELS = {"off": "Off", "record": "Record only (score and signals unchanged)",
               "gates": "Option A - separate gates (score unchanged)", "score": "Option B - inside the score"}


def _envf(name, default):
    try:
        return float(os.getenv(name, str(default)) or default)
    except ValueError:
        return float(default)


DEFAULT_MODE = (os.getenv("DERIV_MODE", "record") or "record").strip().lower()
if DEFAULT_MODE not in MODES:
    DEFAULT_MODE = "record"
REFRESH_SEC = _envf("DERIV_REFRESH_SEC", 30)            # option chain / VIX / stock reads at most this often per index
OI_LOOKBACK_MIN = _envf("DERIV_OI_LOOKBACK_MIN", 15)    # OI change and PCR trend are measured over this window
VOL_LOOKBACK_MIN = _envf("DERIV_VOL_LOOKBACK_MIN", 30)  # IV and straddle change window
ATM_STRIKES = int(_envf("DERIV_ATM_STRIKES", 3))        # +/- strikes around ATM for the writers check
OI_BIAS = _envf("DERIV_OI_BIAS", 0.2)                   # (put OI added - call OI added) / total added must pass +/- this
PCR_STEP = _envf("DERIV_PCR_STEP", 0.03)                # PCR change over the window that counts as rising / falling
FUT_BARS = int(_envf("DERIV_FUT_BARS", 3))              # futures price + OI compared over the last N closed candles
FUT_OI_MIN_PCT = _envf("DERIV_FUT_OI_MIN_PCT", 0.05)    # OI change below this % is "flat"
VIX_SPIKE_PCT = _envf("DERIV_VIX_SPIKE_PCT", 5)         # India VIX up this % on the day = volatility spike
IV_SPIKE_PCT = _envf("DERIV_IV_SPIKE_PCT", 10)          # ATM IV up this % over the window = volatility spike
STRADDLE_DECAY_PCT = _envf("DERIV_STRADDLE_DECAY_PCT", 3)   # straddle down this % over the window ...
RANGE_MOVE_PCT = _envf("DERIV_RANGE_MOVE_PCT", 0.15)    # ... while the index moved less than this % = premium-decay range
MIN_NET = int(_envf("DERIV_MIN_NET", 1))                # Option A: agreeing checks minus opposing checks must be at least this
EVENT_BLOCK_UNTIL = os.getenv("EVENT_BLOCK_UNTIL", "11:30")
# RBI policy decision days (RBI press release, 23 Mar 2026) and the Union Budget. Edit on the Signals page or with EVENT_DAYS.
EVENT_DAYS_DEFAULT = os.getenv("EVENT_DAYS", "2026-12-04,2027-02-01,2027-02-05")
MAX_BUYS_DEFAULT = int(_envf("MAX_BUYS_PER_DAY", 0))    # 0 = no cap
HEAVYWEIGHTS = {"BANKNIFTY": [s.strip().upper() for s in os.getenv("HEAVYWEIGHTS_BANKNIFTY", "HDFCBANK,ICICIBANK").split(",") if s.strip()],
                "NIFTY": [s.strip().upper() for s in os.getenv("HEAVYWEIGHTS_NIFTY", "HDFCBANK,RELIANCE").split(",") if s.strip()]}

WEIGHTS = {"fut_oi": 10, "atm_oi": 10, "pcr_trend": 6, "heavy": 8, "vol_calm": 6, "no_decay": 5}
DIRECTIONAL = ("fut_oi", "atm_oi", "pcr_trend", "heavy")


def clean_mode(v):
    v = str(v or "").strip().lower()
    return v if v in MODES else DEFAULT_MODE


def parse_event_days(text):
    out = []
    for p in str(text or "").replace(";", ",").split(","):
        p = p.strip()
        try:
            out.append(datetime.strptime(p, "%Y-%m-%d").date().isoformat())
        except ValueError:
            continue
    return sorted(set(out))


def _hm(text, default=(11, 30)):
    try:
        h, m = str(text).split(":")
        return int(h), int(m)
    except ValueError:
        return default


class History:
    """Per-index time series of chain readings (in memory; rebuilt during the session after a restart)."""

    def __init__(self, keep_min=90):
        self.keep = keep_min * 60
        self.rows = {}

    def add(self, sym, ts, **vals):
        d = self.rows.setdefault(sym, deque())
        if d and ts - d[-1]["ts"] < 5:
            d[-1].update(vals)
            return
        if d and datetime.fromtimestamp(d[-1]["ts"], IST).date() != datetime.fromtimestamp(ts, IST).date():
            d.clear()                                     # new session: yesterday's readings say nothing about today
        d.append(dict(vals, ts=ts))
        while d and ts - d[0]["ts"] > self.keep:
            d.popleft()

    def ago(self, sym, ts, minutes, field):
        """(value, minutes_back) of the newest reading at least `minutes` old with `field` set, else the oldest usable one if it is
        at least half the window old; (None, None) without enough history."""
        d = self.rows.get(sym) or ()
        best = None
        for r in d:
            if r.get(field) is None:
                continue
            age = (ts - r["ts"]) / 60
            if age >= minutes:
                best = (r[field], age)
            elif best is None and age >= minutes / 2:
                return r[field], age
        return best if best else (None, None)

    def latest(self, sym, field):
        for r in reversed(self.rows.get(sym) or ()):
            if r.get(field) is not None:
                return r[field]
        return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def chain_reading(rows, spot, strikes_each_side=ATM_STRIKES):
    """From Upstox option-chain rows: PCR, near-ATM call / put OI (and day change from prev_oi), ATM IV and ATM straddle."""
    good = [r for r in rows or [] if _f(r.get("strike_price")) is not None]
    spot = _f(spot) or _f((good[0] if good else {}).get("underlying_spot_price"))
    if not good or not spot:
        return None
    good.sort(key=lambda r: _f(r["strike_price"]))
    i_atm = min(range(len(good)), key=lambda i: abs(_f(good[i]["strike_price"]) - spot))
    near = good[max(0, i_atm - strikes_each_side): i_atm + strikes_each_side + 1]
    tc = tp = 0.0
    nc = np_ = 0
    for r in good:
        c = _f(((r.get("call_options") or {}).get("market_data") or {}).get("oi"))
        p = _f(((r.get("put_options") or {}).get("market_data") or {}).get("oi"))
        if c is not None:
            tc += c; nc += 1
        if p is not None:
            tp += p; np_ += 1
    ce_oi = pe_oi = ce_prev = pe_prev = 0.0
    have_prev = True
    for r in near:
        cm = (r.get("call_options") or {}).get("market_data") or {}
        pm = (r.get("put_options") or {}).get("market_data") or {}
        ce_oi += _f(cm.get("oi")) or 0
        pe_oi += _f(pm.get("oi")) or 0
        if _f(cm.get("prev_oi")) is None or _f(pm.get("prev_oi")) is None:
            have_prev = False
        ce_prev += _f(cm.get("prev_oi")) or 0
        pe_prev += _f(pm.get("prev_oi")) or 0
    atm = good[i_atm]
    co, po = atm.get("call_options") or {}, atm.get("put_options") or {}
    cm, pm = co.get("market_data") or {}, po.get("market_data") or {}
    ivs = [x for x in (_f((co.get("option_greeks") or {}).get("iv", cm.get("iv"))), _f((po.get("option_greeks") or {}).get("iv", pm.get("iv")))) if x and x > 0]
    c_ltp, p_ltp = _f(cm.get("ltp")), _f(pm.get("ltp"))
    return {"spot": spot, "atm_strike": _f(atm["strike_price"]),
            "pcr": round(tp / tc, 4) if tc > 0 and nc and np_ else None,
            "atm_ce_oi": ce_oi or None, "atm_pe_oi": pe_oi or None,
            "atm_ce_add_day": (ce_oi - ce_prev) if have_prev and ce_prev else None,
            "atm_pe_add_day": (pe_oi - pe_prev) if have_prev and pe_prev else None,
            "atm_iv": round(sum(ivs) / len(ivs), 2) if ivs else None,
            "straddle": round(c_ltp + p_ltp, 2) if c_ltp and p_ltp else None}


def futures_buildup(fut_candles, bars=FUT_BARS):
    """Price + OI over the last `bars` closed futures candles -> (state, price change, OI change %) or None."""
    cs = [c for c in fut_candles or [] if _f(c.get("close")) is not None and _f(c.get("oi"))]
    if len(cs) < bars + 1:
        return None
    a, b = cs[-bars - 1], cs[-1]
    dp = _f(b["close"]) - _f(a["close"])
    oi0, oi1 = _f(a["oi"]), _f(b["oi"])
    doi_pct = (oi1 - oi0) / oi0 * 100 if oi0 else 0.0
    if abs(doi_pct) < FUT_OI_MIN_PCT or dp == 0:
        state = "FLAT"
    elif dp > 0:
        state = "LONG BUILDUP" if doi_pct > 0 else "SHORT COVERING"
    else:
        state = "SHORT BUILDUP" if doi_pct > 0 else "LONG UNWINDING"
    return {"state": state, "price_change": round(dp, 2), "oi_change_pct": round(doi_pct, 3), "bars": bars}


def _check(key, label, ok, bull=False, bear=False, detail=""):
    return {"key": "deriv_" + key, "label": label, "weight": WEIGHTS[key], "ok": bool(ok), "bull": bool(ok and bull),
            "bear": bool(ok and bear), "detail": detail, "derivatives": True}


def build_checks(sym, now_ts, hist, fut=None, heavy=None, vix=None):
    """Score-style checks (same shape as indicators.analyze checks). `heavy` = {stock: (close, vwap) or None},
    `vix` = {"last", "prev_close"} or None. Chain readings come from `hist` (call History.add first)."""
    checks = []
    # 1. futures price + OI
    if fut:
        st = fut["state"]
        checks.append(_check("fut_oi", "Futures price + OI buildup", True, bull=st == "LONG BUILDUP", bear=st == "SHORT BUILDUP",
                             detail=f"{st} · {fut['price_change']:+.1f} pts, OI {fut['oi_change_pct']:+.2f}% over {fut['bars']} candles"
                                    + (" (covering / unwinding = weak move, supports neither side)" if st in ("SHORT COVERING", "LONG UNWINDING") else "")))
    else:
        checks.append(_check("fut_oi", "Futures price + OI buildup", False, detail="futures OI unavailable"))
    # 2. option writers near ATM
    ce_now, pe_now = hist.latest(sym, "atm_ce_oi"), hist.latest(sym, "atm_pe_oi")
    ce_then, age = hist.ago(sym, now_ts, OI_LOOKBACK_MIN, "atm_ce_oi")
    pe_then, _ = hist.ago(sym, now_ts, OI_LOOKBACK_MIN, "atm_pe_oi")
    if ce_now is not None and pe_now is not None and ce_then is not None and pe_then is not None:
        dce, dpe, basis = ce_now - ce_then, pe_now - pe_then, f"last {age:.0f} min"
    else:
        dce, dpe, basis = hist.latest(sym, "atm_ce_add_day"), hist.latest(sym, "atm_pe_add_day"), "since yesterday's close"
    if dce is not None and dpe is not None and (abs(dce) + abs(dpe)) > 0:
        bias = (dpe - dce) / (abs(dce) + abs(dpe))
        checks.append(_check("atm_oi", f"Option writers near ATM (±{ATM_STRIKES} strikes)", True, bull=bias >= OI_BIAS, bear=bias <= -OI_BIAS,
                             detail=f"put OI {dpe:+,.0f} vs call OI {dce:+,.0f} ({basis}) · bias {bias:+.2f}"
                                    + (" · put writing = support" if bias >= OI_BIAS else " · call writing = resistance" if bias <= -OI_BIAS else " · balanced")))
    else:
        checks.append(_check("atm_oi", f"Option writers near ATM (±{ATM_STRIKES} strikes)", False, detail="option-chain OI unavailable"))
    # 3. PCR trend
    pcr_now = hist.latest(sym, "pcr")
    pcr_then, age = hist.ago(sym, now_ts, OI_LOOKBACK_MIN, "pcr")
    if pcr_now is not None and pcr_then is not None:
        d = pcr_now - pcr_then
        checks.append(_check("pcr_trend", f"PCR trend ({OI_LOOKBACK_MIN:g} min)", True, bull=d >= PCR_STEP, bear=d <= -PCR_STEP,
                             detail=f"PCR {pcr_then:.2f} → {pcr_now:.2f} ({d:+.3f} in {age:.0f} min)"))
    else:
        checks.append(_check("pcr_trend", f"PCR trend ({OI_LOOKBACK_MIN:g} min)", False,
                             detail=f"building history (PCR {pcr_now:.2f} now)" if pcr_now is not None else "PCR unavailable"))
    # 4. heavyweights vs VWAP
    names = HEAVYWEIGHTS.get(sym) or []
    got = {n: v for n, v in (heavy or {}).items() if v and v[0] is not None and v[1] is not None}
    if names and len(got) == len(names):
        above = [n for n, (c, w) in got.items() if c > w]
        below = [n for n, (c, w) in got.items() if c < w]
        checks.append(_check("heavy", "Heavyweights vs VWAP (" + " + ".join(names) + ")", True, bull=len(above) == len(names), bear=len(below) == len(names),
                             detail=" · ".join(f"{n} {c:,.1f} {'>' if c > w else '<' if c < w else '='} VWAP {w:,.1f}" for n, (c, w) in got.items())))
    else:
        checks.append(_check("heavy", "Heavyweights vs VWAP (" + " + ".join(names) + ")", False, detail="stock prices unavailable"))
    # 5. volatility calm (both sides lose these points on a spike = a score penalty in Option B)
    parts, spike, have = [], False, False
    if vix and _f(vix.get("last")) and _f(vix.get("prev_close")):
        have = True
        ch = (_f(vix["last"]) - _f(vix["prev_close"])) / _f(vix["prev_close"]) * 100
        spike |= ch >= VIX_SPIKE_PCT
        parts.append(f"India VIX {_f(vix['last']):.2f} ({ch:+.1f}% today)")
    iv_now = hist.latest(sym, "atm_iv")
    iv_then, age = hist.ago(sym, now_ts, VOL_LOOKBACK_MIN, "atm_iv")
    if iv_now is not None:
        have = True
        if iv_then:
            ch = (iv_now - iv_then) / iv_then * 100
            spike |= ch >= IV_SPIKE_PCT
            parts.append(f"ATM IV {iv_then:.1f} → {iv_now:.1f} ({ch:+.1f}% in {age:.0f} min)")
        else:
            parts.append(f"ATM IV {iv_now:.1f}")
    checks.append(_check("vol_calm", "Volatility calm (IV / VIX not spiking)", have, bull=not spike, bear=not spike,
                         detail=(" · ".join(parts) + (" — SPIKE: premiums inflated, IV crush risk" if spike else "")) if have else "IV / VIX unavailable"))
    # 6. ATM straddle decay with a flat index = premium-decay range (bad for option buyers)
    s_now, sp_now = hist.latest(sym, "straddle"), hist.latest(sym, "spot")
    s_then, age = hist.ago(sym, now_ts, VOL_LOOKBACK_MIN, "straddle")
    sp_then, _ = hist.ago(sym, now_ts, VOL_LOOKBACK_MIN, "spot")
    if s_now and s_then and sp_now and sp_then:
        dec = (s_then - s_now) / s_then * 100
        mv = abs(sp_now - sp_then) / sp_then * 100
        rng = dec >= STRADDLE_DECAY_PCT and mv < RANGE_MOVE_PCT
        checks.append(_check("no_decay", "Not a premium-decay range (ATM straddle)", True, bull=not rng, bear=not rng,
                             detail=f"straddle {s_then:,.1f} → {s_now:,.1f} ({-dec:+.1f}%) while the index moved {mv:.2f}% in {age:.0f} min"
                                    + (" — RANGE: sellers winning, buyers lose to decay" if rng else "")))
    else:
        checks.append(_check("no_decay", "Not a premium-decay range (ATM straddle)", False,
                             detail="building history" if s_now else "straddle price unavailable"))
    return checks


def score_with(tech_checks, deriv_checks):
    """(bull, bear) 0-100 with the derivatives checks added to the technical checks (Option B)."""
    allc = list(tech_checks or []) + list(deriv_checks or [])
    avail = sum(c["weight"] for c in allc if c.get("ok"))
    if not avail:
        return 0, 0
    return (round(sum(c["weight"] for c in allc if c.get("ok") and c.get("bull")) / avail * 100),
            round(sum(c["weight"] for c in allc if c.get("ok") and c.get("bear")) / avail * 100))


def gate_rows(side, checks):
    """Option A gates for `side` from the checks. Unavailable data passes and says so."""
    if side not in ("CE", "PE"):
        return []
    want = "bull" if side == "CE" else "bear"
    other = "bear" if side == "CE" else "bull"
    by = {c["key"][6:]: c for c in checks}
    dirs = [by[k] for k in DIRECTIONAL if k in by and by[k]["ok"]]
    agree = [c for c in dirs if c[want]]
    against = [c for c in dirs if c[other]]
    out = []
    if dirs:
        net = len(agree) - len(against)
        out.append({"label": f"Derivatives agree with {side} (agree − against ≥ {MIN_NET})", "pass": net >= MIN_NET, "derivatives": True,
                    "detail": f"{len(agree)} agree, {len(against)} against, {len(dirs) - len(agree) - len(against)} neutral of {len(dirs)}"
                              + (" · against: " + "; ".join(c["label"] + ": " + c["detail"] for c in against) if against else "")})
    else:
        out.append({"label": f"Derivatives agree with {side}", "pass": True, "derivatives": True, "detail": "no OI / PCR / stock data yet - not blocking"})
    for k in ("vol_calm", "no_decay"):
        c = by.get(k)
        if c:
            out.append({"label": c["label"], "pass": (not c["ok"]) or c[want], "derivatives": True,
                        "detail": c["detail"] if c["ok"] else c["detail"] + " - not blocking"})
    return out


def risk_gates(side, now, event_days, max_buys, buys_today):
    """Gates that apply in Option A and Option B: event-day morning block and the max-BUYs-per-day cap."""
    if side not in ("CE", "PE"):
        return []
    out = []
    today = now.astimezone(IST).date().isoformat()
    if today in (event_days or []):
        h, m = _hm(EVENT_BLOCK_UNTIL)
        ok = (now.astimezone(IST).hour, now.astimezone(IST).minute) >= (h, m)
        out.append({"label": f"Event day: no entries before {h:02d}:{m:02d} (RBI / Budget)", "pass": ok, "derivatives": True,
                    "detail": f"{today} is on the event list" + ("" if ok else " - the announcement swings IV and both sides get stopped")})
    if max_buys and buys_today is not None:
        out.append({"label": f"Max {max_buys} BUY(s) per day", "pass": buys_today < max_buys, "derivatives": True,
                    "detail": f"{buys_today} BUY(s) taken today" + ("" if buys_today < max_buys else " - done for the day")})
    return out


def summary(mode, side, tech_checks, checks, gates_a, risk, tech_bull, tech_bear):
    """What the filters said / would say - attached to the analysis and written on the signal board."""
    bull_b, bear_b = score_with(tech_checks, checks)
    blk = [g["label"] for g in (gates_a + risk) if not g["pass"]]
    dom_b = bull_b if side == "CE" else bear_b if side == "PE" else max(bull_b, bear_b)
    verdict = ("A: " + ("BLOCK (" + "; ".join(blk) + ")" if blk else "PASS") if side else "A: no side") + f" · B score {dom_b}"
    return {"mode": mode, "mode_label": MODE_LABELS[mode], "side": side, "checks": checks, "gates_a": gates_a, "risk_gates": risk,
            "would_block_a": bool(blk), "blocked_by_a": blk, "score_b": {"bull": bull_b, "bear": bear_b, "side": dom_b},
            "score_tech": {"bull": tech_bull, "bear": tech_bear}, "verdict": verdict,
            "agree_text": " · ".join(f"{c['label']}: {'CE' if c['bull'] and not c['bear'] else 'PE' if c['bear'] and not c['bull'] else 'ok' if c['bull'] else '—' if c['ok'] else 'n/a'}"
                                     for c in checks)}
