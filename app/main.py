import asyncio
import json
import time

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, billing, cache, config, connectors, emailverify, outreach as outreach_mod, planner, ranker, refiner

app = FastAPI(title="Outrovo — AI People Search")


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


async def current_user(request: Request) -> dict | None:
    return auth.user_from_token(_bearer_token(request))


async def require_user(user: dict | None = Depends(current_user)) -> dict:
    if not user:
        raise HTTPException(status_code=401, detail="Sign up or log in to use this feature.")
    return user


async def require_search_access(user: dict | None = Depends(current_user)) -> dict | None:
    """Search stays open to anonymous visitors unless AUTH_REQUIRED is set."""
    if config.AUTH_REQUIRED and not user:
        raise HTTPException(status_code=401, detail="Create a free account to search.")
    return user


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)


class OutreachRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    candidate: dict


class EmailRequest(BaseModel):
    candidate: dict


class SendOutreachRequest(BaseModel):
    candidate: dict
    to: str = Field(min_length=3)
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)


class FeedbackRequest(BaseModel):
    query: str = Field(min_length=1)
    person_id: str = Field(min_length=1)
    vote: int = Field(ge=-1, le=1)


class FollowupSendRequest(BaseModel):
    body: str = Field(min_length=1)


class RefineRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    instruction: str = Field(min_length=2, max_length=500)
    candidates: list[dict] = Field(max_length=30)


class ListCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ListAddRequest(BaseModel):
    person: dict


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "model": config.LLM_MODEL,
        "provider": config.LLM_BASE_URL,
        "people_index": cache.people_index_size(),
        "outreach": {"sending_configured": outreach_mod.sending_configured(), **cache.outreach_stats()},
        "sources": {
            "opencorporates": bool(config.OPENCORPORATES_TOKEN),
            "websearch": bool(config.TAVILY_API_KEY),
            "github": bool(config.GITHUB_TOKEN),
            "keyless": ["wikipedia", "wikidata", "hackernews", "mastodon", "devto", "youtube",
                        "bluesky", "stackoverflow", "openalex"],
        },
        "auth_required": config.AUTH_REQUIRED,
        "billing": billing.billing_configured(),
    }


# ---- auth ----

@app.post("/api/auth/signup")
async def signup(req: SignupRequest):
    try:
        return auth.signup(req.email, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    try:
        return auth.login(req.email, req.password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/api/auth/logout")
async def logout(request: Request):
    token = _bearer_token(request)
    if token:
        auth.logout(token)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict | None = Depends(current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="not logged in")
    return {"user": {"id": user["id"], "email": user["email"], "tier": user["tier"],
                     "quota": {
                         "searches": {"used": user["searches_used"],
                                      "limit": auth.QUOTAS[user["tier"]]["searches_per_day"]},
                         "sends": {"used": user["sends_used"],
                                   "limit": auth.QUOTAS[user["tier"]]["sends_per_day"]},
                     }}}


# ---- billing (Stripe) ----

@app.post("/api/billing/checkout")
async def billing_checkout(user: dict = Depends(require_user)):
    try:
        url = await billing.create_checkout_session(user)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"checkout_url": url}


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    if not billing.verify_webhook_signature(payload, signature):
        raise HTTPException(status_code=400, detail="invalid signature")
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid payload")
    return {"received": True, "action": billing.handle_event(event)}


def _enforce_search_quota(user: dict | None) -> None:
    if not user:
        return  # anonymous searching stays unmetered for now
    err = auth.check_quota(user, "searches")
    if err:
        raise HTTPException(status_code=429, detail=err)


@app.post("/api/search")
async def search(req: SearchRequest, user: dict | None = Depends(require_search_access)):
    started = time.time()
    cached = cache.get(req.query)
    if cached is not None:
        return {**cached, "cached": True, "elapsed_seconds": round(time.time() - started, 1)}
    _enforce_search_quota(user)

    plan = await planner.build_plan(req.query)
    candidates = await connectors.gather_candidates(plan)

    try:
        ranked = await ranker.rank_candidates(req.query, candidates, user_id=user["id"] if user else None)
        await asyncio.gather(
            connectors.enrich_company_logos(ranked),
            connectors.enrich_follower_counts(ranked),
            connectors.enrich_x_stats(ranked),
        )
    except Exception:
        # Rate-limit congestion: return unranked real data rather than failing.
        ranked = [
            {
                **c,
                "fit_score": 0,
                "fit_reason": "AI review temporarily unavailable (LLM rate limited).",
                "highlights": [],
                "role": c.get("stats", {}).get("occupation", ""),
                "company": c.get("stats", {}).get("company", "").lstrip("@"),
                "country_code": "",
            }
            for c in candidates
        ]

    result = {
        "query": req.query,
        "plan": plan,
        "total_candidates": len(candidates),
        "results": ranked,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    if ranked:
        cache.put(req.query, result)
        cache.record(req.query, len(ranked), user_id=user["id"] if user else None)
        if user:
            auth.record_usage(user["id"], "searches")
    return result


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/health/sources")
async def health_sources():
    """Per-source emptiness over the last 7 days — distinguishes 'no data exists
    for that topic' from 'connector broken'."""
    return {"window_days": 7, "sources": cache.source_health()}


@app.get("/api/search/stream")
async def search_stream(q: str, user: dict | None = Depends(require_search_access)):
    """Same pipeline as POST /api/search, streamed as real progress events."""

    async def gen():
        started = time.time()
        cached = cache.get(q)
        if cached is not None:
            yield _sse("plan", cached.get("plan", {}))
            yield _sse("done", {**cached, "cached": True,
                               "elapsed_seconds": round(time.time() - started, 1)})
            return

        if user:
            err = auth.check_quota(user, "searches")
            if err:
                yield _sse("error", {"detail": err})
                return

        yield _sse("status", {"text": "AI planning the search…"})
        plan = await planner.build_plan(q)
        yield _sse("plan", plan)

        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(
            connectors.gather_candidates(plan, on_event=queue.put_nowait)
        )
        while not task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                yield _sse("source", event)
            except asyncio.TimeoutError:
                continue
        candidates = task.result() if not task.cancelled() else []
        if task.exception():
            candidates = []

        yield _sse("status", {"text": f"AI reviewing {len(candidates)} profiles…",
                              "count": len(candidates)})
        try:
            ranked = await ranker.rank_candidates(q, candidates, user_id=user["id"] if user else None)
            yield _sse("status", {"text": "Enriching logos and follower counts…"})
            await asyncio.gather(
                connectors.enrich_company_logos(ranked),
                connectors.enrich_follower_counts(ranked),
                connectors.enrich_x_stats(ranked),
            )
        except Exception:
            ranked = [
                {
                    **c,
                    "fit_score": 0,
                    "fit_reason": "AI review temporarily unavailable (LLM rate limited).",
                    "highlights": [],
                    "role": c.get("stats", {}).get("occupation", ""),
                    "company": c.get("stats", {}).get("company", "").lstrip("@"),
                    "country_code": "",
                }
                for c in candidates
            ]

        result = {
            "query": q,
            "plan": plan,
            "total_candidates": len(candidates),
            "results": ranked,
            "elapsed_seconds": round(time.time() - started, 1),
        }
        if ranked:
            cache.put(q, result)
            cache.record(q, len(ranked), user_id=user["id"] if user else None)
            if user:
                auth.record_usage(user["id"], "searches")
        yield _sse("done", result)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/history")
async def history(user: dict | None = Depends(current_user)):
    return {"history": cache.recent(user_id=user["id"] if user else None)}


@app.post("/api/outreach")
async def outreach(req: OutreachRequest, user: dict = Depends(require_user)):
    try:
        message = await ranker.draft_outreach(req.query, req.candidate)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM outreach failed: {e}")
    return {"message": message.strip()}


@app.post("/api/emails")
async def emails(req: EmailRequest, user: dict = Depends(require_user)):
    found = await connectors.discover_emails(req.candidate)
    return {"emails": emailverify.verify_all(found, key="address")}


@app.post("/api/outreach/send")
async def send_outreach(req: SendOutreachRequest, user: dict = Depends(require_user)):
    """Actually send the drafted message via the configured SMTP account.
    Logs the send and schedules a follow-up proposal (never auto-sent)."""
    err = auth.check_quota(user, "sends")
    if err:
        raise HTTPException(status_code=429, detail=err)
    try:
        result = await outreach_mod.send_outreach(
            req.candidate, req.to, req.subject, req.body, user_id=user["id"])
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"send failed: {e}")
    auth.record_usage(user["id"], "sends")
    return result


# 1x1 transparent GIF returned by the open-tracking pixel endpoint
_PIXEL_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)


@app.get("/api/track/open/{log_id}.gif")
async def track_open(log_id: int):
    """Email open-tracking pixel. Called by the recipient's mail client when it
    loads images; records the first-open time and total open count."""
    cache.record_open(log_id)
    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/api/outreach/log")
async def outreach_log(user: dict = Depends(require_user)):
    """Recent sent messages with open status (opened_at / opens per message)."""
    return {"messages": cache.recent_outreach(user_id=user["id"])}


@app.get("/api/outreach/followups")
async def followups(user: dict = Depends(require_user)):
    """Follow-up messages whose wait period has elapsed — ready to review and send."""
    return {"due": outreach_mod.due_followups(user_id=user["id"])}


@app.post("/api/outreach/followups/{followup_id}/send")
async def send_followup(followup_id: int, req: FollowupSendRequest,
                        user: dict = Depends(require_user)):
    due = {f["id"]: f for f in outreach_mod.due_followups(user_id=user["id"])}
    f = due.get(followup_id)
    if not f:
        raise HTTPException(status_code=404, detail="follow-up not found or already sent")
    err = auth.check_quota(user, "sends")
    if err:
        raise HTTPException(status_code=429, detail=err)
    candidate = {"id": f["candidate_id"]}
    try:
        await outreach_mod.send_outreach(candidate, f["to"], f"Re: {f['orig_subject']}",
                                         req.body, user_id=user["id"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"send failed: {e}")
    cache.mark_followup_sent(followup_id, user_id=user["id"])
    auth.record_usage(user["id"], "sends")
    return {"sent": True}


@app.post("/api/feedback")
async def feedback(req: FeedbackRequest, user: dict = Depends(require_user)):
    """Thumbs up/down on a result — feeds back into ranking for repeat/similar queries."""
    cache.record_feedback(req.person_id, req.query, req.vote, user_id=user["id"])
    return {"recorded": True}


# ---- saved prospect lists ----

@app.get("/api/lists")
async def lists(user: dict = Depends(require_user)):
    return {"lists": cache.my_lists(user["id"])}


@app.post("/api/lists")
async def create_list(req: ListCreateRequest, user: dict = Depends(require_user)):
    list_id = cache.create_list(user["id"], req.name)
    return {"id": list_id, "name": req.name.strip()[:100]}


@app.get("/api/lists/{list_id}")
async def get_list(list_id: int, user: dict = Depends(require_user)):
    lst = cache.get_list(list_id, user["id"])
    if not lst:
        raise HTTPException(status_code=404, detail="list not found")
    return {**lst, "members": cache.list_members(list_id)}


@app.delete("/api/lists/{list_id}")
async def delete_list(list_id: int, user: dict = Depends(require_user)):
    if not cache.delete_list(list_id, user["id"]):
        raise HTTPException(status_code=404, detail="list not found")
    return {"deleted": True}


@app.post("/api/lists/{list_id}/members")
async def add_member(list_id: int, req: ListAddRequest, user: dict = Depends(require_user)):
    if not cache.get_list(list_id, user["id"]):
        raise HTTPException(status_code=404, detail="list not found")
    added = cache.add_to_list(list_id, req.person)
    return {"added": added}


@app.delete("/api/lists/{list_id}/members/{person_id}")
async def remove_member(list_id: int, person_id: str, user: dict = Depends(require_user)):
    if not cache.get_list(list_id, user["id"]):
        raise HTTPException(status_code=404, detail="list not found")
    if not cache.remove_from_list(list_id, person_id):
        raise HTTPException(status_code=404, detail="person not in list")
    return {"removed": True}


@app.post("/api/refine")
async def refine(req: RefineRequest, user: dict = Depends(require_user)):
    try:
        out = await refiner.refine(req.query, req.instruction, req.candidates)
    except Exception:
        raise HTTPException(status_code=502, detail="refinement failed, try again")
    return out


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/pricing")
async def pricing():
    return FileResponse("static/pricing.html")


@app.get("/privacy")
async def privacy():
    return FileResponse("static/privacy.html")


@app.get("/terms")
async def terms():
    return FileResponse("static/terms.html")
