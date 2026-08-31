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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS outreach_log "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT, to_addr TEXT, "
        "subject TEXT, body TEXT, sent_at REAL)"
    )
    for col in ("opened_at REAL", "opens INTEGER DEFAULT 0"):
        try:
            conn.execute(f"ALTER TABLE outreach_log ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute(
        "CREATE TABLE IF NOT EXISTS followups "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT, to_addr TEXT, "
        "orig_subject TEXT, orig_body TEXT, due_at REAL, sent INTEGER DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS feedback "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, person_id TEXT, query TEXT, vote INTEGER, created_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS searches "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, query TEXT, created_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS search_entries "
        "(search_id INTEGER, source TEXT, count INTEGER)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE, password_hash TEXT, "
        "tier TEXT DEFAULT 'free', stripe_customer_id TEXT, created_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(token TEXT PRIMARY KEY, user_id INTEGER, expires_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage "
        "(user_id INTEGER, day TEXT, searches INTEGER DEFAULT 0, sends INTEGER DEFAULT 0, "
        "PRIMARY KEY (user_id, day))"
    )
    # Multi-tenancy: personal tables get a user_id (NULL = pre-auth legacy rows,
    # visible only to anonymous use of shared data, never to logged-in users).
    for table in ("history", "outreach_log", "followups", "feedback"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
        except sqlite3.OperationalError:
            pass
    return conn


# ---- users, sessions, daily usage quotas ----

def create_user(email: str, password_hash: str) -> int:
    conn = _conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, password_hash, time.time()),
        )
        user_id = cur.lastrowid
    conn.close()
    return user_id


def _user_row(row) -> dict | None:
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2], "tier": row[3],
            "stripe_customer_id": row[4], "created_at": row[5]}


def get_user_by_email(email: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, email, password_hash, tier, stripe_customer_id, created_at FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    conn.close()
    return _user_row(row)


def get_user_by_id(user_id: int) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, email, password_hash, tier, stripe_customer_id, created_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return _user_row(row)


def set_user_tier(user_id: int, tier: str, stripe_customer_id: str = "") -> None:
    conn = _conn()
    with conn:
        conn.execute(
            "UPDATE users SET tier = ?, stripe_customer_id = COALESCE(NULLIF(?, ''), stripe_customer_id) WHERE id = ?",
            (tier, stripe_customer_id, user_id),
        )
    conn.close()


def create_session(token: str, user_id: int, expires_at: float) -> None:
    conn = _conn()
    with conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    conn.close()


def get_session(token: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT token, user_id, expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"token": row[0], "user_id": row[1], "expires_at": row[2]}


def touch_session(token: str) -> None:
    conn = _conn()
    with conn:
        conn.execute("UPDATE sessions SET expires_at = ? WHERE token = ?",
                     (time.time() + 30 * 86400, token))
    conn.close()


def delete_session(token: str) -> None:
    conn = _conn()
    with conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.close()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def usage_today(user_id: int) -> dict:
    conn = _conn()
    row = conn.execute(
        "SELECT searches, sends FROM usage WHERE user_id = ? AND day = ?",
        (user_id, _today()),
    ).fetchone()
    conn.close()
    return {"searches_used": row[0] if row else 0, "sends_used": row[1] if row else 0}


def record_usage(user_id: int, kind: str) -> None:
    assert kind in ("searches", "sends")
    conn = _conn()
    with conn:
        conn.execute(
            f"INSERT INTO usage (user_id, day, {kind}) VALUES (?, ?, 1) "
            f"ON CONFLICT (user_id, day) DO UPDATE SET {kind} = {kind} + 1",
            (user_id, _today()),
        )
    conn.close()


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


def record(query: str, matches: int, user_id: int | None = None) -> None:
    conn = _conn()
    with conn:
        conn.execute("INSERT INTO history (query, matches, created_at, user_id) VALUES (?, ?, ?, ?)",
                     (query, matches, time.time(), user_id))
    conn.close()


def recent(limit: int = 20, user_id: int | None = None) -> list[dict]:
    conn = _conn()
    if user_id is None:
        rows = conn.execute(
            "SELECT id, query, matches, created_at FROM history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, query, matches, created_at FROM history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    conn.close()
    return [{"id": r[0], "query": r[1], "matches": r[2], "created_at": r[3]} for r in rows]


# ---- outreach log + follow-ups ----

def log_outreach(candidate_id: str, to_addr: str, subject: str, body: str, user_id: int | None = None) -> int:
    conn = _conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO outreach_log (candidate_id, to_addr, subject, body, sent_at, user_id) VALUES (?, ?, ?, ?, ?, ?)",
            (candidate_id, to_addr, subject, body, time.time(), user_id),
        )
        row_id = cur.lastrowid
    conn.close()
    return row_id


def record_open(log_id: int) -> None:
    """Record an email-open event (tracking pixel hit). Keeps first-open time + total count."""
    conn = _conn()
    with conn:
        conn.execute(
            "UPDATE outreach_log SET opened_at = COALESCE(opened_at, ?), opens = COALESCE(opens, 0) + 1 WHERE id = ?",
            (time.time(), log_id),
        )
    conn.close()


def recent_outreach(limit: int = 50, user_id: int | None = None) -> list[dict]:
    conn = _conn()
    if user_id is None:
        rows = conn.execute(
            "SELECT id, candidate_id, to_addr, subject, sent_at, opened_at, opens "
            "FROM outreach_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, candidate_id, to_addr, subject, sent_at, opened_at, opens "
            "FROM outreach_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    conn.close()
    return [
        {"id": r[0], "candidate_id": r[1], "to": r[2], "subject": r[3],
         "sent_at": r[4], "opened_at": r[5], "opens": r[6] or 0}
        for r in rows
    ]


def delete_outreach_log(log_id: int) -> None:
    conn = _conn()
    with conn:
        conn.execute("DELETE FROM outreach_log WHERE id = ?", (log_id,))
    conn.close()


def schedule_followup(candidate_id: str, to_addr: str, orig_subject: str, orig_body: str,
                      due_at: float, user_id: int | None = None) -> int:
    conn = _conn()
    with conn:
        cur = conn.execute(
            "INSERT INTO followups (candidate_id, to_addr, orig_subject, orig_body, due_at, user_id) VALUES (?, ?, ?, ?, ?, ?)",
            (candidate_id, to_addr, orig_subject, orig_body, due_at, user_id),
        )
        row_id = cur.lastrowid
    conn.close()
    return row_id


def due_followups(now: float, user_id: int | None = None) -> list[dict]:
    conn = _conn()
    if user_id is None:
        rows = conn.execute(
            "SELECT id, candidate_id, to_addr, orig_subject, orig_body, due_at FROM followups WHERE sent = 0 AND due_at <= ?",
            (now,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, candidate_id, to_addr, orig_subject, orig_body, due_at FROM followups WHERE sent = 0 AND due_at <= ? AND user_id = ?",
            (now, user_id),
        ).fetchall()
    conn.close()
    return [{"id": r[0], "candidate_id": r[1], "to": r[2], "orig_subject": r[3],
             "orig_body": r[4], "due_at": r[5]} for r in rows]


def mark_followup_sent(followup_id: int, user_id: int | None = None) -> bool:
    """Mark a follow-up sent. With user_id, only that user's rows match —
    returns False when the id belongs to someone else (prevents IDOR)."""
    conn = _conn()
    with conn:
        if user_id is None:
            cur = conn.execute("UPDATE followups SET sent = 1 WHERE id = ?", (followup_id,))
        else:
            cur = conn.execute(
                "UPDATE followups SET sent = 1 WHERE id = ? AND user_id = ?",
                (followup_id, user_id),
            )
        changed = cur.rowcount > 0
    conn.close()
    return changed


def outreach_stats() -> dict:
    conn = _conn()
    sent = conn.execute("SELECT COUNT(*) FROM outreach_log").fetchone()[0]
    opened = conn.execute("SELECT COUNT(*) FROM outreach_log WHERE opened_at IS NOT NULL").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM followups WHERE sent = 0").fetchone()[0]
    conn.close()
    return {"messages_sent": sent, "messages_opened": opened, "followups_pending": pending}


# ---- feedback loop (votes on results feed back into ranking) ----

def record_feedback(person_id: str, query: str, vote: int, user_id: int | None = None) -> None:
    conn = _conn()
    with conn:
        conn.execute(
            "INSERT INTO feedback (person_id, query, vote, created_at, user_id) VALUES (?, ?, ?, ?, ?)",
            (person_id, query, vote, time.time(), user_id),
        )
    conn.close()


def feedback_scores(query: str, user_id: int | None = None) -> dict[str, float]:
    """Net votes per person for this exact query (normalized).

    Feedback is a personal ranking signal: a user's votes only shape their own
    repeat searches. Anonymous (legacy NULL-user) votes stay global.
    """
    q = " ".join(query.lower().split())
    conn = _conn()
    if user_id is None:
        rows = conn.execute(
            "SELECT person_id, SUM(vote) FROM feedback WHERE query = ? AND user_id IS NULL GROUP BY person_id",
            (q,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT person_id, SUM(vote) FROM feedback WHERE query = ? AND user_id = ? GROUP BY person_id",
            (q, user_id),
        ).fetchall()
    conn.close()
    return {r[0]: float(r[1]) for r in rows}



# ---- per-search source telemetry ----

def record_search_query(query: str) -> int:
    conn = _conn()
    with conn:
        cur = conn.execute("INSERT INTO searches (query, created_at) VALUES (?, ?)",
                           (query, time.time()))
        sid = cur.lastrowid
    conn.close()
    return sid


def record_search_entries(search_id: int, entries: dict[str, int]) -> None:
    conn = _conn()
    with conn:
        conn.executemany(
            "INSERT INTO search_entries (search_id, source, count) VALUES (?, ?, ?)",
            [(search_id, src, n) for src, n in entries.items()],
        )
    conn.close()


def source_health(since_days: int = 7) -> list[dict]:
    """Per-source emptiness over recent searches, joined with whether the
    search's terms were actually relevant to that source — distinguishes 'no
    data on the topic' from 'connector broken'."""
    cutoff = time.time() - since_days * 86400
    conn = _conn()
    rows = conn.execute(
        "SELECT e.source, COUNT(*) AS searched, "
        "SUM(CASE WHEN e.count = 0 THEN 1 ELSE 0 END) AS empty, "
        "AVG(e.count) AS avg_n "
        "FROM search_entries e JOIN searches s ON s.id = e.search_id "
        "WHERE s.created_at >= ? GROUP BY e.source ORDER BY searched DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [
        {"source": r[0], "searches": r[1], "empty": r[2],
         "empty_pct": round(100 * r[2] / r[1]) if r[1] else 0,
         "avg_results": round(r[3], 1) if r[3] else 0}
        for r in rows
    ]

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
