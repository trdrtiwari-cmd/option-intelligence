from indicators import candle_confirmation


def bar(o, h, l, c, time='2026-10-26T10:00:00+05:30'):
    return {'open': o, 'high': h, 'low': l, 'close': c, 'volume': 100, 'time': time}


def test_bullish_engulfing_confirms_ce():
    result = candle_confirmation([
        bar(105, 106, 99, 100),
        bar(99, 111, 98, 109),
    ], 'CE')
    assert result['pattern'] == 'BULLISH_ENGULFING'
    assert result['confirmed'] is True


def test_bearish_engulfing_confirms_pe():
    result = candle_confirmation([
        bar(100, 106, 99, 105),
        bar(106, 107, 94, 96),
    ], 'PE')
    assert result['pattern'] == 'BEARISH_ENGULFING'
    assert result['confirmed'] is True


def test_strong_bullish_breakout_confirms_ce():
    result = candle_confirmation([
        bar(100, 105, 98, 102),
        bar(103, 112, 102, 111),
    ], 'CE')
    assert result['pattern'] == 'BULLISH_BREAKOUT_CANDLE'
    assert result['confirmed'] is True


def test_doji_does_not_confirm():
    result = candle_confirmation([
        bar(100, 103, 98, 102),
        bar(100.0, 105, 95, 100.2),
    ], 'CE')
    assert result['pattern'] == 'DOJI_INDECISION'
    assert result['confirmed'] is False


def test_opposite_pattern_does_not_confirm_side():
    result = candle_confirmation([
        bar(100, 106, 99, 105),
        bar(106, 107, 94, 96),
    ], 'CE')
    assert result['pattern'] == 'BEARISH_ENGULFING'
    assert result['confirmed'] is False
