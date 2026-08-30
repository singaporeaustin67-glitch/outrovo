import json
import time
from pathlib import Path

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "search_cache.json"
TTL_SECONDS = 3600


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
