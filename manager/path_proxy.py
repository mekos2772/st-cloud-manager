import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse

from manager.config import BASE_DIR
from manager.db import get_db


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _running_path_instances() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT instance_id, path_prefix
            FROM instances
            WHERE status='running'
              AND path_prefix IS NOT NULL
              AND path_prefix != ''
            ORDER BY length(path_prefix) DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _match_instance(path: str) -> dict | None:
    for inst in _running_path_instances():
        prefix = inst["path_prefix"].rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return inst
    return None


def _port_file(instance_id: str) -> Path:
    return BASE_DIR / "users" / instance_id / ".st_port"


def _instance_port(instance_id: str) -> int:
    pf = _port_file(instance_id)
    if not pf.exists():
        raise HTTPException(status_code=503, detail="Instance route is not ready")
    try:
        return int(pf.read_text().strip())
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Invalid instance route")


def _forward_headers(request: Request) -> dict:
    headers = {}
    for key, value in request.headers.items():
        low = key.lower()
        if low in HOP_BY_HOP_HEADERS or low in ("host", "content-length"):
            continue
        headers[key] = value
    headers["host"] = f"127.0.0.1"
    return headers


async def proxy_path_http(request: Request):
    inst = _match_instance(request.url.path)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance path not found")

    port = _instance_port(inst["instance_id"])
    target = urlunsplit((
        "http",
        f"127.0.0.1:{port}",
        request.url.path,
        request.url.query,
        "",
    ))
    body = await request.body()
    headers = _forward_headers(request)

    client = httpx.AsyncClient(timeout=None, follow_redirects=False)
    upstream_req = client.build_request(request.method, target, headers=headers, content=body)
    try:
        upstream = await client.send(upstream_req, stream=True)
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Instance proxy unavailable: {e}")

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

    async def body_iter():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


async def proxy_path_websocket(websocket: WebSocket):
    inst = _match_instance(websocket.url.path)
    if not inst:
        await websocket.close(code=1008)
        return

    port = _instance_port(inst["instance_id"])
    try:
        import websockets
    except ImportError:
        await websocket.close(code=1011)
        return

    target = urlunsplit((
        "ws",
        f"127.0.0.1:{port}",
        websocket.url.path,
        websocket.url.query,
        "",
    ))

    await websocket.accept()
    try:
        async with websockets.connect(target) as upstream:
            async def client_to_upstream():
                while True:
                    msg = await websocket.receive()
                    if "text" in msg:
                        await upstream.send(msg["text"])
                    elif "bytes" in msg:
                        await upstream.send(msg["bytes"])
                    elif msg.get("type") == "websocket.disconnect":
                        break

            async def upstream_to_client():
                async for msg in upstream:
                    if isinstance(msg, bytes):
                        await websocket.send_bytes(msg)
                    else:
                        await websocket.send_text(msg)

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
