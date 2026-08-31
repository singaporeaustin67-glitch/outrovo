import os

from dotenv import load_dotenv

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3.8-27b")
LLM_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("LLM_FALLBACK_MODELS", "qwen/qwen3.6-27b,openai/gpt-oss-20b,openai/gpt-oss-120b").split(",")
    if m.strip()
]
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
OPENCORPORATES_TOKEN = os.getenv("OPENCORPORATES_TOKEN", "")  # free with registration
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")  # free tier: https://tavily.com

# Outreach sending via any SMTP account (free: Gmail app password, Resend SMTP free tier)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
FROM_NAME = os.getenv("FROM_NAME", "Outrovo")
# Public URL of this app, used to build open-tracking pixel URLs in sent emails.
# Render sets RENDER_EXTERNAL_URL automatically; locally this stays empty (no pixel).
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")

# Billing via Stripe (optional — app runs free-tier-only without these)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")  # recurring price, e.g. price_123
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Set AUTH_REQUIRED=1 to force login before searching (commercial mode).
# Default off: visitors can try search anonymously; outreach always needs login.
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "").lower() in ("1", "true", "yes")
PRODUCTHUNT_API_KEY = os.getenv("PRODUCTHUNT_API_KEY", "")  # free: producthunt.com/v2/oauth/applications
PRODUCTHUNT_API_SECRET = os.getenv("PRODUCTHUNT_API_SECRET", "")

HTTP_TIMEOUT = 20.0
USER_AGENT = "Outrovo/1.0 (https://outrovo.ai; people-search-agent)"
