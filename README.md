# Outrovo — AI People Search Agent

Say who you need in plain language. Outrovo plans the search with an LLM, queries
**live public data sources**, reviews every profile with AI, and returns the
best-matching **real people** — with verified profile links and a personalized
outreach draft. No seed data, no static lists.

## How it works

```
query ──▶ planner (LLM)      ──▶ structured search plan (sources, occupations, location)
      ──▶ connectors         ──▶ real candidates from live sources:
                                  · Wikidata SPARQL — notable people + verified IG/X/YouTube/TikTok handles
                                  · Wikipedia API — notable people with bios
                                  · GitHub API — developers (location, followers, repos)
                                  · Hacker News (Algolia + official API) — tech founders/builders
      ──▶ ranker (LLM)       ──▶ fit score 0–100 + honest review + evidence highlights
      ──▶ outreach (LLM)     ──▶ personalized first-contact message on demand
```

## Run

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your LLM key
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 12000
```

Open http://localhost:12000

## Configuration (`.env`)

The LLM layer is provider-agnostic (any OpenAI-compatible endpoint):

| Variable | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | swap to change providers |
| `LLM_API_KEY` | — | your provider key |
| `LLM_MODEL` | `qwen/qwen3.8-27b` | primary model |
| `LLM_FALLBACK_MODELS` | `qwen/qwen3.6-27b,openai/gpt-oss-20b,openai/gpt-oss-120b` | used on daily-quota exhaustion |
| `GITHUB_TOKEN` | — | optional, raises GitHub rate limits |

Per-minute rate limits are retried with backoff; daily-quota 429s fail over to
the next model automatically.

## API

- `POST /api/search` `{ "query": "..." }` → plan + ranked real people
- `POST /api/outreach` `{ "query": "...", "candidate": {...} }` → outreach draft
- `GET /api/health` → status + active model
