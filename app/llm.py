import asyncio
import json
import re

import httpx

from . import config

_MAX_RETRIES = 6


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    match = re.search(r"try again in ([\d.]+)s", resp.text)
    if match:
        return min(float(match.group(1)) + 1.0, 30.0)
    return min(2.0 * (attempt + 1), 20.0)


async def chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 4096) -> str:
    """Call an OpenAI-compatible chat completions endpoint.

    Retries per-minute rate limits with backoff; on daily-quota exhaustion
    fails over to the next configured model.
    """
    models = [config.LLM_MODEL, *[m for m in config.LLM_FALLBACK_MODELS if m != config.LLM_MODEL]]
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=90.0) as client:
        for model in models:
            for attempt in range(_MAX_RETRIES):
                resp = await client.post(
                    f"{config.LLM_BASE_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                if resp.status_code == 429:
                    if "tokens per day" in resp.text or "TPD" in resp.text:
                        break  # daily quota: fail over to the next model
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(_retry_delay(resp, attempt))
                        continue
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as e:
                    last_error = e
                    break
                return resp.json()["choices"][0]["message"]["content"]
    raise last_error or RuntimeError("all LLM models rate limited")


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM response."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = match.group(1) if match else text
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start = candidate.find(start_char)
        end = candidate.rfind(end_char)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
