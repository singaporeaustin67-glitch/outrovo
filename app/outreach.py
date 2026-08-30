"""Outreach sending, follow-up scheduling, and feedback loop.

Sending uses any standard SMTP account (Gmail app password, your own domain,
Resend SMTP free tier, ...) configured via env vars — no paid API required.
Nothing is ever sent automatically: every send is an explicit API call, and
follow-ups are only *proposed* (listed when due), never auto-sent.
"""

import asyncio
import smtplib
import ssl
import time
from email.message import EmailMessage

from . import cache, config

FOLLOW_UP_DAYS = 3


def sending_configured() -> bool:
    return bool(config.SMTP_HOST)


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = f"{config.FROM_NAME} <{config.FROM_EMAIL or config.SMTP_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.ehlo()
        if smtp.has_extn("starttls"):  # local/plaintext relays simply skip TLS
            smtp.starttls(context=ctx)
            smtp.ehlo()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASS)
        smtp.send_message(msg)


async def send_outreach(candidate: dict, to: str, subject: str, body: str) -> dict:
    """Send one message via SMTP and log it + schedule a follow-up proposal."""
    if not sending_configured():
        raise RuntimeError("SMTP not configured (set SMTP_HOST/USER/PASS in .env)")
    await asyncio.to_thread(_send_sync, to, subject, body)
    log_id = cache.log_outreach(candidate.get("id", ""), to, subject, body)
    followup_id = cache.schedule_followup(
        candidate.get("id", ""), to, subject, body, time.time() + FOLLOW_UP_DAYS * 86400
    )
    return {"sent": True, "log_id": log_id, "followup_id": followup_id,
            "followup_due_in_days": FOLLOW_UP_DAYS}


def due_followups() -> list[dict]:
    return cache.due_followups(time.time())
