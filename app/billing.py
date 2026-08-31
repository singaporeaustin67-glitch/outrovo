"""Stripe billing: checkout sessions + webhook tier upgrades.

Uses Stripe's REST API directly via httpx — no stripe package needed.
Without keys configured, billing is disabled and signup still works (free tier).
"""

import hashlib
import hmac
import time

import httpx

from . import cache, config

STRIPE_API = "https://api.stripe.com/v1"


def billing_configured() -> bool:
    return bool(config.STRIPE_SECRET_KEY and config.STRIPE_PRICE_ID and config.PUBLIC_BASE_URL)


async def create_checkout_session(user: dict) -> str:
    """Return the Stripe Checkout URL for upgrading this user to Pro."""
    if not billing_configured():
        raise RuntimeError("billing not configured (set STRIPE_SECRET_KEY / STRIPE_PRICE_ID)")
    payload = {
        "mode": "subscription",
        "success_url": f"{config.PUBLIC_BASE_URL}/?upgraded=1",
        "cancel_url": f"{config.PUBLIC_BASE_URL}/?upgrade_cancelled=1",
        "client_reference_id": str(user["id"]),
        "customer_email": user["email"],
        "line_items[0][price]": config.STRIPE_PRICE_ID,
        "line_items[0][quantity]": "1",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{STRIPE_API}/checkout/sessions",
            data=payload,
            auth=(config.STRIPE_SECRET_KEY, ""),
        )
    if r.status_code != 200:
        raise RuntimeError(f"Stripe error: {r.text[:200]}")
    return r.json()["url"]


def verify_webhook_signature(payload: bytes, header: str) -> bool:
    """Validate Stripe-Signature: HMAC-SHA256 of '{timestamp}.{payload}'."""
    if not config.STRIPE_WEBHOOK_SECRET:
        return False
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        timestamp, signature = parts["t"], parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs(time.time() - int(timestamp)) > 300:  # replay protection: 5 min window
        return False
    expected = hmac.new(
        config.STRIPE_WEBHOOK_SECRET.encode(),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_event(event: dict) -> str:
    """Process a verified Stripe event; returns a short description of the action."""
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    if etype == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        if user_id and str(user_id).isdigit():
            cache.set_user_tier(int(user_id), "pro", stripe_customer_id=obj.get("customer", "") or "")
            return f"user {user_id} upgraded to pro"
    elif etype in ("customer.subscription.deleted", "customer.subscription.updated"):
        status = obj.get("status", "")
        customer_id = obj.get("customer", "")
        if customer_id and status in ("canceled", "unpaid", "incomplete_expired"):
            _downgrade_by_customer(customer_id)
            return f"customer {customer_id} downgraded to free ({status})"
    return f"ignored {etype or 'unknown event'}"


def _downgrade_by_customer(customer_id: str) -> None:
    conn = cache._conn()
    row = conn.execute("SELECT id FROM users WHERE stripe_customer_id = ?", (customer_id,)).fetchone()
    conn.close()
    if row:
        cache.set_user_tier(row[0], "free")
