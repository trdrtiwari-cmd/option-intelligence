"""Find the front-month index future from Upstox's public instrument file, without loading the whole file.

The NSE instrument dump is large (tens of MB once decompressed). Parsing it with json.loads could exhaust a
small Render instance, so it is streamed: bytes are decompressed chunk by chunk, flat `{...}` records are
split out with a regex, and only records containing "FUT" are JSON-parsed. Memory stays at a few MB.
"""
import json
import re
import zlib
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
_OBJ = re.compile(rb"\{[^{}]*\}")
MAX_BUFFER = 4 * 1024 * 1024


class FutureScanner:
    """Feed it gzip bytes as they arrive; `.rows` collects every FUT record."""

    def __init__(self, eq_symbols=()):
        self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        self._buf = b""
        self.rows = []
        self.indices = []      # NSE_INDEX rows (sector index keys)
        self.equities = []     # NSE_EQ rows, only for `eq_symbols` (offline fallback for the breadth list)
        self._eq_symbols = set(eq_symbols or ())
        self.scanned = 0
        self.overflowed = False

    def feed(self, chunk):
        self._buf += self._d.decompress(chunk)
        last = 0
        for m in _OBJ.finditer(self._buf):
            last = m.end()
            self.scanned += 1
            raw = m.group(0)
            if b'"FUT"' in raw:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if row.get("instrument_type") == "FUT":
                    self.rows.append(row)
            elif b'"INDEX"' in raw:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if row.get("instrument_type") == "INDEX" and row.get("segment") == "NSE_INDEX":
                    self.indices.append(row)
            elif self._eq_symbols and b'"EQ"' in raw:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if (row.get("instrument_type") == "EQ" and row.get("segment") == "NSE_EQ"
                        and row.get("trading_symbol") in self._eq_symbols):
                    self.equities.append(row)
        self._buf = self._buf[last:]
        if len(self._buf) > MAX_BUFFER:  # not flat records (format changed) - stop growing
            self._buf = b""
            self.overflowed = True


def row_expiry(row):
    """YYYY-MM-DD for an instrument row (epoch-ms int or date string)."""
    val = row.get("expiry")
    if isinstance(val, (int, float)):
        seconds = val / 1000 if val > 1e12 else val
        return datetime.fromtimestamp(seconds, tz=IST).strftime("%Y-%m-%d")
    text = str(val or "")[:10]
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def pick_front_future(rows, symbol, index_key, today, roll_days=3):
    """Nearest index future that has not expired. Within `roll_days` of expiry volume has already migrated
    to the next month, so the next contract is used then."""
    cands = []
    for r in rows:
        if r.get("instrument_type") != "FUT" or not r.get("instrument_key"):
            continue
        if r.get("segment") not in (None, "NSE_FO"):
            continue
        if not (r.get("underlying_key") == index_key or str(r.get("underlying_symbol", "")).upper() == symbol.upper()):
            continue
        exp = row_expiry(r)
        if exp and exp >= today.isoformat():
            cands.append((exp, r))
    cands.sort(key=lambda x: x[0])
    if not cands:
        return None
    pick = cands[0]
    days_left = (datetime.strptime(pick[0], "%Y-%m-%d").date() - today).days
    if days_left <= roll_days and len(cands) > 1:
        pick = cands[1]
    exp, r = pick
    return {"instrument_key": r["instrument_key"], "trading_symbol": r.get("trading_symbol") or f"{symbol} FUT {exp}",
            "expiry": exp, "lot_size": r.get("lot_size")}
