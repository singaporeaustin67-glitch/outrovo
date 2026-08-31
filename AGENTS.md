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

## Outreach + feedback loop (added iter 3)
- `app/outreach.py`: SMTP sending via stdlib smtplib (STARTTLS negotiated via EHLO,
  so local plaintext relays work too). Config: SMTP_HOST/PORT/USER/PASS/FROM_EMAIL.
- SQLite tables in cache.py: outreach_log, followups (3-day auto-proposal, never
  auto-sent), feedback. `POST /api/feedback` votes adjust ranker prescore (+3/vote)
  and final fit_score (+5/vote, clamped 0-100).
- Reddit JSON API 403s datacenter IPs; public SearXNG instances 429 — don't bother.

## Iter 9: keyless X data + SearXNG findings
- api.fxtwitter.com/{handle} — keyless real X user data (followers, tweets, avatar).
  Used by enrich_x_stats() for candidates that surfaced an X handle. Rate-limits
  after several rapid calls; send browser UA, tolerate non-JSON bodies.
- Public SearXNG JSON API is effectively dead from datacenter IPs: 12 instances
  probed, all 429 or JSON disabled (text/html). search_websearch() falls back to
  concurrent SearXNG attempts when TAVILY_API_KEY is unset (best-effort, bounded
  latency), but expect empty — Tavily remains the only working websearch.

## Iter 10: email open tracking
- Outreach emails are now multipart/alternative (plain text + HTML part with a
  1x1 GIF pixel keyed to the outreach_log id). Pixel URL built from
  config.PUBLIC_BASE_URL (auto: RENDER_EXTERNAL_URL on Render; empty locally =
  no pixel, plain-text only).
- GET /api/track/open/{log_id}.gif records first open + count; GET
  /api/outreach/log lists sends with opened_at/opens. Stats report messages_opened.
- Log row is written BEFORE send (needed for the pixel id) and rolled back on
  SMTP failure via cache.delete_outreach_log().

## Iter 11: commercial v1 (accounts + billing + multi-tenancy)
- `app/auth.py`: PBKDF2-hashed passwords, 30-day bearer tokens in `sessions` table.
  `user_from_token()` merges the user row with today's usage counters.
  QUOTAS: free 5 searches + 3 sends/day (UTC), pro 100/50. `_public_user()` fetches
  usage itself — callers must NOT pre-merge usage into the row (that was a bug).
- `app/billing.py`: Stripe via raw httpx REST (no stripe pkg). Webhook signature =
  HMAC-SHA256("{t}.{payload}") with 5-min replay window. checkout.session.completed
  reads `client_reference_id` = user id → set_user_tier('pro'); subscription
  deleted/canceled downgrades via stripe_customer_id. Disabled when keys unset.
- Multi-tenancy: user_id columns on history/outreach_log/followups/feedback (ALTER
  TABLE guarded by OperationalError for idempotency). NULL = legacy anonymous rows,
  visible only when NOT logged in. mark_followup_sent takes user_id (IDOR fix).
  feedback_scores(user_id=...) — votes are a personal signal, never cross-user.
- Route protection in main.py via FastAPI Depends: require_user (401) for
  outreach/emails/feedback/refine/followups/log; require_search_access allows
  anonymous search unless config.AUTH_REQUIRED. Quota 429s before spending LLM tokens;
  usage recorded only when ranked results exist.
- Frontend: authToken in localStorage("outrovo_token"), api() helper injects
  Authorization header, 401 → opens signup modal. ?upgraded=1 return from Stripe
  shows welcome + refreshes quota. Legal pages at /privacy and /terms.
- Still needed before charging real money: set STRIPE_* env vars on Render, create
  the Pro price in Stripe dashboard, point the webhook at /api/billing/webhook.
  Per-user SMTP (currently one global account) is the next real gap.
