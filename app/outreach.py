"""Outreach sending, follow-up scheduling, and feedback loop.

Sending uses any standard SMTP account (Gmail app password, your own domain,
Resend SMTP free tier, ...) configured via env vars — no paid API required.
Nothing is ever sent automatically: every send is an explicit API call, and
follow-ups are only *proposed* (listed when due), never auto-sent.
"""

import asyncio
import html
import smtplib
import ssl
import time
from email.message import EmailMessage

from . import cache, config

FOLLOW_UP_DAYS = 3


def sending_configured() -> bool:
    return bool(config.SMTP_HOST)


def _pixel_url(log_id: int) -> str:
    if not config.PUBLIC_BASE_URL:
        return ""
    return f"{config.PUBLIC_BASE_URL}/api/track/open/{log_id}.gif"


def _send_sync(to: str, subject: str, body: str, pixel_url: str = "") -> None:
    msg = EmailMessage()
    msg["From"] = f"{config.FROM_NAME} <{config.FROM_EMAIL or config.SMTP_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if pixel_url:
        # multipart/alternative: plain text stays primary, HTML part carries the
        # invisible 1x1 open-tracking pixel. Clients with images off send nothing.
        html_body = (
            f"<html><body>{html.escape(body).replace(chr(10), '<br>')}"
            f'<img src="{pixel_url}" width="1" height="1" alt="" '
            f'style="display:none;border:0"></body></html>'
        )
        msg.add_alternative(html_body, subtype="html")
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
    # Log first so the pixel URL can be keyed to the log id; roll back on failure.
    log_id = cache.log_outreach(candidate.get("id", ""), to, subject, body)
    try:
        await asyncio.to_thread(_send_sync, to, subject, body, _pixel_url(log_id))
    except Exception:
        cache.delete_outreach_log(log_id)
        raise
    followup_id = cache.schedule_followup(
        candidate.get("id", ""), to, subject, body, time.time() + FOLLOW_UP_DAYS * 86400
    )
    return {"sent": True, "log_id": log_id, "followup_id": followup_id,
            "tracked": bool(config.PUBLIC_BASE_URL),
            "followup_due_in_days": FOLLOW_UP_DAYS}


def due_followups() -> list[dict]:
    return cache.due_followups(time.time())
