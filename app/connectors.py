"""Connectors that fetch REAL people data from live public sources."""

import asyncio
import hashlib
import html
import re
from urllib.parse import quote_plus

import httpx

from . import config


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
        resp = await client.get(
            "https://api.github.com/search/users",
            params={"q": q, "per_page": limit, "sort": "followers"},
            headers=headers,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
        if items:
            break

    async def detail(login: str) -> dict | None:
        r = await client.get(f"https://api.github.com/users/{login}", headers=headers)
        if r.status_code != 200:
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
                "cllimit": 20,
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
    return list(candidates.values())


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


async def search_wikidata(
    client: httpx.AsyncClient,
    occupations: list[str],
    country: str = "",
    limit: int = 12,
) -> list[dict]:
    """Find real notable people (with verified social handles) via Wikidata SPARQL."""
    occ_qids = list(dict.fromkeys(
        qid for qid in await asyncio.gather(*(_resolve_qid(client, o) for o in occupations[:4])) if qid
    ))
    if not occ_qids:
        return []
    country_qid = await _resolve_qid(client, country) if country else None

    handle_selects = "\n".join(f"  OPTIONAL {{ ?person wdt:{prop} ?h_{prop[1:]}. }}" for prop in WD_HANDLE_PROPS)
    handle_vars = " ".join(f"?h_{prop[1:]}" for prop in WD_HANDLE_PROPS)
    country_clause = f"?person wdt:P27 wd:{country_qid} ." if country_qid else ""
    per_occ = max(4, limit // len(occ_qids) + 2)

    async def run_query(occ_qid: str) -> list[dict]:
        sparql = f"""
SELECT DISTINCT ?person ?personLabel ?desc ?img ?sitelinks {handle_vars} WHERE {{
  ?person wdt:P106 wd:{occ_qid} .
  ?person wikibase:sitelinks ?sitelinks .
  {country_clause}
{handle_selects}
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

    batches = await asyncio.gather(*(run_query(q) for q in occ_qids))

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
                    "stats": {"occupation": desc, "sitelinks": b.get("sitelinks", {}).get("value", "0")},
                }
                by_qid[qid] = entry
            for prop, (platform, tpl) in WD_HANDLE_PROPS.items():
                handle = b.get(f"h_{prop[1:]}", {}).get("value", "").strip()
                if handle and platform not in entry["platforms"]:
                    entry["platforms"][platform] = tpl.format(handle)

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


async def gather_candidates(plan: dict) -> list[dict]:
    sources = set(plan.get("sources", []))
    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
        tasks = []
        if "github" in sources:
            tasks.append(search_github(client, plan.get("github_query", "")))
        if "wikipedia" in sources:
            tasks.append(search_wikipedia(client, plan.get("wiki_terms", [])))
        if "hackernews" in sources:
            tasks.append(search_hackernews(client, plan.get("hn_terms", [])))
        if "wikidata" in sources:
            tasks.append(
                search_wikidata(client, plan.get("occupations", []), plan.get("country", ""))
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates: list[dict] = []
    for r in results:
        if isinstance(r, list):
            candidates.extend(r)
    return candidates
