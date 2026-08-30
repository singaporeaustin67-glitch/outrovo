from . import cache, llm

RANK_PROMPT = """You are the review brain of Outrovo, an AI people-search agent.
The user asked: "{query}"

Below are REAL candidate profiles fetched from live public sources. Review each one and decide how well it matches the request.

Candidates:
{candidates}

Return ONLY a JSON array, one object per candidate id you were given:
[
  {{
    "id": "<candidate id>",
    "fit_score": <integer 0-100>,
    "fit_reason": "1 sentence honest assessment of why this person does or does not match",
    "highlights": ["up to 2 concrete evidence points from the profile data"],
    "role": "their job title or role, e.g. 'Founding Partner', 'Software Engineer', 'Beauty Creator' — empty string if unknown",
    "company": "the company/organization they work at or founded — empty string if unknown",
    "country_code": "ISO 3166-1 alpha-2 code of their country (e.g. 'US', 'DE') — empty string if unknown"
  }}
]

Rules:
- Be strict: score below 40 for weak matches, 80+ only for strong, specific matches.
- Base every claim only on the provided profile data. Never invent facts.
- Include every candidate id exactly once.
"""


def _format_candidates(candidates: list[dict]) -> str:
    lines = []
    for c in candidates:
        stats = ", ".join(f"{k}={v}" for k, v in c.get("stats", {}).items() if v != "")
        lines.append(
            f"- id: {c['id']}\n"
            f"  name: {c['name']}\n"
            f"  source: {c['source']}\n"
            f"  headline: {c.get('headline', '')}\n"
            f"  location: {c.get('location', '')}\n"
            f"  bio: {c.get('bio', '')[:300]}\n"
            f"  platforms: {', '.join(c.get('platforms', {}).keys())}\n"
            f"  stats: {stats}"
        )
    return "\n".join(lines)


def _prescore(c: dict) -> float:
    """Cheap relevance proxy used to pick which candidates reach the LLM review
    batch: real engagement/authority signals, profile richness, and whether the
    person was confirmed by multiple independent sources."""
    stats = c.get("stats") or {}
    score = 0.0
    for key, cap in (
        ("followers", 100_000), ("social_followers", 100_000),
        ("sitelinks", 200), ("citations", 100_000),
        ("karma", 50_000), ("reputation", 500_000), ("tag_score", 5_000),
    ):
        try:
            v = float(stats.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        score += min(v / cap, 1.0)
    if c.get("avatar_url"):
        score += 0.3
    if c.get("bio"):
        score += 0.2
    srcs = c.get("sources") or [c.get("source")]
    score += 0.25 * (len([s for s in srcs if s]) - 1)
    return score


async def rank_candidates(query: str, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    # Cap the batch so the prompt stays within context limits. Keep every
    # index hit (people recalled from past searches) in the reviewed batch —
    # they were already proven relevant once, so don't let live sources crowd
    # them out of the 24-slot prompt. Live candidates compete by pre-score.
    indexed = [c for c in candidates if c.get("source") == "index"]
    # Feedback loop: explicit thumbs up/down on past results nudges who makes
    # the review batch (and, after review, the final ordering).
    votes = cache.feedback_scores(query)
    live = sorted(
        (c for c in candidates if c.get("source") != "index"),
        key=lambda c: _prescore(c) + 3 * votes.get(c.get("id", ""), 0),
        reverse=True,
    )
    batch = (indexed + live)[:24]
    text = await llm.chat(
        [{"role": "user", "content": RANK_PROMPT.format(query=query, candidates=_format_candidates(batch))}],
        temperature=0.1,
        max_tokens=4000,
    )
    reviews = llm.extract_json(text)
    if not isinstance(reviews, list):
        reviews = []
    by_id = {r.get("id"): r for r in reviews if isinstance(r, dict)}

    ranked = []
    for c in batch:
        review = by_id.get(c["id"], {})
        c["fit_score"] = max(0, min(100, int(review.get("fit_score", 0) or 0) + 5 * int(votes.get(c["id"], 0))))
        c["fit_reason"] = review.get("fit_reason", "")
        c["highlights"] = review.get("highlights", [])
        c["role"] = review.get("role", "") or c.get("stats", {}).get("occupation", "")
        company = (review.get("company", "") or c.get("stats", {}).get("company", "").lstrip("@")).strip()
        c["company"] = "" if company.lower() in ("n/a", "none", "unknown", "-") else company
        c["country_code"] = (review.get("country_code", "") or "").upper()[:2]
        c.pop("bio", None)
        ranked.append(c)
    ranked.sort(key=lambda c: c["fit_score"], reverse=True)
    return ranked


OUTREACH_PROMPT = """You are Outrovo's outreach copywriter. Write a short, personalized first-contact message from the user to this person.

User's goal: {query}

Person:
- name: {name}
- headline: {headline}
- location: {location}
- source: {source}
- platforms: {platforms}

Rules:
- 80-120 words, friendly and specific, reference something real from their profile.
- No placeholders like [Name] other than the sign-off "— The Outrovo user" is NOT allowed; end with a simple sign-off like "Best," on its own line.
- Return ONLY the message text.
"""


async def draft_outreach(query: str, candidate: dict) -> str:
    return await llm.chat(
        [
            {
                "role": "user",
                "content": OUTREACH_PROMPT.format(
                    query=query,
                    name=candidate.get("name", ""),
                    headline=candidate.get("headline", ""),
                    location=candidate.get("location", ""),
                    source=candidate.get("source", ""),
                    platforms=", ".join(candidate.get("platforms", {}).keys()),
                ),
            }
        ],
        temperature=0.6,
        max_tokens=400,
    )
