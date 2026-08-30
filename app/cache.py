import json
import sqlite3
import time
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "search_cache.json"
HISTORY_DB = Path(__file__).resolve().parent.parent / "data" / "history.db"
TTL_SECONDS = 3600


def _conn() -> sqlite3.Connection:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS history "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, matches INTEGER, created_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS people "
        "(person_id TEXT PRIMARY KEY, data TEXT, seen_at REAL)"
    )
    return conn


# ---- persistent people index (grows with every search) ----

def store_people(candidates: list[dict]) -> None:
    """Remember every real person we've ever found — the seed of our own index."""
    conn = _conn()
    with conn:
        for c in candidates:
            pid = c.get("id")
            if not pid:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO people (person_id, data, seen_at) VALUES (?, ?, ?)",
                (pid, json.dumps(c, ensure_ascii=False), time.time()),
            )
    conn.close()


def people_index_size() -> int:
    conn = _conn()
    n = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    conn.close()
    return n


# Generic words that would match almost every stored profile.
_STOP = {"the", "and", "with", "who", "that", "based", "people", "person", "building"}


def search_people_index(terms: list[str], limit: int = 20) -> list[dict]:
    """Instant recall from our own index of everyone found in past searches.

    Matches any meaningful full term; for multi-word terms also matches each
    distinctive word so recall doesn't depend on the planner's exact phrasing.
    """
    needles: set[str] = set()
    for t in terms:
        t = (t or "").lower().strip()
        if len(t) < 3:
            continue
        if " " in t:
            needles.add(t)
            for w in t.split():
                if len(w) > 3 and w not in _STOP:
                    needles.add(w)
        else:
            needles.add(t)
    if not needles:
        return []
    conn = _conn()
    rows = conn.execute("SELECT data FROM people").fetchall()
    conn.close()
    hits = []
    for (blob,) in rows:
        low = blob.lower()
        if any(n in low for n in needles):
            try:
                hits.append(json.loads(blob))
            except json.JSONDecodeError:
                continue
        if len(hits) >= limit:
            break
    return hits


def record(query: str, matches: int) -> None:
    conn = _conn()
    with conn:
        conn.execute("INSERT INTO history (query, matches, created_at) VALUES (?, ?, ?)",
                     (query, matches, time.time()))
    conn.close()


def recent(limit: int = 20) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, query, matches, created_at FROM history ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [{"id": r[0], "query": r[1], "matches": r[2], "created_at": r[3]} for r in rows]


def _load() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache))


def get(query: str):
    key = " ".join(query.lower().split())
    entry = _load().get(key)
    if entry and (time.time() - entry.get("ts", 0)) < TTL_SECONDS:
        return entry.get("result")
    return None


def put(query: str, result) -> None:
    key = " ".join(query.lower().split())
    cache = _load()
    cache[key] = {"ts": time.time(), "result": result}
    _save(cache)
