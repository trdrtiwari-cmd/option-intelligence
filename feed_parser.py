"""Parser for decoded Upstox Market Data Feed V3 messages (pure Python, unit-testable).

The SDK hands us a dict (protobuf -> dict, camelCase). Depending on the subscription mode the
useful block lives in different places:

  full          feeds[key].fullFeed.marketFF   { ltpc{ltp,ltt,cp}, oi, iv, optionGreeks{...}, ... }
  full (index)  feeds[key].fullFeed.indexFF    { ltpc{...} }
  option_greeks feeds[key].firstLevelWithGreeks{ ltpc{...}, oi, iv, optionGreeks{...} }
  ltpc          feeds[key].ltpc                { ltp, ltt, cp }

OI and IV sit directly on the market block (NOT under an "eFeedDetails" object as in the older
V2 feed), which is why the previous version always showed "—" for OI / IV.
"""


def _num(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _blocks(feed):
    """Yield candidate dict blocks of a single instrument feed, most specific first."""
    if not isinstance(feed, dict):
        return
    full = feed.get("fullFeed")
    if isinstance(full, dict):
        for name in ("marketFF", "indexFF"):
            if isinstance(full.get(name), dict):
                yield full[name]
    if isinstance(feed.get("firstLevelWithGreeks"), dict):
        yield feed["firstLevelWithGreeks"]
    if isinstance(feed.get("ltpc"), dict) and "ltp" in feed["ltpc"]:
        yield {"ltpc": feed["ltpc"]}
    yield feed


def normalize_iv(iv):
    """Return IV as a percentage. Decimal-looking values (0 < iv < 3) are scaled by 100."""
    iv = _num(iv)
    if iv is None or iv <= 0:
        return None
    return iv * 100 if iv < 3 else iv


def parse_feed_message(message):
    """Return (current_ts, {instrument_key: {ltp, ltt, oi, iv, close_price, bid, ask, bid_qty, ask_qty, vtt}})."""
    if not isinstance(message, dict):
        return None, {}
    out = {}
    for key, feed in (message.get("feeds") or {}).items():
        ltp = ltt = oi = iv = cp = None
        bid = ask = bid_qty = ask_qty = vtt = None
        for block in _blocks(feed):
            ltpc = block.get("ltpc") if isinstance(block.get("ltpc"), dict) else {}
            if ltp is None:
                ltp = _num(ltpc.get("ltp"))
            if ltt is None:
                ltt = ltpc.get("ltt")
            if cp is None:
                cp = _num(ltpc.get("cp"))
            if oi is None:
                oi = _num(block.get("oi"))
            if iv is None:
                greeks = block.get("optionGreeks") if isinstance(block.get("optionGreeks"), dict) else {}
                iv = normalize_iv(block.get("iv") if block.get("iv") is not None else greeks.get("iv"))
            # Best bid / ask (market depth level 1) and day volume - used for the spread and liquidity gates.
            level = block.get("marketLevel") if isinstance(block.get("marketLevel"), dict) else {}
            book = level.get("bidAskQuote")
            if isinstance(book, list) and book and isinstance(book[0], dict) and (bid is None or ask is None):
                top = book[0]
                b, a = _num(top.get("bidP")), _num(top.get("askP"))
                if b is not None and b > 0 and bid is None:
                    bid, bid_qty = b, _num(top.get("bidQ"))
                if a is not None and a > 0 and ask is None:
                    ask, ask_qty = a, _num(top.get("askQ"))
            if vtt is None:
                vtt = _num(block.get("vtt"))
        if ltp is None and oi is None and iv is None:
            continue
        out[key] = {"ltp": ltp, "ltt": ltt, "oi": oi, "iv": iv, "close_price": cp,
                    "bid": bid, "ask": ask, "bid_qty": bid_qty, "ask_qty": ask_qty, "vtt": vtt}
    return message.get("currentTs") or message.get("current_ts"), out
