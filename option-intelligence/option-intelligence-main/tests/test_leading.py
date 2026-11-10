"""v1.20.20 leading triggers."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import leading as ld


def mk(closes, wick=1.0):
    out, prev = [], closes[0]
    for c in closes:
        out.append({"open": prev, "close": c, "high": max(prev, c) + wick, "low": min(prev, c) - wick})
        prev = c
    return out


def test_stoch_cross_from_oversold():
    closes = [100 + (k % 5) for k in range(40)] + [100 - 2 * k for k in range(1, 12)] + [80, 83]
    cs = mk(closes)
    fired = [i for i in range(len(cs)) if ld.fires(cs, i, "CE", "stoch")]
    assert fired and fired[-1] >= 50 and not any(ld.fires(cs, i, "PE", "stoch") for i in fired)


def test_macd_turn_and_no_lookahead():
    closes = [100.0] * 40 + [100 - 1.5 * k for k in range(1, 12)] + [84, 85.5, 87]
    cs = mk(closes)
    i = next(i for i in range(45, len(cs)) if ld.fires(cs, i, "CE", "macd_turn"))
    ld._CACHE.update(id=None)
    assert ld.fires(cs[:i + 1], i, "CE", "macd_turn")          # same answer with the future candles removed


def test_rsi_bullish_divergence():
    closes = ([100.0] * 30 + [100 - 3 * k for k in range(1, 9)] + [78, 80, 83, 85, 84, 82.5, 81, 79.5, 78, 76.5, 75, 74.2, 74, 76, 79, 82, 85])
    cs = mk(closes, 0.5)
    hits = [i for i in range(len(cs)) if ld.fires(cs, i, "CE", "rsi_div")]
    assert hits, "lower low in price with a higher RSI low should fire after the turn"
    assert not ld.fires(cs, 10, "CE", "rsi_div") and not ld.fires(cs, hits[0], "PE", "rsi_div")


def test_off_by_default():
    assert ld.LEADING_TRIGGER in ("off",) + ld.KINDS
