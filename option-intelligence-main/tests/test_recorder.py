import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recorder as rec  # noqa: E402
from indicators import IST  # noqa: E402


def test_states_dedupe_timeline_and_reports(tmp_path):
    db = str(tmp_path / "r.sqlite3")
    rec.init(db)
    t = datetime(2026, 11, 9, 10, 0, tzinfo=IST)
    s1 = {"page": "terminal", "device": "phone-browser", "version": "1.20.56", "selects": {"symbol": "NIFTY 50", "apiKey": "x"},
          "texts": {"finalAction": "WAIT"}, "warnings": []}
    assert rec.add_state(db, s1, t) is True
    assert rec.add_state(db, s1, t + timedelta(minutes=1)) is False                      # unchanged: no new row
    s2 = dict(s1, texts={"finalAction": "BUY CE"}, warnings=["MIXED INDEX: x"])
    assert rec.add_state(db, s2, t + timedelta(minutes=2)) is True
    out = rec.states(db, "2026-11-09", "2026-11-09")
    k = "terminal · phone-browser"
    assert out["latest"][k]["texts"]["finalAction"] == "BUY CE" and "apiKey" not in out["latest"][k]["selects"]
    assert any("finalAction: WAIT → BUY CE" in c for c in out["timeline"][0]["changed"])
    rid = rec.add_report(db, "chart wrong", s2, "data:image/jpeg;base64,AAAA", t)
    got = rec.reports(db, "2026-11-09", "2026-11-09")
    assert got[0]["id"] == rid and got[0]["image_url"].endswith(f"/{rid}/image") and got[0]["state"]["page"] == "terminal"
    assert rec.report_image(db, rid) == "data:image/jpeg;base64,AAAA"
    db2 = str(tmp_path / "s.sqlite3"); rec.init(db2); rec.load_reports(db2, rec.dump_reports(db))
    assert rec.reports(db2, "2026-11-09", "2026-11-09")[0]["note"] == "chart wrong"
