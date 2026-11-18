import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_neon_and_render_savings_settings():
    import main as M, accounts as acc
    assert M.MIRROR_GAP_SEC == 3600 and M.HEARTBEAT_SEC == 3600 and acc.CACHE_TTL == 3600
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "if _WAS_OPEN[0] and not is_open:" in src and "_SL_URGENT[0] or time.time() - _SL_LAST_MIRROR[0] >= MIRROR_GAP_SEC" in src
    assert M.WS_ANALYSIS_MIN_SEC == 15 and M.WS_INDEX_MIN_SEC == 0.3
    M._WS_BYTES.clear()
    M._WS_BYTES[(M.ist_now().date().isoformat(), "analysis")] = [3, 3_000_000]
    assert M.ws_bytes_today()["total_mb"] == 3.0


def test_move_database_guards():
    import accounts as acc
    for bad in ("", "mysql://x", "http://x"):
        try:
            acc.target_test(bad)
            assert False
        except acc.AccountError:
            pass
    assert acc.COPY_TABLES[0] == "users" and acc.COPY_TABLES.index("sessions") > 0
    html = (ROOT / "static" / "account.html").read_text(encoding="utf-8")
    assert 'id="dbMovePanel" hidden' in html and "/api/admin/db/copy" in (ROOT / "static" / "mobile.js").read_text(encoding="utf-8")


def test_db_error_text_hides_the_address():
    import main as M
    assert "secret" not in M._db_err(Exception("could not connect to postgres://u:secret@h/db"))
