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
    return conn


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
