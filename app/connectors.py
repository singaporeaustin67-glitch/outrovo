"""Connectors that fetch REAL people data from live public sources."""

import asyncio
import json
import hashlib
import html
import re
from urllib.parse import quote_plus

import httpx

from . import cache, config, llm


def _headers(extra: dict | None = None) -> dict:
    h = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


_USER_QUALIFIERS = ("location:", "language:", "followers:", "repos:", "type:")


def _sanitize_github_query(query: str) -> str:
    """Keep only qualifiers valid for GitHub *user* search and clamp follower floor."""
    kept = []
    for token in query.split():
        if any(token.startswith(q) for q in _USER_QUALIFIERS):
            if token.startswith("followers:>"):
                floor = min(int(token.split(">")[1] or 0), 100)
                token = f"followers:>{floor}"
            kept.append(token)
        elif ":" not in token:
            kept.append(token)
    return " ".join(kept)


async def _gh_get(client: httpx.AsyncClient, url: str, headers: dict, **kwargs) -> httpx.Response | None:
    """GET with short retry on rate limiting — shared datacenter IPs (Render) burn
    through the 10 req/min unauthenticated search quota fast."""
    for attempt in range(4):
        try:
            resp = await client.get(url, headers=headers, **kwargs)
        except httpx.HTTPError:
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 200:
            return resp
        if resp.status_code == 401:
            headers.pop("Authorization", None)  # token invalid — continue unauthenticated
            continue
        if resp.status_code in (403, 429):
            retry_after = resp.headers.get("retry-after")
            await asyncio.sleep(float(retry_after) if retry_after else 2 * (attempt + 1))
            continue
        return resp
    return None


async def search_github(client: httpx.AsyncClient, gh_query: str, limit: int = 8) -> list[dict]:
    if not gh_query:
        return []
    headers = _headers({"Accept": "application/vnd.github+json"})
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    items = []
    for q in (_sanitize_github_query(gh_query), _sanitize_github_query(gh_query).split("followers:")[0].strip()):
        if not q:
            continue
        resp = await _gh_get(
            client,
            "https://api.github.com/search/users",
            headers,
            params={"q": q, "per_page": limit, "sort": "followers"},
        )
        if resp is not None and resp.status_code == 200:
            items = resp.json().get("items", [])
        if items:
            break

    async def detail(login: str) -> dict | None:
        r = await _gh_get(client, f"https://api.github.com/users/{login}", headers)
        if r is None or r.status_code != 200:
            return None
        u = r.json()
        platforms = {"github": u["html_url"]}
        if u.get("twitter_username"):
            platforms["x"] = f"https://x.com/{u['twitter_username']}"
        if u.get("blog"):
            blog = u["blog"]
            platforms["website"] = blog if blog.startswith("http") else f"https://{blog}"
        return {
            "id": f"github:{u['login']}",
            "name": u.get("name") or u["login"],
            "headline": u.get("bio") or "",
            "location": u.get("location") or "",
            "source": "github",
            "profile_url": u["html_url"],
            "avatar_url": u.get("avatar_url", ""),
            "platforms": platforms,
            "stats": {
                "followers": u.get("followers", 0),
                "public_repos": u.get("public_repos", 0),
                "company": u.get("company") or "",
            },
        }

    users = await asyncio.gather(*(detail(i["login"]) for i in items))
    return [u for u in users if u]


async def _wikipedia_category_titles(client: httpx.AsyncClient, terms: list[str]) -> list[str]:
    """Resolve terms to Wikipedia *category* pages (e.g. 'Category:Canadian
    artificial intelligence researchers') — membership lists are the highest-recall
    way to enumerate notable people of a kind."""
    titles: list[str] = []
    for term in terms[:4]:
        resp = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": term,
                "srnamespace": 14,
                "srlimit": 3,
            },
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for hit in resp.json().get("query", {}).get("search", []):
            t = hit.get("title", "")
            if t.startswith("Category:") and t not in titles:
                titles.append(t)
    return titles[:4]


async def search_wikipedia(client: httpx.AsyncClient, terms: list[str], limit_per_term: int = 5) -> list[dict]:
    candidates: dict[str, dict] = {}
    for term in terms[:4]:
        resp = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": term,
                "gsrlimit": limit_per_term,
                "gsrnamespace": 0,
                "prop": "extracts|description|pageimages|categories",
                "exintro": 1,
                "explaintext": 1,
                "exsentences": 2,
                "cllimit": "max",
                "pithumbsize": 200,
            },
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        pages = resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            cats = " ".join(c.get("title", "") for c in page.get("categories", []))
            # Keep only pages that are actually about a person.
            if "Living people" not in cats and not re.search(r"Category:.*\b(people|births|biographies)\b", cats, re.I):
                continue
            title = page.get("title", "")
            extract = page.get("extract", "")
            candidates[title] = {
                "id": f"wikipedia:{page.get('pageid')}",
                "name": title,
                "headline": page.get("description") or extract[:160],
                "location": "",
                "source": "wikipedia",
                "profile_url": f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}",
                "avatar_url": page.get("thumbnail", {}).get("source", ""),
                "platforms": {"wikipedia": f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"},
                "stats": {},
                "bio": extract,
            }

    # Category-member recall: pages in categories like "Category:Canadian
    # artificial intelligence researchers" rarely surface via full-text search.
    cat_titles = await _wikipedia_category_titles(client, terms)
    member_titles: list[str] = []
    for cat in cat_titles:
        resp = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "categorymembers",
                "cmtitle": cat,
                "cmtype": "page",
                "cmlimit": 20,
            },
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for m in resp.json().get("query", {}).get("categorymembers", []):
            t = m.get("title", "")
            if t and ":" not in t and t not in candidates and t not in member_titles:
                member_titles.append(t)
    for i in range(0, len(member_titles[:40]), 40):
        batch = member_titles[i : i + 40]
        resp = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "titles": "|".join(batch),
                "prop": "extracts|description|pageimages|categories",
                "exintro": 1,
                "explaintext": 1,
                "exsentences": 2,
                "cllimit": "max",
                "pithumbsize": 200,
            },
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for page in resp.json().get("query", {}).get("pages", {}).values():
            cats = " ".join(c.get("title", "") for c in page.get("categories", []))
            if "Living people" not in cats and not re.search(r"Category:.*\b(people|births|biographies)\b", cats, re.I):
                continue
            title = page.get("title", "")
            if not title or title in candidates:
                continue
            extract = page.get("extract", "")
            candidates[title] = {
                "id": f"wikipedia:{page.get('pageid')}",
                "name": title,
                "headline": page.get("description") or extract[:160],
                "location": "",
                "source": "wikipedia",
                "profile_url": f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}",
                "avatar_url": page.get("thumbnail", {}).get("source", ""),
                "platforms": {"wikipedia": f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"},
                "stats": {},
                "bio": extract,
            }
    return list(candidates.values())


_US_STATES = {
    "alabama": "us_al", "alaska": "us_ak", "arizona": "us_az", "arkansas": "us_ar",
    "california": "us_ca", "colorado": "us_co", "connecticut": "us_ct", "delaware": "us_de",
    "florida": "us_fl", "georgia": "us_ga", "hawaii": "us_hi", "idaho": "us_id",
    "illinois": "us_il", "indiana": "us_in", "iowa": "us_ia", "kansas": "us_ks",
    "kentucky": "us_ky", "louisiana": "us_la", "maine": "us_me", "maryland": "us_md",
    "massachusetts": "us_ma", "michigan": "us_mi", "minnesota": "us_mn", "mississippi": "us_ms",
    "missouri": "us_mo", "montana": "us_mt", "nebraska": "us_ne", "nevada": "us_nv",
    "new hampshire": "us_nh", "new jersey": "us_nj", "new mexico": "us_nm", "new york": "us_ny",
    "north carolina": "us_nc", "north dakota": "us_nd", "ohio": "us_oh", "oklahoma": "us_ok",
    "oregon": "us_or", "pennsylvania": "us_pa", "rhode island": "us_ri", "south carolina": "us_sc",
    "south dakota": "us_sd", "tennessee": "us_tn", "texas": "us_tx", "utah": "us_ut",
    "vermont": "us_vt", "virginia": "us_va", "washington": "us_wa", "west virginia": "us_wv",
    "wisconsin": "us_wi", "wyoming": "us_wy",
}


def _us_jurisdiction(location: str) -> str:
    for state, code in _US_STATES.items():
        if state in (location or "").lower():
            return code
    return ""


async def search_opencorporates(
    client: httpx.AsyncClient,
    terms: list[str],
    location: str = "",
    limit: int = 12,
) -> list[dict]:
    """Real company officers/founders from public business registries (OpenCorporates).
    Requires OPENCORPORATES_TOKEN (free registration); silently skipped without it."""
    if not config.OPENCORPORATES_TOKEN:
        return []
    params: dict = {"q": " ".join(terms[:4]) or "founder", "api_token": config.OPENCORPORATES_TOKEN}
    jurisdiction = _us_jurisdiction(location)
    if jurisdiction:
        params["jurisdiction_code"] = jurisdiction
    resp = await client.get(
        "https://api.opencorporates.com/v0.4/officers/search",
        params=params,
        headers=_headers(),
        timeout=30.0,
    )
    if resp.status_code != 200:
        return []
    out = []
    for item in resp.json().get("results", {}).get("officers", []):
        officer = item["officer"]
        company = officer.get("company", {})
        company_name = company.get("name", "")
        url = officer.get("opencorporates_url") or company.get("opencorporates_url", "")
        company_url = company.get("opencorporates_url", "")
        out.append({
            "id": f"opencorporates:{officer.get('name')}:{company_name}",
            "name": officer.get("name", "").title(),
            "headline": f"{officer.get('position') or 'Officer'} at {company_name}".strip(),
            "location": company.get("registered_address_in_full", "") or company.get("jurisdiction_code", ""),
            "source": "opencorporates",
            "profile_url": url,
            "avatar_url": "",
            "platforms": {
                "website": url,
                "opencorporates": company_url or url,
            },
            "stats": {
                "company": company_name,
                "position": officer.get("position", ""),
                "company_jurisdiction": company.get("jurisdiction_code", ""),
            },
        })
        if len(out) >= limit:
            break
    return out


_EXTRACT_PROMPT = """You extract REAL people from web search results. Below are real search snippets for pages about {query}.

Snippets:
{snippets}

Extract up to 10 people who match what the user is looking for. Use ONLY names/roles/companies that literally appear in the snippets. Never invent anything.

Return ONLY a JSON array:
[{{"name": "First Last", "role": "their title (CEO/Founder/Owner...)", "company": "company name", "source_url": "the snippet URL that mentions them"}}]
"""


async def search_websearch(client: httpx.AsyncClient, query: str, occupations: list[str], location: str) -> list[dict]:
    """Real web-search layer (Tavily free tier) for founders/professionals that
    Wikidata & friends miss — e.g. local business owners. Extracts real names only
    from real snippets via LLM. Requires TAVILY_API_KEY; auto-skips without it."""
    if not config.TAVILY_API_KEY:
        return []
    topic = " ".join(occupations[:3]) or query
    resp = await client.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
        json={
            "query": f"{topic} {location}".strip(),
            "search_depth": "advanced",
            "max_results": 15,
        },
        timeout=45.0,
    )
    if resp.status_code != 200:
        return []
    results = resp.json().get("results", [])
    if not results:
        return []
    snippets = "\n".join(
        f"- url: {r.get('url', '')}\n  title: {r.get('title', '')}\n  snippet: {(r.get('content') or '')[:280]}"
        for r in results
    )
    try:
        text = await llm.chat(
            [{"role": "user", "content": _EXTRACT_PROMPT.format(query=query, snippets=snippets)}],
            temperature=0.0,
            max_tokens=2000,
        )
        people = llm.extract_json(text)
    except Exception:
        return []
    if not isinstance(people, list):
        return []
    out = []
    for p in people:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name or len(name) > 60:
            continue
        url = (p.get("source_url") or "").strip()
        role = (p.get("role") or "").strip()
        company = (p.get("company") or "").strip()
        out.append({
            "id": f"websearch:{name.lower()}:{company.lower()}",
            "name": name,
            "headline": f"{role or 'Founder'} at {company}".strip(" at"),
            "location": location,
            "source": "websearch",
            "profile_url": url,
            "avatar_url": "",
            "platforms": {"website": url} if url else {},
            "stats": {"company": company, "position": role},
        })
    return out


async def search_hackernews(client: httpx.AsyncClient, terms: list[str], limit: int = 6) -> list[dict]:
    authors: dict[str, int] = {}
    for term in terms[:3]:
        resp = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": term, "tags": "story", "hitsPerPage": 20},
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for hit in resp.json().get("hits", []):
            author = hit.get("author")
            points = hit.get("points") or 0
            if author:
                authors[author] = authors.get(author, 0) + points

    top = sorted(authors.items(), key=lambda kv: kv[1], reverse=True)[:limit]

    async def detail(username: str, points: int) -> dict | None:
        r = await client.get(
            f"https://hacker-news.firebaseio.com/v0/user/{username}.json",
            headers=_headers(),
        )
        if r.status_code != 200 or not r.json():
            return None
        u = r.json()
        about = re.sub(r"<[^>]+>", "", u.get("about") or "")
        return {
            "id": f"hackernews:{username}",
            "name": username,
            "headline": html.unescape(about)[:200] or f"Hacker News member, {u.get('karma', 0)} karma",
            "location": "",
            "source": "hackernews",
            "profile_url": f"https://news.ycombinator.com/user?id={username}",
            "avatar_url": "",
            "platforms": {"hackernews": f"https://news.ycombinator.com/user?id={username}"},
            "stats": {"karma": u.get("karma", 0), "story_points_on_topic": points},
        }

    users = await asyncio.gather(*(detail(name, pts) for name, pts in top))
    return [u for u in users if u]


async def search_mastodon(client: httpx.AsyncClient, terms: list[str], limit: int = 6) -> list[dict]:
    """Mastodon's public account search — real people, real follower counts, no key."""
    seen: dict[str, dict] = {}
    for term in terms[:3]:
        resp = await client.get(
            "https://mastodon.social/api/v2/search",
            params={"q": term, "type": "accounts", "limit": 10, "resolve": "false"},
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for acc in resp.json().get("accounts", []):
            key = acc.get("id")
            if not key or key in seen or acc.get("bot"):
                continue
            note = re.sub(r"<[^>]+>", "", acc.get("note") or "")
            platforms = {}
            for field in acc.get("fields", []):
                val = re.sub(r"<[^>]+>", "", field.get("value") or "")
                m = re.search(r"(https?://[^\s\"']+)", val)
                if not m:
                    continue
                url = m.group(1)
                if "github.com" in url:
                    platforms["github"] = url
                elif "x.com" in url or "twitter.com" in url:
                    platforms["x"] = url
                elif "linkedin.com" in url:
                    platforms["linkedin"] = url
                elif "youtube.com" in url:
                    platforms["youtube"] = url
            seen[key] = {
                "id": f"mastodon:{acc.get('acct')}",
                "name": acc.get("display_name") or acc.get("username", ""),
                "headline": note[:200] or f"Mastodon user, {acc.get('followers_count', 0)} followers",
                "location": "",
                "source": "mastodon",
                "profile_url": acc.get("url", ""),
                "avatar_url": acc.get("avatar", ""),
                "platforms": {"mastodon": acc.get("url", ""), **platforms},
                "stats": {
                    "followers": acc.get("followers_count", 0),
                    "following": acc.get("following_count", 0),
                    "posts": acc.get("statuses_count", 0),
                },
            }
    ranked = sorted(seen.values(), key=lambda c: c["stats"]["followers"], reverse=True)
    return ranked[:limit]


async def search_devto(client: httpx.AsyncClient, terms: list[str], limit: int = 6) -> list[dict]:
    """DEV.to authors writing about the topic — real devs with GitHub/X links."""
    authors: dict[str, dict] = {}

    async def fetch_tag(tag: str, label: str) -> None:
        resp = await client.get(
            "https://dev.to/api/articles",
            params={"tag": tag, "per_page": 15, "top": 7},
            headers=_headers(),
        )
        if resp.status_code != 200:
            return
        for art in resp.json():
            u = art.get("user") or {}
            username = u.get("username")
            if not username or username in authors:
                continue
            platforms = {}
            if u.get("github_username"):
                platforms["github"] = f"https://github.com/{u['github_username']}"
            if u.get("twitter_username"):
                platforms["x"] = f"https://x.com/{u['twitter_username']}"
            authors[username] = {
                "id": f"devto:{username}",
                "name": u.get("name") or username,
                "headline": f"DEV.to author writing about {label}",
                "location": "",
                "source": "devto",
                "profile_url": f"https://dev.to/{username}",
                "avatar_url": u.get("profile_image_90", ""),
                "platforms": {"devto": f"https://dev.to/{username}", **platforms},
                "stats": {"reactions": art.get("public_reactions_count", 0)},
            }

    # Try each term as a tag; if a multi-word term yields nothing,
    # fall back to its individual words (DEV.to tags are single words).
    for term in terms[:3]:
        tag = re.sub(r"[^a-z0-9]", "", term.lower().replace(" ", ""))
        if tag:
            before = len(authors)
            await fetch_tag(tag, term)
            if len(authors) == before:
                for word in term.lower().split():
                    wtag = re.sub(r"[^a-z0-9]", "", word)
                    if len(wtag) > 2:
                        await fetch_tag(wtag, term)
    return list(authors.values())[:limit]


_ph_token_cache: dict = {}


async def _producthunt_token(client: httpx.AsyncClient) -> str | None:
    if _ph_token_cache.get("token"):
        return _ph_token_cache["token"]
    if not (config.PRODUCTHUNT_API_KEY and config.PRODUCTHUNT_API_SECRET):
        return None
    resp = await client.post(
        "https://api.producthunt.com/v1/oauth/token",
        json={
            "client_id": config.PRODUCTHUNT_API_KEY,
            "client_secret": config.PRODUCTHUNT_API_SECRET,
            "grant_type": "client_credentials",
        },
    )
    if resp.status_code != 200:
        return None
    _ph_token_cache["token"] = resp.json().get("access_token")
    return _ph_token_cache["token"]


async def search_producthunt(client: httpx.AsyncClient, terms: list[str], limit: int = 8) -> list[dict]:
    """Product Hunt makers — real founders/makers with Twitter handles + headlines."""
    token = await _producthunt_token(client)
    if not token:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    makers: dict[str, dict] = {}
    for term in terms[:3]:
        slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
        if not slug:
            continue
        # PH has no search and no slug-exists check: guess the slug and
        # verify by whether any posts come back.
        query = (
            '{ posts(first: 10, topic: "' + slug + '", order: VOTES) { edges { node { name '
            "makers { name username headline twitterUsername websiteUrl } } } } }"
        )
        resp = await client.post(
            "https://api.producthunt.com/v2/api/graphql",
            json={"query": query},
            headers=headers,
        )
        if resp.status_code != 200:
            continue
        posts = (((resp.json() or {}).get("data") or {}).get("posts") or {}).get("edges", [])
        if not posts and " " in term:
            # multi-word term missed — try each word as its own slug
            for word in term.lower().split():
                wslug = re.sub(r"[^a-z0-9]+", "-", word).strip("-")
                if len(wslug) < 3:
                    continue
                resp = await client.post(
                    "https://api.producthunt.com/v2/api/graphql",
                    json={"query": query.replace(f'topic: "{slug}"', f'topic: "{wslug}"')},
                    headers=headers,
                )
                if resp.status_code == 200:
                    posts = (((resp.json() or {}).get("data") or {}).get("posts") or {}).get("edges", [])
                if posts:
                    break
        for edge in posts:
            node = edge.get("node") or {}
            for m in node.get("makers", []):
                username = m.get("username")
                if not username or username in makers:
                    continue
                platforms = {}
                if m.get("twitterUsername"):
                    platforms["x"] = f"https://x.com/{m['twitterUsername']}"
                makers[username] = {
                    "id": f"producthunt:{username}",
                    "name": m.get("name") or username,
                    "headline": m.get("headline") or f"Maker of {node.get('name', '')}",
                    "location": "",
                    "source": "producthunt",
                    "profile_url": f"https://www.producthunt.com/@{username}",
                    "avatar_url": "",
                    "platforms": {"producthunt": f"https://www.producthunt.com/@{username}", **platforms},
                    "stats": {"company": node.get("name", "")},
                }
    return list(makers.values())[:limit]


_BSKY_PUBLIC = "https://public.api.bsky.app/xrpc"
_BIO_LINK_RE = re.compile(r"(?:https?://)?(?:www\.)?(github\.com|x\.com|twitter\.com|linkedin\.com/in|youtube\.com|tik\.tok|tiktok\.com)/[^\s)\"']+", re.I)


async def search_bluesky(client: httpx.AsyncClient, terms: list[str], limit: int = 8) -> list[dict]:
    """Bluesky public API (no auth): real founders/journalists/researchers/creators
    with follower counts and bio links to other platforms."""
    actors: dict[str, dict] = {}
    for term in terms[:3]:
        resp = await client.get(
            f"{_BSKY_PUBLIC}/app.bsky.actor.searchActors",
            params={"q": term, "limit": 10},
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for a in resp.json().get("actors", []):
            did = a.get("did")
            if did and did not in actors:
                actors[did] = a
    if not actors:
        return []

    profiles: dict[str, dict] = {}
    dids = list(actors)[:25]
    resp = await client.get(
        f"{_BSKY_PUBLIC}/app.bsky.actor.getProfiles",
        params=[("actors", d) for d in dids],
        headers=_headers(),
    )
    if resp.status_code == 200:
        for p in resp.json().get("profiles", []):
            profiles[p["did"]] = p

    out = []
    for did, a in actors.items():
        p = profiles.get(did, {})
        description = p.get("description") or a.get("description") or ""
        name = p.get("displayName") or a.get("displayName") or ""
        handle = p.get("handle") or a.get("handle", "")
        if not name and not description:
            continue
        platforms: dict[str, str] = {"bluesky": f"https://bsky.app/profile/{handle}"}
        for m in _BIO_LINK_RE.finditer(description):
            url = m.group(0)
            if not url.startswith("http"):
                url = "https://" + url
            host = m.group(1).lower()
            key = (
                "github" if "github" in host
                else "x" if host.startswith(("x.", "twitter."))
                else "linkedin" if "linkedin" in host
                else "youtube" if "youtube" in host
                else "tiktok"
            )
            platforms.setdefault(key, url)
        out.append({
            "id": f"bluesky:{handle}",
            "name": name or handle,
            "headline": description.replace("\n", " ")[:200] or f"Bluesky user @{handle}",
            "location": "",
            "source": "bluesky",
            "profile_url": f"https://bsky.app/profile/{handle}",
            "avatar_url": p.get("avatar") or a.get("avatar", ""),
            "platforms": platforms,
            "stats": {
                "followers": p.get("followersCount", 0),
                "posts": p.get("postsCount", 0),
            },
        })
    out.sort(key=lambda c: c["stats"]["followers"], reverse=True)
    return out[:limit]


_YT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_YT_CHANNELS_ONLY = "EgIQAg=="  # YouTube search filter: channels


def _parse_subscriber_count(text: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMB]?)\s+subscribers", text or "", re.I)
    if not m:
        return 0
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2).upper()]
    return int(float(m.group(1)) * mult)


async def search_youtube(
    client: httpx.AsyncClient, terms: list[str], location: str = "", limit: int = 10
) -> list[dict]:
    """YouTube channel search via the public results page — no API key. The real
    creator-discovery source (subscriber counts, verified badges, niche bios)."""
    channels: dict[str, dict] = {}
    queries = [t for t in terms[:3] if t]
    if location and queries:
        queries.append(f"{queries[0]} {location}")
    for q in queries[:4]:
        try:
            resp = await client.get(
                "https://www.youtube.com/results",
                params={"search_query": q, "sp": _YT_CHANNELS_ONLY},
                headers=_YT_HEADERS,
                timeout=25.0,
            )
        except httpx.HTTPError:
            continue
        if resp.status_code != 200:
            continue
        m = re.search(r"ytInitialData\s*=\s*(\{.+?\});</script>", resp.text)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue

        def walk(node) -> None:
            if isinstance(node, dict):
                cr = node.get("channelRenderer")
                if cr and cr.get("channelId") and cr["channelId"] not in channels:
                    channels[cr["channelId"]] = cr
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)

    out = []
    for cid, cr in channels.items():
        name = (cr.get("title") or {}).get("simpleText", "").strip()
        if not name or name.endswith(" - Topic"):
            continue  # auto-generated topic channels are not people
        handle = (cr.get("subscriberCountText") or {}).get("simpleText", "")
        url = f"https://www.youtube.com/{handle}" if handle.startswith("@") else f"https://www.youtube.com/channel/{cid}"
        subs = _parse_subscriber_count((cr.get("videoCountText") or {}).get("simpleText", ""))
        desc = "".join(r.get("text", "") for r in (cr.get("descriptionSnippet") or {}).get("runs", []))
        verified = any(
            (b.get("metadataBadgeRenderer") or {}).get("style") == "BADGE_STYLE_TYPE_VERIFIED"
            for b in cr.get("ownerBadges", [])
        )
        emails = _clean_emails(desc)
        out.append({
            "id": f"youtube:{cid}",
            "name": name,
            "headline": desc.replace("\n", " ")[:200] or f"YouTube channel {handle or name}",
            "location": "",
            "source": "youtube",
            "profile_url": url,
            "avatar_url": (((cr.get("thumbnail") or {}).get("thumbnails") or [{}])[-1]).get("url", ""),
            "platforms": {"youtube": url},
            "stats": {
                "followers": subs,
                "verified": verified,
                "published_emails": emails,
            },
        })
    out.sort(key=lambda c: c["stats"]["followers"], reverse=True)
    return out[:limit]


async def search_stackoverflow(client: httpx.AsyncClient, tags: list[str], limit: int = 6) -> list[dict]:
    """Stack Overflow all-time top answerers for a technology tag — real, proven experts."""
    seen: dict[str, dict] = {}
    for tag in tags[:2]:
        slug = re.sub(r"[^a-z0-9.#+-]+", "-", tag.lower()).strip("-")
        if len(slug) < 2:
            continue
        resp = await client.get(
            f"https://api.stackexchange.com/2.3/tags/{slug}/top-answerers/all_time",
            params={"site": "stackoverflow", "pagesize": 8},
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for item in resp.json().get("items", []):
            u = item.get("user") or {}
            uid = u.get("user_id")
            if not uid or str(uid) in seen:
                continue
            seen[str(uid)] = {
                "id": f"stackoverflow:{uid}",
                "name": html.unescape(u.get("display_name", "")),
                "headline": f"Top Stack Overflow answerer in '{tag}' (score {item.get('score', 0)}, reputation {u.get('reputation', 0)})",
                "location": u.get("location", ""),
                "source": "stackoverflow",
                "profile_url": u.get("link", ""),
                "avatar_url": u.get("profile_image", ""),
                "platforms": {"stackoverflow": u.get("link", "")},
                "stats": {
                    "reputation": u.get("reputation", 0),
                    "tag_score": item.get("score", 0),
                    "website": u.get("website_url", ""),
                },
            }
            if u.get("website_url"):
                seen[str(uid)]["platforms"]["website"] = u["website_url"]
    out = sorted(seen.values(), key=lambda c: c["stats"]["tag_score"], reverse=True)
    return out[:limit]


async def search_openalex(client: httpx.AsyncClient, topics: list[str], limit: int = 10) -> list[dict]:
    """OpenAlex (no key): real researchers/scientists in a field, ranked by citations,
    with affiliations and ORCID links."""
    authors: dict[str, dict] = {}
    for topic in topics[:2]:
        resp = await client.get(
            "https://api.openalex.org/works",
            params={
                "search": topic,
                "per-page": 15,
                "sort": "cited_by_count:desc",
                "filter": "publication_year:>2014",
                "select": "id,cited_by_count,authorships",
                "mailto": "hello@outrovo.ai",
            },
            headers=_headers(),
        )
        if resp.status_code != 200:
            continue
        for w in resp.json().get("results", []):
            for a in w.get("authorships", []):
                au = a.get("author") or {}
                aid = au.get("id")
                if not aid:
                    continue
                rec = authors.setdefault(aid, {"name": au.get("display_name", ""), "cites": 0, "papers": 0, "inst": ""})
                rec["cites"] += w.get("cited_by_count") or 0
                rec["papers"] += 1
                insts = a.get("institutions") or []
                if insts and not rec["inst"]:
                    rec["inst"] = insts[0].get("display_name", "")
    if not authors:
        return []

    top = sorted(authors.items(), key=lambda kv: kv[1]["cites"], reverse=True)[:limit]
    id_filter = "|".join(aid.rsplit("/", 1)[-1] for aid, _ in top)
    details: dict[str, dict] = {}
    resp = await client.get(
        "https://api.openalex.org/authors",
        params={
            "filter": f"openalex_id:{id_filter}",
            "per-page": limit,
            "mailto": "hello@outrovo.ai",
        },
        headers=_headers(),
    )
    if resp.status_code == 200:
        for a in resp.json().get("results", []):
            details[a["id"]] = a

    out = []
    for aid, rec in top:
        d = details.get(aid, {})
        insts = d.get("last_known_institutions") or []
        inst_name = (insts[0].get("display_name", "") if insts else "") or rec["inst"]
        country = insts[0].get("country_code", "") if insts else ""
        orcid = d.get("orcid") or ""
        platforms = {"openalex": aid}
        if orcid:
            platforms["orcid"] = orcid
        works = d.get("works_count", rec["papers"])
        cites = d.get("cited_by_count", rec["cites"])
        out.append({
            "id": f"openalex:{aid.rsplit('/', 1)[-1]}",
            "name": d.get("display_name") or rec["name"],
            "headline": f"Researcher{f' at {inst_name}' if inst_name else ''} — {works} works, {cites} citations",
            "location": country,
            "source": "openalex",
            "profile_url": orcid or aid,
            "avatar_url": "",
            "platforms": platforms,
            "stats": {"citations": cites, "works": works, "company": inst_name},
        })
    return out


# Wikidata properties holding real social-media handles.
WD_HANDLE_PROPS = {
    "P2003": ("instagram", "https://www.instagram.com/{}/"),
    "P2002": ("x", "https://x.com/{}"),
    "P2397": ("youtube", "https://www.youtube.com/channel/{}"),
    "P7085": ("tiktok", "https://www.tiktok.com/@{}"),
}


_STOP_WORDS = {"a", "an", "the", "of", "and", "or", "in", "on", "for", "with", "content"}


async def _resolve_qid(client: httpx.AsyncClient, label: str) -> str | None:
    async def lookup(term: str) -> tuple[str, str] | None:
        resp = await client.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities",
                "search": term,
                "language": "en",
                "format": "json",
                "type": "item",
                "limit": 1,
            },
            headers=_headers(),
        )
        if resp.status_code != 200:
            return None
        results = resp.json().get("search", [])
        if not results:
            return None
        return results[0]["id"], results[0].get("label", "")

    def shares_word(resolved_label: str, term: str) -> bool:
        words = {w for w in re.split(r"\W+", term.lower()) if w and w not in _STOP_WORDS}
        resolved_words = set(re.split(r"\W+", resolved_label.lower()))
        return bool(words & resolved_words)

    hit = await lookup(label)
    if hit and shares_word(hit[1], label):
        return hit[0]
    if " " in label:
        # "skincare YouTuber" rarely exists as an item; "YouTuber" does.
        hit = await lookup(label.split()[-1])
        if hit:
            return hit[0]
    return hit[0] if hit else None


async def _country_demonym(client: httpx.AsyncClient, country_qid: str) -> str | None:
    """Fetch a country's demonym (e.g. Q865 -> 'Taiwanese') from Wikidata P1549."""
    resp = await client.get(
        f"https://www.wikidata.org/wiki/Special:EntityData/{country_qid}.json",
        headers=_headers(),
    )
    if resp.status_code != 200:
        return None
    claims = resp.json()["entities"].get(country_qid, {}).get("claims", {})
    for claim in claims.get("P1549", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", "")
        if isinstance(value, dict):  # monolingual text
            value = value.get("text", "")
        value = re.sub(r"[^A-Za-z ]", "", str(value)).strip()
        if value:
            return value
    return None


async def _wikidata_people_fulltext(
    client: httpx.AsyncClient,
    demonym: str,
    occupations: list[str],
    country: str,
    limit: int = 20,
) -> list[dict]:
    """Indexed full-text people search on Wikidata (fast MediaWiki API, not SPARQL)."""
    variants: list[str] = []
    for o in occupations[:4]:
        variants.append(o)
        if " " in o:
            variants.append(o.split()[-1])  # "beauty influencer" -> also "influencer"
    terms = list(dict.fromkeys(variants))[:6]
    if demonym:
        terms = [f"{demonym} {t}" for t in terms]
    qids: list[str] = []

    async def search_term(term: str) -> list[str]:
        try:
            resp = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "query", "list": "search", "srsearch": term,
                    "srlimit": 12, "format": "json",
                },
                headers=_headers(),
            )
            if resp.status_code != 200:
                return []
            return [r["title"] for r in resp.json().get("query", {}).get("search", [])]
        except httpx.HTTPError:
            return []

    for hit in await asyncio.gather(*(search_term(t) for t in terms)):
        qids += hit
    qids = [q for q in dict.fromkeys(qids) if re.fullmatch(r"Q\d+", q)][:25]
    if not qids:
        return []

    resp = await client.get(
        "https://www.wikidata.org/w/api.php",
        params={
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "labels|descriptions|claims|sitelinks",
            "languages": "en",
            "format": "json",
        },
        headers=_headers(),
    )
    if resp.status_code != 200:
        return []

    out = []
    for qid, ent in resp.json().get("entities", {}).items():
        claims = ent.get("claims", {})
        p31 = {
            c["mainsnak"]["datavalue"]["value"]["id"]
            for c in claims.get("P31", [])
            if c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
        }
        if "Q5" not in p31:  # keep humans only
            continue
        label = ent.get("labels", {}).get("en", {}).get("value", "")
        if not label or re.fullmatch(r"Q\d+", label):
            continue
        desc = ent.get("descriptions", {}).get("en", {}).get("value", "")
        platforms: dict[str, str] = {"wikidata": f"https://www.wikidata.org/wiki/{qid}"}
        for prop, (platform, tpl) in WD_HANDLE_PROPS.items():
            for claim in claims.get(prop, []):
                handle = claim.get("mainsnak", {}).get("datavalue", {}).get("value", "")
                if isinstance(handle, str) and handle.strip():
                    platforms[platform] = tpl.format(handle.strip())
                    break
        img = ""
        for claim in claims.get("P18", []):
            filename = claim.get("mainsnak", {}).get("datavalue", {}).get("value", "")
            if filename:
                fn = filename.replace(" ", "_")
                h = hashlib.md5(fn.encode()).hexdigest()
                img = f"https://upload.wikimedia.org/wikipedia/commons/{h[0]}/{h[:2]}/{fn}"
                break
        entry = {
            "id": f"wikidata:{qid}",
            "name": label,
            "headline": desc,
            "location": country or "",
            "source": "wikidata",
            "profile_url": next(
                (u for k, u in platforms.items() if k != "wikidata"),
                f"https://www.wikidata.org/wiki/{qid}",
            ),
            "avatar_url": img,
            "platforms": platforms,
            "stats": {"occupation": desc, "sitelinks": str(len(ent.get("sitelinks", {})))},
        }
        out.append(entry)
        if len(out) >= limit:
            break
    return out


_REGIONS = {
    "europe", "asia", "africa", "oceania", "north america", "south america",
    "latin america", "middle east", "southeast asia", "scandinavia", "emea",
    "apac", "worldwide", "global", "international",
}

# Occupation labels too generic to identify anyone — querying Wikidata for
# "professor" or "researcher" unfiltered just returns the most sitelinked
# humans in history (Clinton, Confucius...). Specific labels only.
_GENERIC_OCCUPATIONS = {
    "researcher", "professor", "academic", "scientist", "expert", "founder",
    "engineer", "developer", "author", "writer", "consultant", "entrepreneur",
    "person", "professional", "specialist", "executive", "manager", "director",
    "businessperson", "teacher", "lecturer", "scholar",
}


async def search_wikidata(
    client: httpx.AsyncClient,
    occupations: list[str],
    country: str = "",
    limit: int = 12,
) -> list[dict]:
    """Find real notable people (with verified social handles) via Wikidata SPARQL."""
    specific = [o for o in occupations if o.strip().lower() not in _GENERIC_OCCUPATIONS]
    if not specific and occupations:
        specific = occupations[:1]  # all-generic request: keep one, rely on ranker
    occ_qids = list(dict.fromkeys(
        qid for qid in await asyncio.gather(*(_resolve_qid(client, o) for o in specific[:4])) if qid
    ))
    if not occ_qids:
        return []
    # Continents/regions are not countries — P27 citizenship filtering on them
    # would silently return nothing.
    if country and country.strip().lower() in _REGIONS:
        country = ""
    country_qid = await _resolve_qid(client, country) if country else None
    demonym = await _country_demonym(client, country_qid) if country_qid else None

    handle_selects = "\n".join(f"  OPTIONAL {{ ?person wdt:{prop} ?h_{prop[1:]}. }}" for prop in WD_HANDLE_PROPS)
    handle_vars = " ".join(f"?h_{prop[1:]}" for prop in WD_HANDLE_PROPS)
    # P27 (citizenship) is sparse for some countries; also match by demonym in the
    # English description (e.g. "Taiwanese YouTuber") to widen recall.
    country_filter = f"?person wdt:P27 wd:{country_qid} ." if country_qid else ""
    demonym_filter = f'FILTER(CONTAINS(LCASE(STR(?desc)), "{demonym.lower()}"))' if demonym else ""
    per_occ = max(4, (limit * 2) // len(occ_qids) + 2)

    async def run_query(occ_qid: str, filt: str) -> list[dict]:
        occ_clause = f"?person wdt:P106 wd:{occ_qid} ." if occ_qid else ""
        sparql = f"""
SELECT DISTINCT ?person ?personLabel ?desc ?img ?sitelinks ?followers {handle_vars} WHERE {{
  {occ_clause}
  ?person wikibase:sitelinks ?sitelinks .
  {filt}
{handle_selects}
  OPTIONAL {{ ?person wdt:P8687 ?followers . }}
  OPTIONAL {{ ?person wdt:P18 ?img . }}
  OPTIONAL {{ ?person schema:description ?desc . FILTER(LANG(?desc) = "en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
ORDER BY DESC(?sitelinks)
LIMIT {per_occ * 3}
"""
        try:
            resp = await client.get(
                "https://query.wikidata.org/sparql",
                params={"query": sparql, "format": "json"},
                headers=_headers(),
                timeout=40.0,
            )
        except httpx.HTTPError:
            return []
        if resp.status_code != 200:
            return []
        return resp.json()["results"]["bindings"]

    queries = [(q, country_filter) for q in occ_qids]
    if demonym_filter:
        queries += [(q, demonym_filter) for q in occ_qids]
    sparql_task = asyncio.gather(*(run_query(q, f) for q, f in queries))
    # Indexed full-text search widens recall where occupation tagging is sparse.
    fulltext_task = _wikidata_people_fulltext(
        client, demonym or "", specific or occupations, country, limit=limit
    ) if (demonym or country) else asyncio.sleep(0, result=[])
    batches, fulltext_results = await asyncio.gather(sparql_task, fulltext_task)

    by_qid: dict[str, dict] = {}
    for bindings in batches:
        for b in bindings:
            qid = b["person"]["value"].rsplit("/", 1)[-1]
            if re.fullmatch(r"Q\d+", b["personLabel"]["value"]):
                continue  # no human-readable label available
            entry = by_qid.get(qid)
            if not entry:
                desc = b.get("desc", {}).get("value", "")
                entry = {
                    "id": f"wikidata:{qid}",
                    "name": b["personLabel"]["value"],
                    "headline": desc,
                    "location": country or "",
                    "source": "wikidata",
                    "profile_url": f"https://www.wikidata.org/wiki/{qid}",
                    "avatar_url": b.get("img", {}).get("value", ""),
                    "platforms": {"wikidata": f"https://www.wikidata.org/wiki/{qid}"},
                    "stats": {
                        "occupation": desc,
                        "sitelinks": b.get("sitelinks", {}).get("value", "0"),
                        "social_followers": b.get("followers", {}).get("value"),
                    },
                }
                by_qid[qid] = entry
            for prop, (platform, tpl) in WD_HANDLE_PROPS.items():
                handle = b.get(f"h_{prop[1:]}", {}).get("value", "").strip()
                if handle and platform not in entry["platforms"]:
                    entry["platforms"][platform] = tpl.format(handle)

    for ft in fulltext_results:
        qid = ft["id"].split(":", 1)[1]
        if qid not in by_qid:
            by_qid[qid] = ft
        else:
            by_qid[qid]["platforms"].update(ft["platforms"])

    candidates = list(by_qid.values())
    for c in candidates:
        social = [u for k, u in c["platforms"].items() if k != "wikidata"]
        if social:
            c["profile_url"] = social[0]
    # People with reachable social profiles are more actionable; rank them first.
    candidates.sort(
        key=lambda c: (len(c["platforms"]) > 1, int(c["stats"].get("sitelinks", 0) or 0)),
        reverse=True,
    )
    return candidates[:limit]


async def enrich_company_logos(candidates: list[dict]) -> None:
    """Resolve company names to logo URLs via Wikidata (real logos, free)."""
    companies = {c["company"].strip() for c in candidates if c.get("company", "").strip()}
    if not companies:
        return

    async def resolve(client: httpx.AsyncClient, name: str) -> tuple[str, str]:
        try:
            resp = await client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities",
                    "search": name,
                    "language": "en",
                    "format": "json",
                    "type": "item",
                    "limit": 1,
                },
                headers=_headers(),
            )
            if resp.status_code != 200:
                return name, ""
            results = resp.json().get("search", [])
            if not results:
                return name, ""
            qid = results[0]["id"]
            resp = await client.get(
                f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json",
                headers=_headers(),
            )
            if resp.status_code != 200:
                return name, ""
            entity = resp.json()["entities"][qid]
            logo = entity.get("claims", {}).get("P154", [])
            if not logo:
                return name, ""
            filename = logo[0]["mainsnak"]["datavalue"]["value"].replace(" ", "_")
            h = hashlib.md5(filename.encode()).hexdigest()
            # SVG thumbs on Wikimedia need a .png suffix; PNG/JPG use the raw name.
            thumb = f"120px-{filename}.png" if filename.lower().endswith(".svg") else f"120px-{filename}"
            url = f"https://upload.wikimedia.org/wikipedia/commons/thumb/{h[0]}/{h[:2]}/{filename}/{thumb}"
            return name, url
        except Exception:
            return name, ""

    async with httpx.AsyncClient(timeout=20) as client:
        pairs = await asyncio.gather(*(resolve(client, n) for n in list(companies)[:12]))
    logo_map = dict(pairs)
    for c in candidates:
        c["company_logo"] = logo_map.get(c.get("company", "").strip(), "")


_YT_CHANNEL_RE = re.compile(r"/channel/(UC[\w-]+)")


async def enrich_follower_counts(candidates: list[dict], max_fetch: int = 6) -> None:
    """Fill follower counts + avg views from the free public socialcounts API
    (live YouTube metrics). Only runs for channels whose ID we truly know."""
    targets = []
    for c in candidates:
        s = c.get("stats", {})
        if s.get("social_followers") is not None or s.get("followers") is not None:
            continue
        m = _YT_CHANNEL_RE.search(c.get("platforms", {}).get("youtube", ""))
        if m:
            targets.append((c, m.group(1)))
    if not targets:
        return

    async def fetch(client: httpx.AsyncClient, channel_id: str) -> tuple[int | None, int | None]:
        try:
            resp = await client.get(
                f"https://api.socialcounts.org/youtube-live-subscriber-count/{channel_id}",
                headers=_headers(),
            )
            if resp.status_code != 200:
                return None, None
            e = resp.json().get("counters", {}).get("estimation", {})
            subs = e.get("subscriberCount")
            views, videos = e.get("viewCount"), e.get("videoCount")
            avg = round(views / videos) if views and videos else None
            return subs, avg
        except (httpx.HTTPError, ValueError):
            return None, None

    async with httpx.AsyncClient(timeout=15) as client:
        results = await asyncio.gather(
            *(fetch(client, cid) for _, cid in targets[:max_fetch])
        )
    for (c, _), (subs, avg) in zip(targets[:max_fetch], results):
        s = c.setdefault("stats", {})
        if subs:
            s["social_followers"] = subs
        if avg:
            s["avg_views"] = avg


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAIL_BLOCKLIST = (
    "noreply", "no-reply", "example.com", "example.org", "sentry", "localhost",
    "users.noreply.github.com", "@2x.", "@3x.", "w3.org", "schema.org",
    "creativecommons", "wikimedia", "mediawiki", "bot@", "placeholder",
)


def _clean_emails(text: str) -> list[str]:
    found = []
    for m in _EMAIL_RE.finditer(text or ""):
        addr = m.group(0).lower().strip(".")
        if any(b in addr for b in _EMAIL_BLOCKLIST):
            continue
        if addr not in found:
            found.append(addr)
    return found


async def _emails_from_github(client: httpx.AsyncClient, login: str) -> list[dict]:
    """Emails a GitHub user published: profile field + public commit authorship."""
    headers = _headers({"Accept": "application/vnd.github+json"})
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    out = []

    resp = await client.get(f"https://api.github.com/users/{login}", headers=headers)
    if resp.status_code == 200:
        email = (resp.json().get("email") or "").strip().lower()
        if email and not any(b in email for b in _EMAIL_BLOCKLIST):
            out.append({"address": email, "source": "GitHub profile", "url": f"https://github.com/{login}"})

    # Public events expose commit author emails the user pushed with.
    resp = await client.get(
        f"https://api.github.com/users/{login}/events/public",
        params={"per_page": 30},
        headers=headers,
    )
    if resp.status_code == 200:
        for event in resp.json():
            if event.get("type") != "PushEvent":
                continue
            for commit in event.get("payload", {}).get("commits", []):
                addr = (commit.get("author", {}).get("email") or "").lower()
                if addr and not any(b in addr for b in _EMAIL_BLOCKLIST):
                    if all(e["address"] != addr for e in out):
                        out.append({"address": addr, "source": "GitHub commits", "url": f"https://github.com/{login}"})
    return out


async def _emails_from_hackernews(client: httpx.AsyncClient, username: str) -> list[dict]:
    resp = await client.get(f"https://hacker-news.firebaseio.com/v0/user/{username}.json")
    if resp.status_code != 200:
        return []
    about = html.unescape(resp.json().get("about", "") or "")
    return [
        {"address": a, "source": "Hacker News bio", "url": f"https://news.ycombinator.com/user?id={username}"}
        for a in _clean_emails(re.sub(r"<[^>]+>", " ", about))
    ]


async def _emails_from_wikidata(client: httpx.AsyncClient, qid: str) -> list[dict]:
    resp = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", headers=_headers())
    if resp.status_code != 200:
        return []
    claims = resp.json()["entities"].get(qid, {}).get("claims", {})
    out = []
    for claim in claims.get("P968", []):  # email address property
        addr = (claim.get("mainsnak", {}).get("datavalue", {}).get("value") or "").lower()
        if addr and not any(b in addr for b in _EMAIL_BLOCKLIST):
            out.append({"address": addr, "source": "Wikidata", "url": f"https://www.wikidata.org/wiki/{qid}"})
    return out


async def _emails_from_mastodon(client: httpx.AsyncClient, profile_url: str) -> list[dict]:
    """Mastodon users often publish emails in their bio/fields."""
    acct = profile_url.rstrip("/").rsplit("/", 1)[-1].lstrip("@")
    if "@" not in acct:
        host = profile_url.split("/")[2] if "://" in profile_url else "mastodon.social"
        acct = f"{acct}@{host}"
    resp = await client.get(
        f"https://{acct.split('@')[-1]}/api/v1/accounts/lookup",
        params={"acct": acct},
        headers=_headers(),
    )
    if resp.status_code != 200:
        return []
    acc = resp.json()
    text = re.sub(r"<[^>]+>", " ", acc.get("note") or "")
    for field in acc.get("fields", []):
        text += " " + re.sub(r"<[^>]+>", " ", field.get("value") or "")
    return [
        {"address": a, "source": "Mastodon profile", "url": profile_url}
        for a in _clean_emails(text)
    ]


async def _emails_from_bluesky(client: httpx.AsyncClient, profile_url: str) -> list[dict]:
    """Bluesky bios often include a public contact email."""
    handle = profile_url.rstrip("/").rsplit("/", 1)[-1]
    if not handle:
        return []
    resp = await client.get(
        f"{_BSKY_PUBLIC}/app.bsky.actor.getProfile",
        params={"actor": handle},
        headers=_headers(),
    )
    if resp.status_code != 200:
        return []
    text = resp.json().get("description") or ""
    return [
        {"address": a, "source": "Bluesky bio", "url": profile_url}
        for a in _clean_emails(text)
    ]


async def _emails_from_producthunt(client: httpx.AsyncClient, username: str) -> list[dict]:
    """Product Hunt makers link a personal website — scan it for published emails."""
    token = await _producthunt_token(client)
    if not token:
        return []
    resp = await client.post(
        "https://api.producthunt.com/v2/api/graphql",
        json={"query": '{ user(username: "' + username + '") { websiteUrl } }'},
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        return []
    site = (((resp.json() or {}).get("data") or {}).get("user") or {}).get("websiteUrl")
    if not site or not site.startswith("http"):
        return []
    return await _emails_from_website(client, site)


async def _emails_from_website(client: httpx.AsyncClient, url: str) -> list[dict]:
    """Scan a person's own public website for mailto: links and published addresses."""
    try:
        resp = await client.get(url, headers=_headers({"Accept": "text/html"}), follow_redirects=True, timeout=12.0)
    except httpx.HTTPError:
        return []
    if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
        return []
    body = resp.text[:400_000]
    mailtos = [m.replace("mailto:", "").split("?")[0] for m in re.findall(r"mailto:([^\"'>\s]+)", body, re.I)]
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", body)
    found = _clean_emails(" ".join(mailtos)) + [a for a in _clean_emails(re.sub(r"<[^>]+>", " ", text)) if a not in _clean_emails(" ".join(mailtos))]
    return [{"address": a, "source": "personal website", "url": url} for a in found[:4]]


async def discover_emails(candidate: dict) -> list[dict]:
    """Find email addresses this person has publicly published. Never guesses."""
    platforms = candidate.get("platforms", {})
    results: list[dict] = []

    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
        calls = []
        if "github" in platforms:
            calls.append(_emails_from_github(client, platforms["github"].rstrip("/").rsplit("/", 1)[-1]))
        if "hackernews" in platforms:
            calls.append(_emails_from_hackernews(client, platforms["hackernews"].split("id=")[-1]))
        if "wikidata" in platforms:
            calls.append(_emails_from_wikidata(client, platforms["wikidata"].rsplit("/", 1)[-1]))
        if "mastodon" in platforms:
            calls.append(_emails_from_mastodon(client, platforms["mastodon"]))
        if "bluesky" in platforms:
            calls.append(_emails_from_bluesky(client, platforms["bluesky"]))
        if "producthunt" in platforms:
            calls.append(_emails_from_producthunt(client, platforms["producthunt"].split("@")[-1]))
        for key in ("website", "blog"):
            if key in platforms:
                calls.append(_emails_from_website(client, platforms[key]))
        # GitHub blog field stored in stats
        blog = candidate.get("stats", {}).get("blog", "")
        if blog and blog.startswith("http"):
            calls.append(_emails_from_website(client, blog))
        for batch in await asyncio.gather(*calls, return_exceptions=True):
            if isinstance(batch, list):
                results.extend(batch)

    for addr in candidate.get("stats", {}).get("published_emails", []):
        results.append({"address": addr, "source": f"{candidate.get('source', 'profile')} bio", "url": candidate.get("profile_url", "")})

    seen, unique = set(), []
    for e in results:
        if e["address"] not in seen:
            seen.add(e["address"])
            unique.append(e)
    return unique


_NOTABILITY_GATED = {"wikidata", "wikipedia", "index"}


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _norm_url(url: str) -> str:
    u = (url or "").lower().strip()
    u = re.sub(r"^https?://(www\.)?", "", u).rstrip("/").split("?")[0]
    return u


def merge_candidates(candidates: list[dict]) -> list[dict]:
    """Unify records that represent the same real person across sources.

    Two records merge when they share a platform profile URL (github/x/linkedin/
    website/etc.), or when they share an exact normalized name AND both come from
    notability-gated sources (wikidata/wikipedia/index) where a name match is
    near-certain to be the same person. Open platforms (SO, HN, bluesky...) never
    merge on name alone — common names would cause false merges.
    """
    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    by_link: dict[str, int] = {}
    by_gated_name: dict[str, int] = {}
    for i, c in enumerate(candidates):
        for key, url in (c.get("platforms") or {}).items():
            if key in ("wikidata", "wikipedia", "openalex"):
                continue  # source-specific IDs; never shared across records
            u = _norm_url(url)
            if not u:
                continue
            if u in by_link:
                union(i, by_link[u])
            else:
                by_link[u] = i
        if set((c.get("source") or "").split("+")) & _NOTABILITY_GATED:
            n = _norm_name(c.get("name", ""))
            if len(n) >= 5:
                if n in by_gated_name:
                    union(i, by_gated_name[n])
                else:
                    by_gated_name[n] = i

    groups: dict[int, list[dict]] = {}
    for i in range(len(candidates)):
        groups.setdefault(find(i), []).append(candidates[i])

    merged: list[dict] = []
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        primary = max(
            group,
            key=lambda c: (
                bool(c.get("avatar_url")),
                (c.get("stats") or {}).get("followers") or 0,
                len(c.get("headline") or ""),
            ),
        )
        out = dict(primary)
        platforms = dict(primary.get("platforms") or {})
        stats = dict(primary.get("stats") or {})
        srcs: list[str] = []
        for c in group:
            s = c.get("source", "")
            if s and s not in srcs:
                srcs.append(s)
            for k, v in (c.get("platforms") or {}).items():
                platforms.setdefault(k, v)
            for k, v in (c.get("stats") or {}).items():
                if k not in stats or stats[k] in ("", 0, None):
                    stats[k] = v
            for field in ("location", "headline", "bio", "profile_url"):
                if not out.get(field) and c.get(field):
                    out[field] = c[field]
        out["platforms"] = platforms
        out["stats"] = stats
        out["sources"] = srcs
        out["source"] = "+".join(srcs)
        merged.append(out)
    return merged


async def gather_candidates(plan: dict, on_event=None) -> list[dict]:
    sources = set(plan.get("sources", []))
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
        tasks: dict[str, asyncio.Task] = {}
        if "github" in sources:
            tasks["github"] = search_github(client, plan.get("github_query", ""))
        if "wikipedia" in sources:
            tasks["wikipedia"] = search_wikipedia(client, plan.get("wiki_terms", []))
        if "hackernews" in sources:
            tasks["hackernews"] = search_hackernews(client, plan.get("hn_terms", []))
        if "mastodon" in sources:
            tasks["mastodon"] = search_mastodon(client, plan.get("hn_terms", []) or plan.get("occupations", []))
        if "devto" in sources:
            tasks["devto"] = search_devto(client, plan.get("occupations", []) or plan.get("hn_terms", []))
        # Topic-like terms (field/technology), NOT person-type labels like
        # "researcher" — searching OpenAlex/Bluesky for "researcher" returns noise.
        topic_terms = list(dict.fromkeys(
            (plan.get("role_keywords", []) or []) + (plan.get("hn_terms", []) or [])
        ))
        if "bluesky" in sources:
            tasks["bluesky"] = search_bluesky(client, topic_terms or plan.get("occupations", []))
        if "stackoverflow" in sources:
            tasks["stackoverflow"] = search_stackoverflow(client, topic_terms)
        if "openalex" in sources:
            tasks["openalex"] = search_openalex(client, topic_terms)
        if "youtube" in sources:
            tasks["youtube"] = search_youtube(client, topic_terms, location=plan.get("location", ""))
        if "producthunt" in sources:
            tasks["producthunt"] = search_producthunt(
                client,
                plan.get("ph_topics") or plan.get("role_keywords", []) or plan.get("hn_terms", []),
            )
        if "opencorporates" in sources:
            terms = plan.get("role_keywords", []) or plan.get("wiki_terms", [])
            location = plan.get("location") or plan.get("country", "")
            tasks["opencorporates"] = search_opencorporates(client, terms, location)
        if "websearch" in sources:
            location = plan.get("location") or plan.get("country", "")
            tasks["websearch"] = search_websearch(client, query=plan.get("intent_summary", ""), occupations=plan.get("occupations", []), location=location)
        if "wikidata" in sources:
            tasks["wikidata"] = search_wikidata(client, plan.get("occupations", []), plan.get("country", ""))
            # Wikidata coverage is thin for some regions/topics; Wikipedia full-text
            # complements it even when the planner picked only Wikidata.
            if "wikipedia" not in tasks:
                terms = plan.get("wiki_terms", []) or [
                    f"{plan.get('country', '')} {occ}".strip() for occ in plan.get("occupations", [])[:3]
                ]
                tasks["wikipedia"] = search_wikipedia(client, terms)
                plan["sources"] = sorted(set(plan.get("sources", [])) | {"wikipedia"})

        async def track(name: str, coro) -> list[dict]:
            try:
                found = await coro
            except Exception:
                found = []
            if on_event:
                on_event({"type": "source", "source": name, "count": len(found)})
            return found

        results = await asyncio.gather(
            *(track(name, coro) for name, coro in tasks.items()),
            return_exceptions=True,
        )

    candidates: list[dict] = []
    seen_ids: set[str] = set()
    for r in results:
        if isinstance(r, list):
            for c in r:
                if c.get("id") and c["id"] not in seen_ids:
                    seen_ids.add(c["id"])
                    candidates.append(c)

    # Unify the same real person appearing in multiple live sources into one
    # enriched profile (cross-platform links, combined stats).
    candidates = merge_candidates(candidates)

    # Merge in real people found in past searches (our own growing index),
    # skipping anyone already returned by the live sources.
    known = {c.get("id") for c in candidates}
    terms = (
        (plan.get("occupations") or [])
        + (plan.get("role_keywords") or [])
        + (plan.get("hn_terms") or [])
    )
    for hit in cache.search_people_index(terms):
        if hit.get("id") not in known:
            known.add(hit.get("id"))
            hit["source"] = hit.get("source", "index")
            candidates.append(hit)

    # Persist everyone we found so the index keeps growing.
    cache.store_people([c for c in candidates if "index" not in c.get("source", "")])
    # Index hits can duplicate a person just found live — unify once more.
    return merge_candidates(candidates)
