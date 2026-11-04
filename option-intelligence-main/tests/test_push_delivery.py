"""Web Push delivery rules (v1.18.11). Run: python tests/test_push_delivery.py
main.py needs FastAPI, so _send_web_push / _push_summary are cut out of main.py and run against a fake pywebpush + fake accounts."""
import asyncio, json, logging, os, sys, types
from pathlib import Path
SRC = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def cut(start, end):
    a = SRC.index(start); b = SRC.index(end, a)
    return SRC[a:b]


class Resp:  # what pywebpush's exception carries
    def __init__(self, c): self.status_code = c
class PushErr(Exception):
    def __init__(self, c): super().__init__(f"push service said {c}"); self.response = Resp(c)


PREFS = {}
def load_helpers(ns):
    ns.update({"time": __import__("time"), "asyncio": asyncio, "acc": types.SimpleNamespace(meta_get=lambda k: PREFS.get(k), vapid_keys=ns["acc"].vapid_keys if "acc" in ns else None)})
    exec(cut("PUSH_PREF_MODES =", "async def _send_native_fcm"), ns)


def run_send(rows, behaviour, owner=0, event="CONFIRMED"):
    calls, deleted = [], []
    fake = types.ModuleType("pywebpush")
    def webpush(**kw):
        calls.append(kw)
        r = behaviour[json.loads(kw["subscription_info"]["endpoint"] if False else json.dumps(kw["subscription_info"]))["endpoint"]]
        if r != 200: raise (r if isinstance(r, Exception) else PushErr(r))
    fake.webpush = webpush
    sys.modules["pywebpush"] = fake
    acc = types.SimpleNamespace(vapid_keys=lambda: ("PUB", "PRIV"), push_devices=lambda kind, uid=0: rows, push_delete_device=lambda i: deleted.append(i))
    ns = {"asyncio": asyncio, "json": json, "os": os, "acc": acc, "logger": logging.getLogger("t"), "_push_owner_id": lambda: owner}
    load_helpers(ns); ns["acc"] = acc; ns["acc"].meta_get = lambda k: PREFS.get(k)
    exec(cut("async def _send_web_push(", "async def _dispatch_confirmed_push"), ns)
    out = asyncio.run(ns["_send_web_push"]("T", "B", {"event": event}))
    return out, calls, deleted


def dev(i, ep): return {"id": i, "user_id": 1, "endpoint": ep, "payload": json.dumps({"endpoint": ep, "keys": {"p256dh": "x", "auth": "y"}})}


def test_sends_with_ttl_and_urgency_and_heals_bad_registrations():
    rows = [dev(1, "ok"), dev(2, "gone"), dev(3, "wrongkey"), dev(4, "flaky"), dev(5, "badreq")]
    out, calls, deleted = run_send(rows, {"ok": 200, "gone": 410, "wrongkey": 403, "flaky": TimeoutError("slow"), "badreq": 400})
    assert out["devices"] == 5 and out["sent"] == 1 and out["dropped"] == 2 and out["failed"] == 2, out
    assert sorted(deleted) == [2, 3]                                  # v1.18.12: a 400 no longer deletes the registration                                  # 410 and 403 registrations are removed, the timeout one is kept
    assert all(c["ttl"] == 600 and c["headers"] == {"Urgency": "high"} for c in calls)      # TTL 0 would drop the push for a sleeping phone
    assert all(c["vapid_claims"]["sub"].startswith("mailto:") for c in calls)


def test_notification_type_filters_devices_per_account():
    rows = [{**dev(1, "a"), "user_id": 1}, {**dev(2, "b"), "user_id": 2}]
    ok = {"a": 200, "b": 200}
    PREFS.clear(); PREFS["push_pref:2"] = "confirmed"
    for ev, expect in (("STARTED", 1), ("FAILED", 1), ("CHANGED", 1), ("CONFIRMED", 2), ("TEST", 2), ("SYSTEM", 2)):
        PREFS_CACHE_RESET()
        out, calls, _ = run_send(rows, ok, event=ev)
        assert out["sent"] == expect, (ev, out)
    PREFS.clear()


def PREFS_CACHE_RESET():
    pass


def test_signal_change_event_rules():
    ns = {"time": __import__("time")}
    exec(cut("_LAST_FINAL = {}", "async def _send_signal_alert(event,"), ns)
    f = ns["_signal_change_event"]; k = ("NIFTY", 5, "scalp")
    L = lambda level, side="CE": {"level": level, "side": side}
    assert f(k, L("WATCH"), True, 1000) is None                     # first reading after a restart is never a change
    assert f(k, L("WATCH"), True, 1001) is None                     # same state
    assert f(k, L("CONFLICT"), True, 1002) == "CHANGED"
    assert f(k, L("WATCH"), True, 1003) == "CHANGED"
    assert f(k, L("CONFLICT"), True, 1004) is None                  # flap guard: same state pushed < 120 s ago
    assert f(k, L("WAIT", None), True, 1200) is None                # WAIT never pushes
    assert f(k, L("PENDING"), True, 1201) is None and f(k, L("WATCH"), True, 1202) is None   # PENDING/CONFIRMED/FAILED have their own events
    assert f(k, L("CONFIRMED"), True, 1203) is None
    assert f(k, L("WATCH", "PE"), False, 1204) is None              # market closed


def test_no_devices_is_reported_not_hidden():
    out, calls, deleted = run_send([], {})
    assert out["configured"] and out["devices"] == 0 and out["sent"] == 0 and not calls


def test_summary_text_names_the_problem():
    ns = {}
    exec(cut("def _push_summary(", '@app.post("/api/alerts/test")'), ns)
    s = ns["_push_summary"]
    assert "NO DEVICE REGISTERED" in s([], [{"configured": True, "devices": 0, "sent": 0}, {"configured": False, "sent": 0}])
    assert "delivered to 1 of 2" in s([], [{"configured": True, "devices": 2, "sent": 1}])
    assert "FAILED" in s([], [{"configured": True, "devices": 1, "sent": 0, "dropped": 1, "errors": ["HTTP 403"]}]) and "re-register" in s([], [{"configured": True, "devices": 1, "sent": 0, "dropped": 1, "errors": ["HTTP 403"]}])
    assert "telegram: FAILED" in s([{"channel": "telegram", "ok": False, "error": "bad token"}], [])


def test_main_wires_monitor_and_status():
    for needle in ('"/api/push/status"', "_report_monitor_gap", "_spawn(_send_signal_alert", "monitor_heartbeat", "_upstox_client()", "compresslevel=3", "_watch_list(", "ALERT_FOLLOW_TERMINAL", '"last_record"', "owner_native", "/api/push/prefs", "_signal_change_event("):
        assert needle in SRC, needle


if __name__ == "__main__":
    test_sends_with_ttl_and_urgency_and_heals_bad_registrations(); test_no_devices_is_reported_not_hidden()
    test_notification_type_filters_devices_per_account(); test_signal_change_event_rules()
    test_summary_text_names_the_problem(); test_main_wires_monitor_and_status(); print("push delivery tests passed")
