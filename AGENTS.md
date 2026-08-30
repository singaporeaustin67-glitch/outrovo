# Outrovo — project memory

## What this is
Outrovo is an AI people-search agent (modeled on lessie.ai's main feature).
Stack: FastAPI backend (`app/`) + single-page frontend (`static/index.html`).
Run: `python3 -m uvicorn app.main:app --host 0.0.0.0 --port 12000` (uvicorn binary
is NOT on PATH — always use `python3 -m uvicorn`).

## Key facts learned
- LLM: OpenAI-compatible via env config (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`,
  `LLM_FALLBACK_MODELS` in `.env`, gitignored). User's Groq key is a shared/free key:
  `openai/gpt-oss-120b` has only ~2000 tokens/day (usually exhausted) — default model
  is `qwen/qwen3.8-27b` with failover to `qwen/qwen3.6-27b`, `openai/gpt-oss-20b`.
  `llm.chat()` retries TPM 429s with backoff and fails over on TPD 429s.
- `groq/compound` (built-in web search) is too rate-limited on this key to be usable.
- DuckDuckGo HTML endpoint bot-blocks this datacenter IP (202 challenge); Bing serves
  a sanitized SERP that ignores `site:` filters. Do NOT rely on web scraping for search.
- Real data sources that work reliably from this environment:
  Wikidata SPARQL (occupation + country + social handles P2003/P2002/P2397/P7085),
  Wikipedia API, GitHub API (use GITHUB_TOKEN env for higher limits), HN Algolia + Firebase API.
- Wikidata SPARQL: avoid `wdt:P279*` subclass paths (timeouts); query per-occupation
  QID with `ORDER BY DESC(?sitelinks)`; sanity-check resolved occupation labels.
- User requirement: NO seed/mock data — all results must come from live sources.
