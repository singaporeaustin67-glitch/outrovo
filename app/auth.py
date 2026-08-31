"""User accounts, sessions, and tier quotas.

Stdlib-only: PBKDF2 password hashing, random bearer tokens kept in SQLite.
Tier quotas are per UTC day; 'pro' is granted by the Stripe webhook.
"""

import hashlib
import hmac
import re
import secrets
import time

from . import cache

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSION_TTL = 30 * 86400  # 30 days

QUOTAS = {
    "free": {"searches_per_day": 5, "sends_per_day": 3},
    "pro": {"searches_per_day": 100, "sends_per_day": 50},
}


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${digest}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return hmac.compare_digest(check, digest)


def _public_user(row: dict) -> dict:
    usage = cache.usage_today(row["id"])
    return {
        "id": row["id"],
        "email": row["email"],
        "tier": row["tier"],
        "quota": {
            "searches": {"used": usage["searches_used"], "limit": QUOTAS[row["tier"]]["searches_per_day"]},
            "sends": {"used": usage["sends_used"], "limit": QUOTAS[row["tier"]]["sends_per_day"]},
        },
    }


def signup(email: str, password: str) -> dict:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("invalid email address")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if cache.get_user_by_email(email):
        raise ValueError("an account with this email already exists")
    user_id = cache.create_user(email, _hash_password(password))
    token = _create_session(user_id)
    user = cache.get_user_by_id(user_id)
    return {"token": token, "user": _public_user(user)}


def login(email: str, password: str) -> dict:
    user = cache.get_user_by_email(email.strip().lower())
    if not user or not _verify_password(password, user["password_hash"]):
        raise ValueError("invalid email or password")
    token = _create_session(user["id"])
    return {"token": token, "user": _public_user(user)}


def _create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    cache.create_session(token, user_id, time.time() + SESSION_TTL)
    return token


def user_from_token(token: str | None) -> dict | None:
    """Resolve a bearer token to a fresh user row (with today's quota counters)."""
    if not token:
        return None
    sess = cache.get_session(token)
    if not sess or sess["expires_at"] < time.time():
        return None
    cache.touch_session(token)
    user = cache.get_user_by_id(sess["user_id"])
    if not user:
        return None
    return {**user, **cache.usage_today(user["id"])}


def logout(token: str) -> None:
    cache.delete_session(token)


def check_quota(user: dict, kind: str) -> str | None:
    """Return an error message if the user is over their daily quota, else None."""
    limits = QUOTAS[user["tier"]]
    used_key, limit_key = f"{kind}_used", f"{kind}_per_day"
    if user[used_key] >= limits[limit_key]:
        return (
            f"Daily {kind} limit reached ({limits[limit_key]}/day on the {user['tier']} plan). "
            "Upgrade to Pro for a higher limit."
        )
    return None


def record_usage(user_id: int, kind: str) -> None:
    cache.record_usage(user_id, kind)
