from __future__ import annotations

import os

from manager.config import RUNTIME_MODE as ENV_RUNTIME_MODE
from manager.settings_service import get_all_settings


def effective_runtime_mode() -> str:
    db_mode = get_all_settings().get("runtime_mode", ENV_RUNTIME_MODE)
    return ENV_RUNTIME_MODE if ENV_RUNTIME_MODE != "docker" else db_mode


def get_runtime_service():
    """Return a RuntimeAdapter instance — business code calls protocol methods only."""
    # Mock ST E2E mode: use fake runtime that hosts real HTTP servers.
    # Controlled by ST_E2E_FAKE_SERVER=1 (set by validate_http_e2e.py --mock-st).
    if os.environ.get("ST_E2E_FAKE_SERVER", "0") == "1":
        from tests.helpers.fake_runtime import FakeE2ERuntime
        return FakeE2ERuntime()

    if effective_runtime_mode() == "process":
        from manager.runtimes.process_runtime import ProcessRuntime
        return ProcessRuntime()
    from manager.runtimes.docker_runtime import DockerRuntime
    return DockerRuntime()


def sync_routes() -> int:
    if os.environ.get("ST_E2E_FAKE_SERVER", "0") == "1":
        return 0  # skip nginx reload in mock mode
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
