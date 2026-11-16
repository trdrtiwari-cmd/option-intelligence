"""v1.20.64 phone health alerts. Run: python tests/test_health_alerts.py"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_problems_and_alert_cycle():
    try:
        import main as M
    except ImportError:
        return
    k = M.INDEX_KEYS["NIFTY"]
    now = time.time()
    saved = (M.TOKEN_CHECK.get("state"), M._health_send, M.market_status, M.PUSH_DELIVERY_ENABLED, M.asyncio.sleep)
    sent = []
    try:
        M.TOKEN_CHECK["state"] = "VALID"
        M.STREAMS["NIFTY"] = {"keys": [k], "started_ts": now - 3600}
        M.LIVE_TICKS[k] = {"received_ts": now - 44000}
        probs = M.health_problems(now)
        assert [p[0] for p in probs] == ["feed-NIFTY"] and "stopped" in probs[0][1]
        M.LIVE_TICKS[k] = {"received_ts": now - 5}
        assert M.health_problems(now) == []
        M.TOKEN_CHECK["state"] = "EXPIRED"
        assert [p[0] for p in M.health_problems(now)] == ["token"]
        M.TOKEN_CHECK["state"] = "VALID"
        day = M.ist_now().strftime("%Y-%m-%d")
        M.STREAM_RESTARTS["NIFTY"] = [{"at": day + "T10:0%d:00+05:30" % i, "reason": "x"} for i in range(3)]
        assert any(p[0] == "restarts-NIFTY" for p in M.health_problems(now))
        M.STREAM_RESTARTS.pop("NIFTY", None)

        # loop: alert once, not again within the gap, then "OK again" when it clears
        async def fake_send(key, title, body):
            sent.append(title)
        M._health_send = fake_send
        M.market_status = lambda: {"open": True}
        M.PUSH_DELIVERY_ENABLED = lambda: True
        M.LIVE_TICKS[k] = {"received_ts": time.time() - 500}
        n = [0]
        real = asyncio.sleep

        async def fast(t):
            n[0] += 1
            if n[0] == 4:
                M.LIVE_TICKS[k] = {"received_ts": time.time()}
            if n[0] > 6:
                raise asyncio.CancelledError
            await real(0)
        M.asyncio.sleep = fast
        try:
            asyncio.run(M._health_loop())
        except BaseException:
            pass
        assert len(sent) == 2 and "stopped" in sent[0] and sent[1].startswith("OK again: NIFTY live prices are back"), sent
    finally:
        M.TOKEN_CHECK["state"], M._health_send, M.market_status, M.PUSH_DELIVERY_ENABLED, M.asyncio.sleep = saved
        M.STREAMS.pop("NIFTY", None); M.LIVE_TICKS.pop(k, None); M._HEALTH.clear()


if __name__ == "__main__":
    test_problems_and_alert_cycle(); print("all health-alert tests passed")
