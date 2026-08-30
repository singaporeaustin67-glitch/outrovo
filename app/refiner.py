"""Conversational refinement: filter the existing result set in place."""

import json

from . import llm

REFINER_PROMPT = """You are the refinement brain of Outrovo, an AI people-search agent.
The user already has a table of people found for their search. They now type a
follow-up instruction to refine THAT list (e.g. "only Taiwan", "drop the ones
under 100K followers", "just the founders", "sort by avg views").

Original search: {query}

Candidates (JSON, each has id/name/headline/company/role/country_code/stats):
{candidates}

User instruction: {instruction}

Return ONLY a JSON object:
{{
  "keep_ids": ["ids of candidates that satisfy the instruction — keep ALL if the instruction is a sort or asks a question"],
  "sort": "one of: fit_score, followers, avg_views, name — or empty string",
  "sort_dir": "desc or asc (empty string if no sort)",
  "reply": "one short sentence telling the user what you did, e.g. 'Filtered to 4 people in Taiwan.'"
}}

Rules:
- Never invent ids; only use ids from the candidate list.
- If nothing matches, keep_ids may be empty and reply explains that.
- "only X" means filter to X; "remove/drop X" means filter X out.
"""


def _slim(candidate: dict) -> dict:
    stats = candidate.get("stats", {}) or {}
    return {
        "id": candidate.get("id"),
        "name": candidate.get("name"),
        "fit_score": candidate.get("fit_score"),
        "headline": (candidate.get("headline") or "")[:150],
        "company": candidate.get("company"),
        "role": candidate.get("role"),
        "country_code": candidate.get("country_code"),
        "stats": {
            "followers": stats.get("followers") or stats.get("social_followers"),
            "avg_views": stats.get("avg_views"),
        },
    }


async def refine(query: str, instruction: str, candidates: list[dict]) -> dict:
    slim = [_slim(c) for c in candidates[:30]]
    prompt = REFINER_PROMPT.format(
        query=query,
        instruction=instruction,
        candidates=json.dumps(slim, ensure_ascii=False),
    )
    text = await llm.chat([{"role": "user", "content": prompt}], max_tokens=2048)
    parsed = llm.extract_json(text) or {}
    keep = set(parsed.get("keep_ids") or [c["id"] for c in slim])
    kept = [c for c in candidates if c.get("id") in keep]
    sort_key = parsed.get("sort") or ""
    if sort_key in ("followers", "avg_views"):
        kept.sort(key=lambda c: (c.get("stats") or {}).get(sort_key) or 0,
                  reverse=parsed.get("sort_dir") != "asc")
    elif sort_key == "name":
        kept.sort(key=lambda c: (c.get("name") or "").lower(),
                  reverse=parsed.get("sort_dir") == "desc")
    return {
        "results": kept,
        "reply": (parsed.get("reply") or "Done.").strip(),
    }
