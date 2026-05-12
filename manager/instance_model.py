from __future__ import annotations

from manager.config import PUBLIC_SCHEME


def normalize_path_prefix(path_prefix: str | None) -> str:
    if not path_prefix:
        return ""
    prefix = str(path_prefix).strip()
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/")


def normalize_domain(domain: str, path_prefix: str | None = "") -> str:
    host = (domain or "").strip().rstrip("/")
    prefix = normalize_path_prefix(path_prefix)
    if prefix and host.endswith(prefix):
        host = host[: -len(prefix)]
    return host.rstrip("/")


def build_access_url(domain: str, path_prefix: str | None = "") -> str:
    host = normalize_domain(domain, path_prefix)
    prefix = normalize_path_prefix(path_prefix)
    if prefix:
        return f"{PUBLIC_SCHEME}://{host}{prefix}/"
    return f"{PUBLIC_SCHEME}://{host}"


def with_access_url(inst: dict) -> dict:
    item = dict(inst)
    item["path_prefix"] = normalize_path_prefix(item.get("path_prefix", ""))
    item["domain"] = normalize_domain(item.get("domain", ""), item["path_prefix"])
    item["url"] = build_access_url(item["domain"], item["path_prefix"])
    return item


def route_base_domain(inst: dict) -> str:
    return normalize_domain(inst.get("domain", ""), inst.get("path_prefix", ""))
