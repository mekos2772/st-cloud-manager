"""Manager fallback router — wraps path_proxy for catch-all /st-* routing."""
from __future__ import annotations

from manager.path_proxy import proxy_path_http, proxy_path_websocket


async def handle_http(request):
    return await proxy_path_http(request)


async def handle_websocket(websocket):
    await proxy_path_websocket(websocket)
