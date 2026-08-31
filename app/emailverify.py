"""Email verification: syntax + disposable-domain + MX record check.

Deliberately avoids SMTP RCPT probing — that is unreliable from datacenter
IPs (most mail servers tarpit or accept-then-bounce) and can get the sender
flagged. DNS MX lookup is fast, free, and catches dead domains.
"""

import re
import socket

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")

# Common disposable/temporary email providers — never worth sending to.
_DISPOSABLE = {
    "mailinator.com", "tempmail.com", "10minutemail.com", "guerrillamail.com",
    "yopmail.com", "throwaway.email", "temp-mail.org", "fakeinbox.com",
    "sharklasers.com", "getnada.com", "maildrop.cc", "trashmail.com",
}


def _has_mx(domain: str) -> bool:
    """True if the domain has an MX or A record (i.e. can plausibly receive mail)."""
    try:
        import dns.resolver  # type: ignore
        try:
            return bool(dns.resolver.resolve(domain, "MX"))
        except Exception:
            return bool(dns.resolver.resolve(domain, "A"))
    except ImportError:
        pass
    # Fallback without dnspython: resolve the hostname (A record check only).
    try:
        socket.getaddrinfo(domain, 25, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False


def verify_email(email: str) -> dict:
    """Classify an address. Returns {status, reason}; status is one of
    valid / risky / invalid."""
    email = (email or "").strip().lower()
    m = EMAIL_RE.match(email)
    if not m:
        return {"status": "invalid", "reason": "malformed address"}
    domain = m.group(1)
    if domain in _DISPOSABLE:
        return {"status": "risky", "reason": "disposable email provider"}
    if not _has_mx(domain):
        return {"status": "invalid", "reason": "domain has no mail server"}
    return {"status": "valid", "reason": f"{domain} accepts mail"}


def verify_all(emails: list, key: str | None = None) -> list[dict]:
    """Verify a list of address strings, or dicts containing an address under `key`.
    Dict entries are returned with status/reason merged in (source info preserved)."""
    out = []
    for e in emails:
        addr = e[key] if key else e
        v = verify_email(addr)
        if key:
            out.append({**e, **v})
        else:
            out.append({"email": addr, **v})
    # Best first
    order = {"valid": 0, "risky": 1, "invalid": 2}
    out.sort(key=lambda x: order[x["status"]])
    return out
