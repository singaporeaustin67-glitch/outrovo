from . import llm

PLANNER_PROMPT = """You are the query-planning brain of Outrovo, an AI people-search agent.
Convert the user's natural-language request into a structured search plan for finding REAL people.

Available data sources:
- github: real developers/engineers (great for technical roles, open-source people)
- wikipedia: real notable people (founders, investors, researchers, authors, public figures)
- hackernews: real tech founders, builders, writers (active startup/tech community members)
- wikidata: real notable people with verified social-media handles (influencers, creators, athletes, musicians, politicians, founders, experts) — searchable by occupation and country

Return ONLY a JSON object with this shape:
{
  "intent_summary": "one sentence describing who the user wants",
  "role_keywords": ["keyword1", "keyword2"],
  "location": "city/country or empty string",
  "platforms": ["instagram", "tiktok", "youtube", "x", "linkedin", "github"],
  "sources": ["github", "wikipedia", "hackernews", "wikidata"],
  "github_query": "GitHub USER-search string using only free-text keywords plus location:/language:/followers:>N qualifiers (N <= 100), e.g. 'machine learning location:berlin followers:>50', or empty string if github not useful",
  "wiki_terms": ["2-4 short Wikipedia search terms for kinds of people"],
  "hn_terms": ["1-3 short keyword terms for Hacker News story search"],
  "occupations": ["1-4 Wikidata-style occupation labels, e.g. 'influencer', 'beauty YouTuber', 'venture capitalist', 'software engineer'"],
  "country": "country label like 'United States' or empty string"
}

Rules:
- Only include sources that can plausibly surface the requested people.
- Keep every list short and specific. No invented names.
- If the request is about social-media influencers/creators/public figures, "wikidata" must be included and occupations should be specific (e.g. 'beauty YouTuber' not just 'YouTuber' when the niche is beauty).
- "wikidata" and "wikipedia" are best for notable/public people; "github"/"hackernews" for builders and engineers.
"""


def fallback_plan(query: str) -> dict:
    return {
        "intent_summary": query,
        "role_keywords": [query],
        "location": "",
        "platforms": [],
        "sources": ["wikidata", "wikipedia", "github", "hackernews"],
        "github_query": query,
        "wiki_terms": [query],
        "hn_terms": [query],
        "occupations": [query],
        "country": "",
    }


async def build_plan(query: str) -> dict:
    try:
        text = await llm.chat(
            [
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.1,
            max_tokens=1200,
        )
        plan = llm.extract_json(text)
    except Exception:
        plan = None
    if not isinstance(plan, dict):
        plan = fallback_plan(query)
        plan["degraded"] = True
    plan.setdefault("sources", ["wikidata", "wikipedia"])
    plan.setdefault("occupations", [])
    return plan
