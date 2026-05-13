"""Route snapshot DTO — canonical representation of a routing entry."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RouteEntry:
    instance_id: str
    domain: str
    path_prefix: str = ""
    routing_mode: str = "subdomain"
    container_name: str = ""
    port: int = 0
    ready: bool = False
    base_domain: str = ""


@dataclass
class RouteSnapshot:
    entries: list[RouteEntry] = field(default_factory=list)
    base_domain: str = ""
    routing_mode: str = "subdomain"
