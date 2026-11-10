"""v1.19.2: no phone alerts while NSE is closed (nights, weekends, holidays). Needs the FastAPI stubs only when main is importable."""
import os, sys, asyncio
from datetime import datetime, time as dtime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import main as M
except Exception:          # FastAPI not installed in this environment
    M = None


def test_session_calendar_and_holiday():
    if M is None:
        return
    IST = M.IST
    wed = datetime(2026, 11, 4, 19, 10, tzinfo=IST)
    assert not M.in_session(wed)                                           # evening
    assert M.in_session(datetime(2026, 11, 4, 11, 0, tzinfo=IST))
    assert not M.in_session(datetime(2026, 11, 7, 11, 0, tzinfo=IST))     # Saturday
    assert M.in_session(datetime(2026, 11, 4, 15, 40, tzinfo=IST), grace_minutes=15)
    M._TRADING_DAY["2026-11-10"] = {"open_day": False, "start": None, "end": None, "source": "test", "note": "holiday"}
    assert not M.in_session(datetime(2026, 11, 10, 11, 0, tzinfo=IST))   # a Tuesday holiday
    M._TRADING_DAY["2026-11-08"] = {"open_day": True, "start": dtime(18, 0), "end": dtime(19, 0), "source": "test", "note": "muhurat"}
    assert M.in_session(datetime(2026, 11, 8, 18, 30, tzinfo=IST))       # special evening session on a Sunday


def test_exit_after_close_goes_to_history_only():
    if M is None:
        return
    sent, hist = [], []
    async def fake_send(*a, **k): sent.append(a); return []
    async def fake_push(*a, **k): sent.append(a); return []
    M.notifier.send_alert, M._dispatch_confirmed_push = fake_send, fake_push
    M.acc.push_recipient_ids = lambda *a: [1]
    M.acc.push_add_history = lambda *a: hist.append(a)
    row = {"option_name": "NIFTY 23850 CE", "side": "CE", "entry": 23960, "option_entry": 185.5, "qty": 65, "charges_rs": 60}
    c = {"status": "STOP", "net": -19.0, "gross": -15.0, "exit_price": 23945, "exit_time": "2026-11-04T11:02:00+05:30"}
    asyncio.run(M._send_exit_alert("NIFTY", row, c, 179.0, -482.5, push=False))
    assert hist and not sent
    asyncio.run(M._send_exit_alert("NIFTY", row, c, 179.0, -482.5, push=True))
    assert len(sent) == 2
