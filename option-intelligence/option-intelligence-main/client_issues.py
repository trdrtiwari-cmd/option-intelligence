"""v1.20.49: problems the pages hit on your phones / computers (script errors, failed requests, warnings shown), collected on the server.

Before, these lived only in the open Terminal tab until "Download for Claude" was pressed there, so problems on the Chart, Signals or Tests
tabs, or on another device, were never seen. Now every page sends them here (batched, de-duplicated) and the Download for Claude files
(day / week / month) carry them. One row per (day, page, kind, message) with a count; at most MAX_PER_DAY rows a day.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

MAX_PER_DAY = 400
KINDS = ("error", "request", "warning", "message")
FIELDS = ("day", "page", "kind", "msg", "detail", "device", "version", "count", "first_at", "last_at")


def init(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS client_issues (
            day TEXT NOT NULL, page TEXT NOT NULL, kind TEXT NOT NULL, msg TEXT NOT NULL, detail TEXT, device TEXT, version TEXT,
            count INTEGER NOT NULL DEFAULT 1, first_at TEXT, last_at TEXT, PRIMARY KEY (day, page, kind, msg))""")
        db.commit()


def clean(items, now):
    """Validate a batch from a page. Returns rows ready to store (bad items are dropped, text is trimmed)."""
    out = []
    for it in (items or [])[:60]:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "")
        msg = str(it.get("msg") or "").strip()
        if kind not in KINDS or not msg:
            continue
        out.append({"day": now.date().isoformat(), "page": str(it.get("page") or "?")[:40], "kind": kind, "msg": msg[:300],
                    "detail": str(it.get("detail") or "")[:300], "device": str(it.get("device") or "")[:40],
                    "version": str(it.get("version") or "")[:20], "n": max(1, min(1000, int(it.get("n") or 1))),
                    "at": now.isoformat(timespec="seconds")})
    return out


def add(db_path, rows, lock=None):
    def run():
        n = 0
        with sqlite3.connect(db_path, timeout=5) as db:
            for r in rows:
                cur = db.execute("UPDATE client_issues SET count = count + ?, last_at = ?, detail = ?, device = ?, version = ? "
                                 "WHERE day = ? AND page = ? AND kind = ? AND msg = ?",
                                 (r["n"], r["at"], r["detail"], r["device"], r["version"], r["day"], r["page"], r["kind"], r["msg"]))
                if cur.rowcount:
                    n += 1
                    continue
                if db.execute("SELECT COUNT(*) FROM client_issues WHERE day = ?", (r["day"],)).fetchone()[0] >= MAX_PER_DAY:
                    continue
                db.execute("INSERT INTO client_issues (day, page, kind, msg, detail, device, version, count, first_at, last_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (r["day"], r["page"], r["kind"], r["msg"], r["detail"], r["device"], r["version"], r["n"], r["at"], r["at"]))
                n += 1
            db.commit()
        return n
    if lock:
        with lock:
            return run()
    return run()


def query(db_path, start, end, lock=None, limit=300):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(
                "SELECT * FROM client_issues WHERE day >= ? AND day <= ? ORDER BY kind = 'error' DESC, count DESC, last_at DESC LIMIT ?",
                (start, end, limit))]
    if lock:
        with lock:
            return run()
    return run()


def summary(rows):
    """Short lines for the checks at the top of the review file."""
    if not rows:
        return []
    by = {}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)
    out = []
    for kind in KINDS:
        rs = by.get(kind) or []
        if not rs:
            continue
        top = sorted(rs, key=lambda r: -r["count"])[:3]
        out.append(f"PAGE {kind.upper()}S reported by your devices: {len(rs)} different ({sum(r['count'] for r in rs)} times) - most often: "
                   + "; ".join(f"[{r['page']}] {r['msg'][:90]} (x{r['count']})" for r in top))
    return out


def dump(db_path, keep_days=14, lock=None):
    since = (datetime.now(timezone.utc).date() - timedelta(days=keep_days)).isoformat()

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            return [list(r) for r in db.execute(f"SELECT {','.join(FIELDS)} FROM client_issues WHERE day >= ?", (since,))]
    if lock:
        with lock:
            return run()
    return run()


def load(db_path, data, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            for r in data or []:
                if len(r) == len(FIELDS):
                    db.execute(f"INSERT OR IGNORE INTO client_issues ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})", r)
            db.commit()
    if lock:
        with lock:
            return run()
    return run()


def to_json(data):
    return json.dumps(data, separators=(",", ":"))
