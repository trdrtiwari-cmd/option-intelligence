"""Signal alerts (v1.14). Every channel is optional and switched on by environment variables; nothing is sent unless set.

Render's FREE web services block outbound SMTP (ports 25/465/587), so the default channels all use HTTPS (port 443):
  * Telegram bot   TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID           - instant phone push, free, 2-minute setup
  * ntfy           NTFY_TOPIC (+ optional NTFY_SERVER)             - install the ntfy app, subscribe to your topic, no account
  * Email (HTTPS)  RESEND_API_KEY + ALERT_EMAIL_TO (+ ALERT_EMAIL_FROM)  - Resend's HTTP API, free tier
  * Email (SMTP)   SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO  - works locally or on a paid Render instance
Failures never raise: an alert problem must not break the terminal.
"""
import asyncio
import logging
import os
import smtplib
import time
from email.message import EmailMessage

import httpx

logger = logging.getLogger("option_intelligence.notifier")
_LAST = {}          # title -> time of last send: identical alerts closer than 20 s apart are dropped


def _env(name, default=""):
    return os.getenv(name, default).strip()


def events():
    """Which reconfirmation events send a message. STARTED = setup detected (heads-up), CONFIRMED = reconfirmed entry, FAILED = did not hold."""
    raw = _env("ALERT_EVENTS", "STARTED,CONFIRMED")
    return {e.strip().upper() for e in raw.split(",") if e.strip()}


def channels():
    return {
        "telegram": bool(_env("TELEGRAM_BOT_TOKEN") and _env("TELEGRAM_CHAT_ID")),
        "ntfy": bool(_env("NTFY_TOPIC")),
        "email_api": bool(_env("RESEND_API_KEY") and _env("ALERT_EMAIL_TO")),
        "email_smtp": bool(_env("SMTP_HOST") and _env("ALERT_EMAIL_TO") and _env("SMTP_USER")),
    }


def any_configured():
    return any(channels().values())


async def _telegram(title, body):
    url = f"https://api.telegram.org/bot{_env('TELEGRAM_BOT_TOKEN')}/sendMessage"
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.post(url, json={"chat_id": _env("TELEGRAM_CHAT_ID"), "text": f"{title}\n{body}"})
    r.raise_for_status()


async def _ntfy(title, body, priority):
    server = (_env("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {"Title": title.encode("ascii", "ignore").decode() or "Option Intelligence", "Priority": {"high": "high", "urgent": "urgent"}.get(priority, "default"),
               "Tags": "bell"}
    if _env("NTFY_TOKEN"):
        headers["Authorization"] = "Bearer " + _env("NTFY_TOKEN")
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.post(f"{server}/{_env('NTFY_TOPIC')}", content=body.encode("utf-8"), headers=headers)
    r.raise_for_status()


async def _email_api(title, body):
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post("https://api.resend.com/emails", headers={"Authorization": "Bearer " + _env("RESEND_API_KEY")},
                         json={"from": _env("ALERT_EMAIL_FROM") or "Option Intelligence <onboarding@resend.dev>",
                               "to": [x.strip() for x in _env("ALERT_EMAIL_TO").split(",") if x.strip()], "subject": title, "text": body})
    r.raise_for_status()


def _smtp_send(title, body):
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = title, _env("ALERT_EMAIL_FROM") or _env("SMTP_USER"), _env("ALERT_EMAIL_TO")
    msg.set_content(body)
    port = int(_env("SMTP_PORT") or 587)
    if port == 465:
        with smtplib.SMTP_SSL(_env("SMTP_HOST"), port, timeout=10) as s:
            s.login(_env("SMTP_USER"), _env("SMTP_PASS"))
            s.send_message(msg)
    else:
        with smtplib.SMTP(_env("SMTP_HOST"), port, timeout=10) as s:
            s.starttls()
            s.login(_env("SMTP_USER"), _env("SMTP_PASS"))
            s.send_message(msg)


async def send_alert(title, body, priority="default", force=False):
    """Send to every configured channel in parallel. Returns [{channel, ok, error}]. Never raises."""
    now = time.time()
    if not force and now - _LAST.get(title, 0) < 20:
        return [{"channel": "all", "ok": False, "error": "duplicate alert suppressed (same title within 20 s)"}]
    _LAST[title] = now
    cfg = channels()
    jobs = {}
    if cfg["telegram"]:
        jobs["telegram"] = _telegram(title, body)
    if cfg["ntfy"]:
        jobs["ntfy"] = _ntfy(title, body, priority)
    if cfg["email_api"]:
        jobs["email_api"] = _email_api(title, body)
    if cfg["email_smtp"]:
        jobs["email_smtp"] = asyncio.to_thread(_smtp_send, title, body)
    if not jobs:
        return []
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    out = []
    for name, res in zip(jobs, results):
        if isinstance(res, Exception):
            logger.warning("Alert channel %s failed: %s", name, str(res)[:200])
            out.append({"channel": name, "ok": False, "error": f"{type(res).__name__}: {str(res)[:160]}"})
        else:
            out.append({"channel": name, "ok": True, "error": None})
    return out


def format_event(event, sym, interval, mode, side, a, rc, levels=None, now_text=""):
    """(title, body, priority) for a reconfirmation event."""
    price = a.get("last_close")
    score = a.get("score")
    lv = ""
    if levels and levels.get("entry"):
        lv = f"\nEntry ~{levels['entry']:,.0f} | SL {levels.get('stop_loss') or '-'} | Target {levels.get('target') or '-'} (index pts)"
    head = {"STARTED": f"{sym} {side} setup detected - reconfirming",
            "CONFIRMED": f"{sym} BUY {side} - CONFIRMED",
            "FAILED": f"{sym} {side} setup failed reconfirmation"}.get(event, f"{sym} {side} {event}")
    detail = {"STARTED": f"Not an entry yet. It must hold for {rc.get('window')}s without price moving {rc.get('limit_atr')} ATR against it.",
              "CONFIRMED": f"Held {rc.get('window')}s and passed every terminal gate (check the page for the S/R-room and option-volume gates before entering).",
              "FAILED": f"Did not hold: {rc.get('reason')}."}.get(event, "")
    body = f"{detail}\n{sym} {interval}m | {mode} | index {price:,.2f} | score {score}/100 | {now_text}{lv}\nAnalysis only - not an order."
    return head, body, ("high" if event == "CONFIRMED" else "default")
