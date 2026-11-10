import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import client_issues as cis  # noqa: E402
from indicators import IST  # noqa: E402


def test_collect_dedupe_summary_and_mirror(tmp_path):
    db = str(tmp_path / "c.sqlite3")
    cis.init(db)
    now = datetime(2026, 11, 6, 10, 0, tzinfo=IST)
    rows = cis.clean([{"kind": "error", "msg": "x is undefined", "page": "chart", "n": 2}, {"kind": "bogus", "msg": "?"},
                      {"kind": "request", "msg": "GET /api/analysis -> HTTP 500", "page": "terminal"}, {"kind": "warning", "msg": ""}], now)
    assert [r["kind"] for r in rows] == ["error", "request"]
    assert cis.add(db, rows) == 2
    assert cis.add(db, cis.clean([{"kind": "error", "msg": "x is undefined", "page": "chart"}], now)) == 1
    got = cis.query(db, "2026-11-06", "2026-11-06")
    assert got[0]["kind"] == "error" and got[0]["count"] == 3 and len(got) == 2
    lines = cis.summary(got)
    assert lines[0].startswith("PAGE ERRORS") and "(x3)" in lines[0]
    db2 = str(tmp_path / "d.sqlite3")
    cis.init(db2)
    data = [r for r in cis.dump(db, keep_days=10000)]
    cis.load(db2, data)
    assert len(cis.query(db2, "2026-11-06", "2026-11-06")) == 2


def test_merge_volume_refuses_futures_that_end_a_session_early():
    import indicators as ind
    idx = [{"time": f"2026-11-0{d}T10:{m:02d}:00+05:30", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 0} for d in (5, 6) for m in range(0, 60, 5)]
    fut_old = [dict(c, volume=100) for c in idx if c["time"].startswith("2026-11-05")]
    assert ind.merge_volume(idx, fut_old + fut_old[:1]) is None                        # 6 Nov would get volume 0
    full = [dict(c, volume=100) for c in idx]
    m = ind.merge_volume(idx, full)
    assert m and all(r["volume"] == 100 for r in m)
