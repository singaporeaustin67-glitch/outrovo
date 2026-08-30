import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cache, config, connectors, planner, ranker

app = FastAPI(title="Outrovo — AI People Search")


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)


class OutreachRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    candidate: dict


class EmailRequest(BaseModel):
    candidate: dict


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "model": config.LLM_MODEL,
        "provider": config.LLM_BASE_URL,
        "sources": {
            "opencorporates": bool(config.OPENCORPORATES_TOKEN),
            "websearch": bool(config.TAVILY_API_KEY),
            "github": bool(config.GITHUB_TOKEN),
        },
    }


@app.post("/api/search")
async def search(req: SearchRequest):
    started = time.time()
    cached = cache.get(req.query)
    if cached is not None:
        return {**cached, "cached": True, "elapsed_seconds": round(time.time() - started, 1)}

    plan = await planner.build_plan(req.query)
    candidates = await connectors.gather_candidates(plan)

    try:
        ranked = await ranker.rank_candidates(req.query, candidates)
        await connectors.enrich_company_logos(ranked)
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
        cache.record(req.query, len(ranked))
    return result


@app.get("/api/history")
async def history():
    return {"history": cache.recent()}


@app.post("/api/outreach")
async def outreach(req: OutreachRequest):
    try:
        message = await ranker.draft_outreach(req.query, req.candidate)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM outreach failed: {e}")
    return {"message": message.strip()}


@app.post("/api/emails")
async def emails(req: EmailRequest):
    found = await connectors.discover_emails(req.candidate)
    return {"emails": found}


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    return FileResponse("static/index.html")
