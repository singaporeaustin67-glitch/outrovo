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
PRODUCTHUNT_API_KEY = os.getenv("PRODUCTHUNT_API_KEY", "")  # free: producthunt.com/v2/oauth/applications
PRODUCTHUNT_API_SECRET = os.getenv("PRODUCTHUNT_API_SECRET", "")

HTTP_TIMEOUT = 20.0
USER_AGENT = "Outrovo/1.0 (https://outrovo.ai; people-search-agent)"
