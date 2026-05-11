"""Generate Traefik file-provider dynamic config and reload Traefik.

Writes a YAML file then restarts the Traefik container so it picks up
the new routes.  A restart takes ~1 second, which is fine for the
infrequent create/stop/delete operations.
"""
import subprocess
from manager.db import get_db
from manager.config import TRAEFIK_DYNAMIC_CONFIG, TRAEFIK_ENTRYPOINT

TRAEFIK_CONTAINER = "st-traefik"


def _render_routers(running: list[dict]) -> str:
    if not running:
        return "    {}\n"
    lines = []
    for inst in running:
        cid = inst["container_name"]
        domain = inst["domain"]
        path_prefix = inst.get("path_prefix", "")
        entry = TRAEFIK_ENTRYPOINT
        lines.append(f"    {cid}:\n")
        lines.append(f"      entryPoints:\n")
        lines.append(f"        - {entry}\n")
        if path_prefix:
            base = domain.replace(path_prefix, "") if path_prefix in domain else domain
            lines.append(f'      rule: "Host(`{base}`) && PathPrefix(`{path_prefix}`)"\n')
        else:
            lines.append(f'      rule: "Host(`{domain}`)"\n')
        lines.append(f"      service: {cid}\n")
    return "".join(lines)


def _render_services(running: list[dict]) -> str:
    if not running:
        return "    {}\n"
    lines = []
    for inst in running:
        cid = inst["container_name"]
        lines.append(f"    {cid}:\n")
        lines.append(f"      loadBalancer:\n")
        lines.append(f"        servers:\n")
        # Docker DNS resolves container name → internal IP :8000
        lines.append(f"          - url: http://{cid}:8000\n")
    return "".join(lines)


def _reload_traefik():
    """Restart Traefik to pick up new config."""
    subprocess.run(
        ["docker", "restart", TRAEFIK_CONTAINER],
        capture_output=True, text=True, timeout=10,
    )


def regenerate() -> int:
    """Rebuild dynamic config from running instances and restart Traefik."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT container_name, domain, path_prefix FROM instances WHERE status = 'running'"
        ).fetchall()
    running = [dict(r) for r in rows]

    yaml = (
        "http:\n"
        "  routers:\n"
        f"{_render_routers(running)}"
        "  services:\n"
        f"{_render_services(running)}"
    )
    TRAEFIK_DYNAMIC_CONFIG.write_text(yaml, encoding="utf-8")
    _reload_traefik()
    return len(running)
