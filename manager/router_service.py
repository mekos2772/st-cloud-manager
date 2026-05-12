from __future__ import annotations

from manager.config import RUNTIME_MODE as ENV_RUNTIME_MODE
from manager.settings_service import get_all_settings


def effective_runtime_mode() -> str:
    db_mode = get_all_settings().get("runtime_mode", ENV_RUNTIME_MODE)
    return ENV_RUNTIME_MODE if ENV_RUNTIME_MODE != "docker" else db_mode


def get_runtime_service():
    if effective_runtime_mode() == "process":
        import manager.process_service as svc
        return svc
    import manager.docker_service as svc
    return svc


def sync_routes() -> int:
    if effective_runtime_mode() == "process":
        from manager.nginx_config_service import regenerate
    else:
        from manager.traefik_config_service import regenerate
    return regenerate()


def sync_routes_safely(context: str = "routes") -> int:
    try:
        return sync_routes()
    except Exception as e:
        print(f"[{context}] route sync failed: {e}")
        return 0
