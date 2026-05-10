"""API Proxy — validates instance keys and forwards to the real API with the master key.

ST instances connect here instead of directly to api.lordfa.top.
Requests arrive with instance-specific keys (sk-st-xxx), get verified,
then forwarded with the real master key.  Streaming responses are
passed through chunk-by-chunk.

Rate limiting: each instance key is limited to 120 requests per minute.
"""
import time
import httpx
from fastapi import Request, HTTPException
from fastapi.responses import StreamingResponse

from manager.config import MASTER_API_KEY, API_BASE_URL
from manager.db import get_db

# Simple token bucket: {key: [timestamps]}
_rate_windows: dict[str, list[float]] = {}
_RATE_LIMIT = 120   # requests per window
_RATE_WINDOW = 60   # seconds
_RATE_CLEAN_EVERY = 300  # clean up stale entries every N requests
_rate_req_count = 0


def _check_rate(api_key: str):
    global _rate_req_count
    _rate_req_count += 1
    if _rate_req_count % _RATE_CLEAN_EVERY == 0:
        _clean_rate_limits()

    now = time.time()
    window = now - _RATE_WINDOW
    timestamps = _rate_windows.get(api_key, [])
    timestamps = [t for t in timestamps if t > window]
    timestamps.append(now)
    _rate_windows[api_key] = timestamps

    if len(timestamps) > _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded (120 req/min)")


def _clean_rate_limits():
    now = time.time()
    window = now - _RATE_WINDOW
    stale = [k for k, v in _rate_windows.items() if not any(t > window for t in v)]
    for k in stale:
        del _rate_windows[k]


def _verify_instance_key(api_key: str) -> dict | None:
    if not api_key or not api_key.startswith("sk-st-"):
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE api_key = ? AND status = 'running'",
            (api_key,),
        ).fetchone()
    return dict(row) if row else None


def _extract_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


def _build_headers(request: Request) -> dict:
    """Copy request headers but swap in the real master key."""
    headers = {}
    for key, value in request.headers.items():
        low = key.lower()
        if low in ("host", "content-length", "transfer-encoding"):
            continue
        if low == "authorization":
            headers[key] = f"Bearer {MASTER_API_KEY}"
        else:
            headers[key] = value
    return headers


async def _stream_response(method: str, url: str, headers: dict, content: bytes):
    """Stream upstream → client. Raw bytes + original headers pass through,
    so the final client (browser/Cloudflare) handles Content-Encoding correctly."""
    async with httpx.AsyncClient(timeout=120, http2=False) as client:
        async with client.stream(method, url, headers=headers, content=content) as resp:
            yield resp.status_code
            yield dict(resp.headers)
            async for chunk in resp.aiter_bytes():
                yield chunk


async def proxy_chat_completions(request: Request):
    api_key = _extract_key(request)
    if not _verify_instance_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    _check_rate(api_key)

    body = await request.body()
    headers = _build_headers(request)

    gen = _stream_response("POST", f"{API_BASE_URL}/v1/chat/completions", headers, body)
    status_code = await gen.__anext__()
    resp_headers = await gen.__anext__()

    # Only strip hop-by-hop headers — keep Content-Encoding so the chain handles it correctly
    for h in ("transfer-encoding", "connection", "keep-alive"):
        resp_headers.pop(h, None)

    return StreamingResponse(
        _iter_bytes(gen),
        status_code=status_code,
        headers=resp_headers,
        media_type="text/event-stream" if status_code == 200 else "application/json",
    )


async def _iter_bytes(gen):
    """Drain the remaining byte chunks from the async generator."""
    async for chunk in gen:
        yield chunk


async def proxy_models(request: Request):
    api_key = _extract_key(request)
    if not _verify_instance_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{API_BASE_URL}/v1/models",
            headers={"Authorization": f"Bearer {MASTER_API_KEY}"},
        )
    # httpx auto-decompresses non-stream responses, so body is raw now.
    # Remove content-encoding from forwarded headers to match.
    headers = dict(resp.headers)
    headers.pop("content-encoding", None)
    headers.pop("transfer-encoding", None)
    return StreamingResponse(
        resp.aiter_bytes(),
        status_code=resp.status_code,
        headers=headers,
    )
