"""Proxy routes — API proxy for ST instances and path-based fallback routing.

/v1/chat/completions and /v1/models are always active (API proxy).
/st-* path fallback is gated by ENABLE_MANAGER_PATH_FALLBACK.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, WebSocket

from manager.api_proxy import proxy_chat_completions, proxy_models
from manager.config import ENABLE_MANAGER_PATH_FALLBACK

router = APIRouter(tags=["proxy"])


@router.api_route("/v1/chat/completions", methods=["POST", "OPTIONS"])
async def v1_chat_completions(request: Request):
    return await proxy_chat_completions(request)


@router.api_route("/v1/models", methods=["GET", "OPTIONS"])
async def v1_models(request: Request):
    return await proxy_models(request)


# Manager /st-* fallback proxy — safety net when the outer router (nginx/traefik)
# is not available.  Disable this in production behind a reliable reverse proxy
# to avoid double-hop routing.
if ENABLE_MANAGER_PATH_FALLBACK:

    from manager.path_proxy import proxy_path_http, proxy_path_websocket

    @router.api_route("/st-{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def st_path_fallback(request: Request):
        return await proxy_path_http(request)

    @router.websocket("/st-{path:path}")
    async def st_path_fallback_ws(websocket: WebSocket):
        await proxy_path_websocket(websocket)
