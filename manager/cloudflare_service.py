"""Cloudflare DNS automation service.

Manages DNS records via Cloudflare API. Token is masked in all logs/display.
Supports A/CNAME records, proxied mode, and idempotent record creation.
"""
import json
import os
import re
import ssl
import urllib.request
import urllib.error

from manager.settings_service import get_setting, set_settings, _mask_key

CF_API_BASE = "https://api.cloudflare.com/client/v4"


def _clean_token(raw: str) -> str:
    """Strip 'Bearer' prefix and whitespace. Returns clean token."""
    t = raw.strip()
    # Remove repeated "Bearer" prefixes (case-insensitive)
    while True:
        lower = t.lower()
        if lower.startswith("bearer "):
            t = t[7:].strip()
        elif lower.startswith("bearer"):
            t = t[6:].strip()
        else:
            break
    return t


def _token() -> str:
    raw = get_setting("cf_api_token")
    return _clean_token(raw)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }


def _api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{CF_API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())

    # Build SSL context — accept system proxy env vars
    ctx = ssl.create_default_context()
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or None
    if proxy:
        req.set_proxy(proxy, "https")

    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_str = e.read().decode(errors="replace")
        return {"success": False, "errors": [{"message": f"HTTP {e.code}: {body_str[:500]}"}]}
    except urllib.error.URLError as e:
        reason = str(e.reason)
        if "SSL" in reason or "ssl" in reason or "certificate" in reason.lower():
            return {"success": False, "errors": [{
                "message": f"SSL连接失败: {reason}",
                "help": "可能是网络代理/防火墙拦截了 HTTPS。请设置环境变量 HTTPS_PROXY=http://proxy:port 后重启 Manager。"
            }]}
        if "No such file" in reason or "getaddrinfo" in reason.lower() or "Name or service not known" in reason:
            return {"success": False, "errors": [{
                "message": f"DNS解析失败或网络不通: {reason}",
                "help": "1) 检查能否 ping api.cloudflare.com\n2) 设置 DNS 服务器为 1.1.1.1\n3) 设置 HTTPS_PROXY 代理"
            }]}
        return {"success": False, "errors": [{"message": f"URL错误: {reason} (url={url})"}]}
    except Exception as e:
        return {"success": False, "errors": [{"message": f"{type(e).__name__}: {e}"}]}


def _clean_token_in_db():
    """One-time: clean any stored tokens that have Bearer prefix."""
    raw = get_setting("cf_api_token")
    cleaned = _clean_token(raw)
    if cleaned != raw and cleaned:
        set_settings({"cf_api_token": cleaned})


# ─── settings ───

_CF_DEFAULTS = {
    "cf_api_token": "",
    "cf_zone_id": "",
    "cf_zone_name": "",
    "cf_base_domain": "",
    "cf_record_type": "CNAME",
    "cf_record_target": "",
    "cf_proxied": "false",
    "cf_ttl": "1",
    "cf_sync_delete": "true",
    "domain_mode": "local",  # "local" or "cloudflare"
    # Runtime
    "runtime_mode": "docker",  # "docker" or "process"
    # Routing
    "routing_mode": "subdomain",  # "subdomain" or "path"
    "base_domain": "",
    "path_prefix_length": "8",
    # Trial mode
    "trial_enabled": "false",
    "trial_max_instances": "3",
    "trial_idle_timeout": "600",
    "trial_max_memory_pct": "85",
    "trial_queue_enabled": "true",
}


def get_cf_settings() -> dict:
    """Return Cloudflare and routing settings with token masked."""
    from manager.settings_service import _DEFAULTS
    result = {}
    for k, v in _CF_DEFAULTS.items():
        val = get_setting(k)
        result[k] = val if val else v
    result["cf_api_token"] = _mask_key(result["cf_api_token"])
    result["domain_mode"] = get_setting("domain_mode") or "local"
    # Runtime settings
    result["runtime_mode"] = get_setting("runtime_mode") or "docker"
    # Routing settings
    result["routing_mode"] = get_setting("routing_mode") or "subdomain"
    result["base_domain"] = get_setting("base_domain") or ""
    result["path_prefix_length"] = get_setting("path_prefix_length") or "8"
    # Trial settings
    result["trial_enabled"] = get_setting("trial_enabled") or "false"
    result["trial_max_instances"] = get_setting("trial_max_instances") or "3"
    result["trial_idle_timeout"] = get_setting("trial_idle_timeout") or "600"
    result["trial_max_memory_pct"] = get_setting("trial_max_memory_pct") or "85"
    result["trial_queue_enabled"] = get_setting("trial_queue_enabled") or "true"
    return result


def update_cf_settings(data: dict) -> dict:
    _clean_token_in_db()  # clean any old bad data first
    updates = {}
    for k in _CF_DEFAULTS:
        if k in data:
            if k == "cf_api_token":
                val = str(data[k]).strip()
                if not val or set(val) == {"*"}:
                    continue
                updates[k] = _clean_token(val)
            else:
                updates[k] = str(data[k])
    if updates:
        set_settings(updates)
    return get_cf_settings()


def is_cf_enabled() -> bool:
    return bool(_token()) and bool(get_setting("cf_zone_id"))


# ─── API actions ───

def test_token() -> dict:
    """Verify the API token works by listing zones."""
    _clean_token_in_db()
    tok = _token()
    if not tok:
        return {"ok": False, "error": "未配置 Token"}

    token_preview = _mask_key(tok)

    # Use /zones to verify — works for any token with Zone Read permission
    resp = _api("GET", "/zones?per_page=1")
    if resp.get("success"):
        n = len(resp.get("result", []))
        return {"ok": True, "message": f"Token 验证成功，可访问 {n} 个 Zone",
                "token_preview": token_preview}

    first = _first_error(resp)
    cf_errors = resp.get("errors", [])
    error_codes = [e.get("code", 0) for e in cf_errors]

    if 6003 in error_codes or 6111 in error_codes or 1000 in error_codes:
        return {"ok": False,
                "error": "Token 格式错误或无效。请只填写 Token 本体，不要包含 Bearer 前缀。",
                "help": "正确格式：AbCdEf1234567890...（纯 token）",
                "cf_code": error_codes[0], "token_preview": token_preview}

    if any("401" in str(e.get("message", "")) for e in cf_errors):
        return {"ok": False,
                "error": "Token 无效或权限不足。请重新生成 Token，权限需要 Zone Read + DNS Edit。",
                "help": "在 Cloudflare Dashboard → 个人资料 → API Tokens 创建，限定到目标 Zone。",
                "token_preview": token_preview}

    return {"ok": False, "error": first, "token_preview": token_preview}


def verify_zone() -> dict:
    """Verify the configured Zone ID is valid and accessible."""
    zone_id = get_setting("cf_zone_id")
    if not zone_id:
        return {"ok": False, "error": "Zone ID 未配置"}

    resp = _api("GET", f"/zones/{zone_id}")
    if resp.get("success"):
        z = resp["result"]
        return {"ok": True, "zone_name": z["name"], "zone_status": z["status"],
                "name_servers": z.get("name_servers", [])[:4]}
    return {"ok": False, "error": _first_error(resp),
            "help": "请检查 Zone ID 是否正确，Token 是否有该 Zone 的读取权限"}


def list_zones() -> dict:
    """List available zones."""
    resp = _api("GET", "/zones?per_page=50")
    if resp.get("success"):
        zones = [{"id": z["id"], "name": z["name"], "status": z["status"]}
                 for z in resp.get("result", [])]
        return {"ok": True, "zones": zones}
    return {"ok": False, "error": _first_error(resp)}


def create_test_record(name: str, content: str = "127.0.0.1") -> dict:
    zone_id = get_setting("cf_zone_id")
    if not zone_id:
        return {"ok": False, "error": "Zone ID 未配置"}
    record_type = "A" if re.match(r"^\d+\.\d+\.\d+\.\d+$", content) else "CNAME"
    base = get_setting("cf_base_domain")
    if not base:
        return {"ok": False, "error": "Base Domain 未配置"}
    resp = _api("POST", f"/zones/{zone_id}/dns_records", {
        "type": record_type,
        "name": f"test-{name}.{base}",
        "content": content,
        "ttl": 1,
        "proxied": False,
    })
    if resp.get("success"):
        r = resp["result"]
        return {"ok": True, "record_id": r["id"], "name": r["name"],
                "type": r["type"], "content": r["content"]}
    return {"ok": False, "error": _first_error(resp)}


def delete_test_record(record_id: str) -> dict:
    zone_id = get_setting("cf_zone_id")
    if not zone_id:
        return {"ok": False, "error": "Zone ID 未配置"}
    resp = _api("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
    return {"ok": resp.get("success", False)}


def create_dns_record(subdomain: str) -> dict:
    zone_id = get_setting("cf_zone_id")
    if not zone_id:
        raise RuntimeError("Cloudflare Zone ID not configured")

    name = f"{subdomain}.{get_setting('cf_base_domain')}"
    record_type = get_setting("cf_record_type") or "CNAME"
    content = get_setting("cf_record_target")
    proxied = get_setting("cf_proxied") == "true"
    ttl = int(get_setting("cf_ttl") or "1")

    if not content:
        raise RuntimeError("Cloudflare record target not configured")

    # Check for existing record
    existing = _api("GET", f"/zones/{zone_id}/dns_records?type={record_type}&name={name}")
    if existing.get("success") and existing.get("result"):
        old_id = existing["result"][0]["id"]
        resp = _api("PATCH", f"/zones/{zone_id}/dns_records/{old_id}", {
            "type": record_type, "name": name, "content": content,
            "ttl": ttl, "proxied": proxied,
        })
        if resp.get("success"):
            return {"record_id": old_id, "name": name, "type": record_type,
                    "content": content, "proxied": proxied, "updated": True}
        raise RuntimeError(f"DNS update failed: {_first_error(resp)}")

    resp = _api("POST", f"/zones/{zone_id}/dns_records", {
        "type": record_type, "name": name, "content": content,
        "ttl": ttl, "proxied": proxied,
    })
    if resp.get("success"):
        r = resp["result"]
        return {"record_id": r["id"], "name": r["name"], "type": r["type"],
                "content": r["content"], "proxied": r.get("proxied", False), "updated": False}
    raise RuntimeError(f"DNS creation failed: {_first_error(resp)}")


def delete_dns_record(record_id: str) -> bool:
    zone_id = get_setting("cf_zone_id")
    if not zone_id:
        return False
    resp = _api("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
    return resp.get("success", False)


def _first_error(resp: dict) -> str:
    errors = resp.get("errors", [])
    return errors[0].get("message", "Unknown error") if errors else "Unknown error"
