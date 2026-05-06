"""Directly test OpenAI-compatible API endpoints (not via SillyTavern)."""
import json
import urllib.request
import urllib.error

from manager.settings_service import get_all_settings


def _api_url() -> str:
    s = get_all_settings()
    base = s["api_base_url"].rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return f"{base}/chat/completions"


def _headers() -> dict:
    s = get_all_settings()
    return {
        "Authorization": f"Bearer {s['upstream_api_key']}",
        "Content-Type": "application/json",
    }


def test_connection() -> dict:
    """Non-streaming chat completions test."""
    body = json.dumps({
        "model": get_all_settings()["api_model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 20,
        "stream": False,
    }).encode()

    url = _api_url()
    req = urllib.request.Request(url, data=body, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            if resp.status == 200 and "choices" in data:
                return {"ok": True, "message": "连接成功"}
            return {"ok": False, "error": f"响应异常: status={resp.status}"}
    except urllib.error.HTTPError as e:
        body_str = e.read().decode(errors="replace")
        return {"ok": False, "error": f"HTTP {e.code}: {body_str[:300]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def test_stream() -> dict:
    """Streaming chat completions test."""
    body = json.dumps({
        "model": get_all_settings()["api_model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 20,
        "stream": True,
    }).encode()

    url = _api_url()
    req = urllib.request.Request(url, data=body, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            chunks = 0
            content_type = resp.headers.get("Content-Type", "")
            for line in resp:
                if line.startswith(b"data: "):
                    chunks += 1
            is_sse = "text/event-stream" in content_type or "application/x-ndjson" in content_type
            if chunks > 0 or is_sse:
                return {"ok": True, "stream": True, "message": "流式测试成功"}
            return {"ok": False, "stream": False, "error": "未检测到流式数据"}
    except urllib.error.HTTPError as e:
        body_str = e.read().decode(errors="replace")
        return {"ok": False, "stream": False, "error": f"HTTP {e.code}: {body_str[:300]}"}
    except Exception as e:
        return {"ok": False, "stream": False, "error": str(e)}
