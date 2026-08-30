from . import llm

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
    "fit_reason": "1-2 sentence honest assessment of why this person does or does not match",
    "highlights": ["up to 3 concrete evidence points from the profile data"]
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


async def rank_candidates(query: str, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    # Cap the batch so the prompt stays within context limits.
    batch = candidates[:24]
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
        c["fit_score"] = int(review.get("fit_score", 0) or 0)
        c["fit_reason"] = review.get("fit_reason", "")
        c["highlights"] = review.get("highlights", [])
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
