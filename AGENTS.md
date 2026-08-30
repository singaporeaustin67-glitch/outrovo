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
- Added later (all keyless, verified working): Bluesky public API
  (`public.api.bsky.app`, searchActors + getProfiles for follower counts + bio links),
  Stack Exchange API (`/2.3/tags/{tag}/top-answerers/all_time`, ~300 calls/day unauth),
  OpenAlex (`api.openalex.org`, works search → aggregate authorships → batch author
  details with `filter=openalex_id:A1|A2`; pass `mailto=` for the polite pool).
- GDELT doc API persistently 429s this datacenter IP — do not integrate.
- YouTube channel search works WITHOUT an API key: GET youtube.com/results
  ?search_query=X&sp=EgIQAg== (channels filter), parse ytInitialData JSON from the
  HTML -> channelRenderer entries (subs count is in videoCountText.simpleText in the
  new layout; handle in subscriberCountText). Verified OK from datacenter IPs.
  Channel descriptions often contain published business emails.
- GitHub API from Render's shared egress IP: unauthenticated search (10 req/min)
  is often exhausted by other tenants; stale stored tokens also silently kill the
  connector. `_gh_get` retries 403/429 and drops auth on 401.
- Wikipedia API gotcha: `cllimit` (and similar per-prop limits) is shared across ALL
  pages in a multi-title request — always use `cllimit=max` or most pages get zero
  categories and the person-filter silently drops them. Category members
  (`list=categorymembers` on categories found via `srnamespace=14` search) give far
  better people recall than full-text search alone.
- Never feed generic occupation labels ("researcher", "professor") to Wikidata/
  OpenAlex/Bluesky — unfiltered they return history's most-sitelinked humans
  (Clinton, Confucius). `connectors._GENERIC_OCCUPATIONS` filters them; OpenAlex/
  Bluesky take topic terms (role_keywords/hn_terms), not occupations.
- Wikidata P27 country filter only works for actual countries; `_REGIONS` in
  connectors.py maps continents/regions to no-filter.
- `merge_candidates()` unifies the same person across sources (shared platform URL,
  or exact name match only among notability-gated sources wikidata/wikipedia/index).
- Latency profile: plan ~1s, gather ~15s, LLM rank ~15-20s (worse when Groq TPM
  contended — 3 sequential LLM calls per search: planner, websearch-extract, ranker).
- User requirement: NO seed/mock data — all results must come from live sources.
