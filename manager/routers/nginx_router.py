"""Nginx router backend — wraps nginx_config_service.regenerate()."""
from __future__ import annotations

from manager.nginx_config_service import regenerate


def apply_routes() -> int:
    return regenerate()
