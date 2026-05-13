"""System settings persistence."""
from __future__ import annotations

from datetime import datetime, timezone

from manager.db import get_db

_DEFAULTS: dict[str, str] = {
    "api_base_url": "http://api.lordfa.top",
    "api_model": "deepseek-v4-pro",
    "upstream_api_key": "",
    "streaming_enabled": "true",
    "default_temperature": "1.0",
    "default_context_size": "8192",
    "default_max_tokens": "4096",
    "api_mode": "proxy",
    "enable_litellm": "false",
    # Cloudflare
    "cf_api_token": "",
    "cf_zone_id": "",
    "cf_zone_name": "",
    "cf_base_domain": "",
    "cf_record_type": "CNAME",
    "cf_record_target": "",
    "cf_proxied": "false",
    "cf_ttl": "1",
    "cf_sync_delete": "true",
    "domain_mode": "local",
    # Routing
    "routing_mode": "subdomain",
    "base_domain": "",
    "path_prefix_length": "8",
    # Runtime
    "runtime_mode": "docker",
    # Trial mode
    "trial_enabled": "false",
    "trial_max_instances": "3",
    "trial_idle_timeout": "600",
    "trial_max_memory_pct": "85",
    "trial_queue_enabled": "true",
}


def get_defaults() -> dict[str, str]:
    return dict(_DEFAULTS)


def get_default(key: str) -> str:
    return _DEFAULTS.get(key, "")


def get_all_settings() -> dict[str, str]:
    """Return all settings, filling in defaults for missing keys."""
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()

    result = dict(_DEFAULTS)
    for r in rows:
        result[r["key"]] = r["value"]
    return result


def get_setting(key: str) -> str:
    row = None
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", (key,)
        ).fetchone()
    if row:
        return row["value"]
    return _DEFAULTS.get(key, "")


def set_settings(updates: dict[str, str]):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        for key, value in updates.items():
            if key not in _DEFAULTS:
                continue
            conn.execute(
                """INSERT INTO system_settings (key, value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value, now),
            )


def get_effective_api_settings() -> dict:
    from manager.config import API_BASE_URL, API_MODEL, MASTER_API_KEY, MANAGER_PROXY_URL
    s = get_all_settings()

    def _val(key: str, env_default: str, fallback: str = "") -> str:
        db_val = s.get(key, "")
        if db_val == _DEFAULTS.get(key, "") or not db_val:
            return env_default or fallback
        return db_val

    base = _val("api_base_url", API_BASE_URL)
    return {
        "api_base_url": base,
        "api_host": base.split("://")[-1].split("/")[0] if "://" in base else base,
        "api_model": _val("api_model", API_MODEL),
        "upstream_api_key": _val("upstream_api_key", "") or MASTER_API_KEY,
        "streaming_enabled": _val("streaming_enabled", "true"),
        "default_temperature": _val("default_temperature", "1.0"),
        "default_context_size": _val("default_context_size", "8192"),
        "default_max_tokens": _val("default_max_tokens", "4096"),
        "api_mode": _val("api_mode", "proxy"),
        "enable_litellm": _val("enable_litellm", "false"),
        "manager_proxy_url": MANAGER_PROXY_URL,
    }


_MASK_LENGTH = 10


def _mask_key(key: str) -> str:
    if not key or len(key) <= _MASK_LENGTH:
        return key
    return key[:6] + "*" * (len(key) - _MASK_LENGTH) + key[-4:]


def _is_masked(value: str) -> bool:
    return "****" in value or value == ""


def get_api_config_safe() -> dict:
    s = get_all_settings()
    key = s.get("upstream_api_key", "")
    s["upstream_api_key"] = _mask_key(key)
    return {
        "api_base_url": s["api_base_url"],
        "api_model": s["api_model"],
        "upstream_api_key": s["upstream_api_key"],
        "streaming_enabled": s["streaming_enabled"],
        "default_temperature": s["default_temperature"],
        "default_context_size": s["default_context_size"],
        "default_max_tokens": s["default_max_tokens"],
        "api_mode": s["api_mode"],
        "enable_litellm": s["enable_litellm"],
    }


def update_api_config(data: dict) -> dict:
    updates = {}
    for field in _DEFAULTS:
        if field in data:
            if field == "upstream_api_key":
                val = data[field]
                if not val or _is_masked(val):
                    continue
                updates[field] = val
            else:
                updates[field] = str(data[field])

    if updates:
        set_settings(updates)

    return get_api_config_safe()


def get_real_upstream_key() -> str:
    return get_setting("upstream_api_key")
