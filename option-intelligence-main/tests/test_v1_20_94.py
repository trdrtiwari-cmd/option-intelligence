import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import option_plan as op
import paper_tests as pt
from indicators import IST


def _cs():
    t0 = datetime(2026, 11, 18, 10, 20, tzinfo=IST)
    return [{"_dt": t0 + timedelta(minutes=5 * k), "close": 25250.0} for k in range(3)]


def _pick(**kw):
    p = {"symbol": "NIFTY", "strike": 25300, "label": "ITM 1", "ltp": 120.0, "bid": 119.8, "ask": 120.0,
         "delta": 0.55, "gamma": 0.0, "theta": -20.0, "lot_size": 65}
    p.update(kw)
    return p


def _plan(pick):
    # like 18 Nov 10:30 PE: support 0.95 ATR away -> target ~0.8 ATR, stop 1.5 ATR -> Room fails and the premium reward fails with it
    atr = 34.0
    sr = {"supports": [{"price": 25250.0 - 0.95 * atr, "strength": "Strong"}], "resistances": []}
    cfg = dict(op.DEFAULTS, min_rr=1.5, min_target_atr=1.2)
    return op.profit_plan("PE", 25250.0, atr, sr, pick, None, 1.5, 20.0, 3.0, cfg)


def test_real_plan_room_and_premium_fail_together():
    plan = _plan(_pick())
    failed = [c["label"] for c in plan["checks"] if not c["pass"]]
    assert failed[0].startswith("Room to the target") and len(failed) >= 2
    assert all(f.startswith(pt.TARGET_DERIVED) for f in failed)
    e = pt.check_room_relaxed({"side": "PE", "atr": 34.0, "score": 72}, plan, _cs())
    assert e and e["strategy"] == "room_relaxed" and e["side"] == "PE" and e["bar_time"] == "10:30"


def test_other_failures_still_block_the_paper_entry():
    plan = _plan(_pick(bid=100.0, ask=120.0))                     # wide spread: not a target problem
    assert any(c["label"].startswith("Bid/ask spread") and not c["pass"] for c in plan["checks"])
    assert pt.check_room_relaxed({"side": "PE", "atr": 34.0, "score": 72}, plan, _cs()) is None
    premium_only = {"ok": False, "index": {"reward_pts": 30.0},
                    "checks": [{"label": "Room to the target (x)", "pass": True}, {"label": "Premium reward : risk ≥ 0.8", "pass": False}]}
    assert pt.check_room_relaxed({"side": "CE", "atr": 34.0}, premium_only, _cs()) is None     # Room passed: not this test


def test_oracle_setup_fixes():
    s = (ROOT.parent / "oracle-setup.sh").read_text(encoding="utf-8")
    assert 'GITLAB_REPO="gitlab.com/trdrtiwari1/option-intelligence.git"' in s
    assert "DPkg::Lock::Timeout=900" in s and "\ntrap '" in s
    assert "{ crontab -l 2>/dev/null || true; }" in s


def test_apps_point_at_the_oracle_address():
    url = "https://option-intelligence.duckdns.org"
    assert url in (ROOT / "mobile" / "capacitor.config.json").read_text(encoding="utf-8")
    assert url in (ROOT.parent / ".github" / "workflows" / "android-apk.yml").read_text(encoding="utf-8")
    assert "onrender.com" not in (ROOT / "desktop" / "app-url.json").read_text(encoding="utf-8")


def test_signal_log_kept_out_of_the_code_folder_on_the_server():
    s = (ROOT.parent / "oracle-setup.sh").read_text(encoding="utf-8")
    assert 'SIGNAL_LOG_DB=$HOME/oi-data/signal_log.sqlite3' in s
    assert s.count('checkout -- .') >= 2
    assert "*.sqlite3" in (ROOT / ".gitignore").read_text(encoding="utf-8")
