from . import llm

PLANNER_PROMPT = """You are the query-planning brain of Outrovo, an AI people-search agent.
Convert the user's natural-language request into a structured search plan for finding REAL people.

Available data sources:
- github: real developers/engineers (great for technical roles, open-source people)
- wikipedia: real notable people (founders, investors, researchers, authors, public figures)
- hackernews: only real tech/builder community members (avoid for local or non-tech businesses)
- mastodon: real people with public profiles + real follower counts (founders, developers, researchers, journalists)
- devto: real software authors on DEV.to writing about a topic (only for developer/technical queries)
- producthunt: real startup founders/makers with Twitter handles and product headlines (best for founder/maker/indie-hacker/startup queries)
- wikidata: real notable people with verified social-media handles (influencers, creators, athletes, musicians, politicians, founders, experts) — searchable by occupation and country
- bluesky: real people (founders, journalists, researchers, creators, developers) with real follower counts and bio links — good general-purpose source for most queries
- stackoverflow: all-time top answerers for a technology tag — only for clearly technical/developer-expertise queries (e.g. 'Kubernetes experts')
- openalex: real researchers/scientists in a field with citation counts and university/company affiliations — best for researcher/scientist/professor/expert queries
- opencorporates: real company officers/founders from public business registries — the right source for local business owners/executives (e.g. 'construction company founders in Ohio')
- websearch: real web-search for founders/professionals via live snippets + LLM extraction (best for local businesses, niche professionals, recent press)

Return ONLY a JSON object with this shape:
{
  "intent_summary": "one sentence describing who the user wants",
  "role_keywords": ["keyword1", "keyword2"],
  "location": "city/country/state or empty string",
  "platforms": ["instagram", "tiktok", "youtube", "x", "linkedin", "github"],
  "sources": ["github", "wikipedia", "hackernews", "mastodon", "devto", "producthunt", "wikidata", "bluesky", "stackoverflow", "openalex", "opencorporates", "websearch"],
  "github_query": "GitHub USER-search string using only free-text keywords plus location:/language:/followers:>N qualifiers (N <= 100), e.g. 'machine learning location:berlin followers:>50', or empty string if github not useful",
  "wiki_terms": ["2-4 short Wikipedia search terms for kinds of people"],
  "hn_terms": ["1-3 short keyword terms for Hacker News story search"],
  "occupations": ["1-4 Wikidata-style occupation labels, e.g. 'influencer', 'beauty YouTuber', 'venture capitalist', 'software engineer'"],
  "country": "country label like 'United States' or empty string",
  "ph_topics": ["1-2 EXACT Product Hunt topic slugs, e.g. 'artificial-intelligence', 'fintech', 'design-tools', 'developer-tools', 'saas', 'marketing', 'no-code', 'productivity', 'health', 'education' — only if producthunt is in sources"]
}

Rules:
- Only include sources that can plausibly surface the requested people; 4-7 sources is typical.
- For local small-business founders/owners (construction, retail, restaurants, contracting), "opencorporates" and/or "websearch" must be included and "hackernews" excluded.
- For researcher/scientist/professor queries, "openalex" must be included.
- For technical-expertise queries, "stackoverflow" and "github" should be included.
- "bluesky" is a good default for founders, journalists, creators and professionals.
- Keep every list short and specific. No invented names.
- If the request is about social-media influencers/creators/public figures, "wikidata" must be included and occupations should be specific (e.g. 'beauty YouTuber' not just 'YouTuber' when the niche is beauty).
- "wikidata" and "wikipedia" are best for notable/public people; "github"/"hackernews" for builders and engineers.
- "country" must be a single country label; for continents/regions (Europe, Asia, EMEA...) leave "country" empty and keep the region in "location".
"""


def fallback_plan(query: str) -> dict:
    return {
        "intent_summary": query,
        "role_keywords": [query],
        "location": "",
        "platforms": [],
        "sources": ["wikidata", "wikipedia", "bluesky", "github", "hackernews"],
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
