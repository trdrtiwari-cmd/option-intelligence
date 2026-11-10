"""v1.20.56: page recorder - what each page showed, and one-tap problem reports with a picture of the page.

page_states    every page sends a compact snapshot of what is on screen (choices in every menu, the numbers and texts with an
               id, the warnings shown) when it changes, checked once a minute, and at least every 15 minutes. One row per change;
               at most MAX_STATES_PER_DAY rows a day. Download for Claude / the review link show the latest per page + device and
               the day's timeline, so screenshots are no longer needed to see what the app displayed.
client_reports the "⚑ Report" button: an optional note, the snapshot and a JPEG of the page. The newest MAX_REPORTS are kept.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

MAX_STATES_PER_DAY = 600
MAX_STATE_BYTES = 24000
MAX_REPORTS = 40
MAX_IMAGE_BYTES = 900_000          # data-URL length (about 650 kB of JPEG)


def init(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS page_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, at TEXT NOT NULL, page TEXT, device TEXT, version TEXT,
            hash TEXT, data TEXT)""")
        db.execute("CREATE INDEX IF NOT EXISTS page_states_day ON page_states (day, page, device)")
        db.execute("""CREATE TABLE IF NOT EXISTS client_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, at TEXT NOT NULL, page TEXT, device TEXT, version TEXT,
            note TEXT, state TEXT, image TEXT)""")
        db.commit()


def _clean_state(state):
    """Keep it small and JSON-safe; drop anything that looks like a secret."""
    if not isinstance(state, dict):
        return None
    out = {}
    for k in ("page", "path", "title", "device", "version", "viewport", "scroll"):
        if k in state:
            out[k] = str(state[k])[:200]
    for k in ("selects", "checks", "inputs", "texts"):
        v = state.get(k)
        if isinstance(v, dict):
            out[k] = {str(a)[:60]: str(b)[:200] for a, b in list(v.items())[:260]
                      if not any(s in str(a).lower() for s in ("pass", "token", "secret", "key"))}
    for k in ("warnings", "toasts"):
        v = state.get(k)
        if isinstance(v, list):
            out[k] = [str(x)[:240] for x in v[:30]]
    data = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    while len(data) > MAX_STATE_BYTES and out.get("texts"):
        keep = dict(list(out["texts"].items())[: max(0, len(out["texts"]) * 3 // 4)])
        if len(keep) == len(out["texts"]):
            break
        out["texts"] = keep
        data = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    return data if len(data) <= MAX_STATE_BYTES else None


def add_state(db_path, state, now, lock=None):
    data = _clean_state(state)
    if not data:
        return False
    d = json.loads(data)
    h = hashlib.sha1(data.encode("utf-8")).hexdigest()[:16]
    day = now.date().isoformat()

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            last = db.execute("SELECT hash FROM page_states WHERE day=? AND page=? AND device=? ORDER BY id DESC LIMIT 1",
                              (day, d.get("page"), d.get("device"))).fetchone()
            if last and last[0] == h:
                db.execute("UPDATE page_states SET at=? WHERE id=(SELECT MAX(id) FROM page_states WHERE day=? AND page=? AND device=?)",
                           (now.isoformat(timespec="seconds"), day, d.get("page"), d.get("device")))
                db.commit()
                return False
            if db.execute("SELECT COUNT(*) FROM page_states WHERE day=?", (day,)).fetchone()[0] >= MAX_STATES_PER_DAY:
                return False
            db.execute("INSERT INTO page_states (day, at, page, device, version, hash, data) VALUES (?,?,?,?,?,?,?)",
                       (day, now.isoformat(timespec="seconds"), d.get("page"), d.get("device"), d.get("version"), h, data))
            db.execute("DELETE FROM page_states WHERE day < ?", ((now.date() - timedelta(days=10)).isoformat(),))
            db.commit()
            return True
    if lock:
        with lock:
            return run()
    return run()


def _diff(a, b):
    """Short list of what changed between two snapshots (menus, ticks, texts, warnings)."""
    out = []
    for k in ("selects", "checks", "inputs", "texts"):
        x, y = a.get(k) or {}, b.get(k) or {}
        for key in y:
            if x.get(key) != y[key]:
                out.append(f"{key}: {x.get(key, '—')} → {y[key]}"[:160])
    for w in b.get("warnings") or []:
        if w not in (a.get("warnings") or []):
            out.append("⚠ " + w[:150])
    return out


def states(db_path, start, end, lock=None, timeline_limit=120):
    """latest: the newest snapshot per page + device; timeline: what changed, in order (text only)."""
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM page_states WHERE day>=? AND day<=? ORDER BY id", (start, end))]
    rows = _locked(lock, run) if lock else run()
    latest, prev, timeline = {}, {}, []
    for r in rows:
        k = f"{r['page']} · {r['device']}"
        d = json.loads(r["data"] or "{}")
        if k in prev:
            ch = _diff(prev[k], d)
            if ch:
                timeline.append({"at": r["at"], "where": k, "changed": ch[:12]})
        prev[k] = d
        latest[k] = {"at": r["at"], "version": r["version"], **d}
    return {"latest": latest, "timeline": timeline[-timeline_limit:], "snapshots": len(rows)}


def _locked(lock, fn):
    with lock:
        return fn()


def add_report(db_path, note, state, image, now, lock=None):
    data = _clean_state(state) or "{}"
    d = json.loads(data)
    img = image if isinstance(image, str) and image.startswith("data:image/") and len(image) <= MAX_IMAGE_BYTES else None

    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            cur = db.execute("INSERT INTO client_reports (day, at, page, device, version, note, state, image) VALUES (?,?,?,?,?,?,?,?)",
                             (now.date().isoformat(), now.isoformat(timespec="seconds"), d.get("page"), d.get("device"),
                              d.get("version"), str(note or "")[:1000], data, img))
            rid = cur.lastrowid
            db.execute("DELETE FROM client_reports WHERE id <= ?", (rid - MAX_REPORTS,))
            db.commit()
            return rid
    return _locked(lock, run) if lock else run()


def reports(db_path, start, end, lock=None, with_state=True):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(
                "SELECT id, day, at, page, device, version, note, state, image IS NOT NULL AS has_image FROM client_reports "
                "WHERE day>=? AND day<=? ORDER BY id DESC", (start, end))]
    rows = _locked(lock, run) if lock else run()
    for r in rows:
        r["has_image"] = bool(r["has_image"])
        r["image_url"] = f"/api/client-reports/{r['id']}/image" if r["has_image"] else None
        st = r.pop("state")
        if with_state:
            r["state"] = json.loads(st or "{}")
    return rows


def report_image(db_path, rid, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            row = db.execute("SELECT image FROM client_reports WHERE id=?", (rid,)).fetchone()
            return row[0] if row else None
    return _locked(lock, run) if lock else run()


def dump_reports(db_path, lock=None, keep=12):
    """The newest reports with their pictures - mirrored to the accounts database (Render's disk is wiped on restart)."""
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            return [list(r) for r in db.execute(
                "SELECT id, day, at, page, device, version, note, state, image FROM client_reports ORDER BY id DESC LIMIT ?", (keep,))]
    return _locked(lock, run) if lock else run()


def load_reports(db_path, data, lock=None):
    def run():
        with sqlite3.connect(db_path, timeout=5) as db:
            for r in data or []:
                if len(r) == 9:
                    db.execute("INSERT OR IGNORE INTO client_reports (id, day, at, page, device, version, note, state, image) VALUES (?,?,?,?,?,?,?,?,?)", r)
            db.commit()
    return _locked(lock, run) if lock else run()


def utc_today():
    return datetime.now(timezone.utc).date()
