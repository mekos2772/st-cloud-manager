"""Traefik router backend — wraps traefik_config_service.regenerate()."""
from __future__ import annotations

from manager.traefik_config_service import regenerate


def apply_routes() -> int:
    return regenerate()
