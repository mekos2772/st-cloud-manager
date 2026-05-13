"""Nginx reverse-proxy config generator (Dockerless mode).

Writes per-instance nginx configs and reloads nginx.
Supports both subdomain and path-based routing.
"""
import os
import subprocess
from pathlib import Path
from manager.db import get_db
from manager.instance_model import normalize_path_prefix, route_base_domain, with_access_url

NGINX_CONF_DIR = Path(os.getenv("ST_NGINX_CONF_DIR", "/etc/nginx"))
NGINX_SITES_DIR = NGINX_CONF_DIR / os.getenv("ST_NGINX_SITES_SUBDIR", "sites-enabled")
NGINX_BIN = os.getenv("ST_NGINX_BIN", "nginx")
NGINX_UPSTREAM_PORT = int(os.getenv("ST_NGINX_UPSTREAM_PORT", "8000"))


def _site_path(container_name: str) -> Path:
    return NGINX_SITES_DIR / f"st-{container_name.replace('st-', '')}.conf"


def _path_site_path(base_domain: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in base_domain)
    return NGINX_SITES_DIR / f"st-path-{safe}.conf"


def _reload_nginx():
    # Nginx needs its prefix directory to resolve relative paths like logs/error.log.
    # On Windows (winget install), nginx's own prefix may differ from the CWD.
    nginx_args = [NGINX_BIN, "-s", "reload"]
    env = os.environ.copy()
    if NGINX_CONF_DIR.exists():
        nginx_args = [NGINX_BIN, "-p", str(NGINX_CONF_DIR), "-s", "reload"]
    try:
        result = subprocess.run(nginx_args, capture_output=True, text=True, timeout=10,
                                cwd=str(NGINX_CONF_DIR) if NGINX_CONF_DIR.exists() else None)
        if result.returncode != 0:
            print(f"[nginx] reload failed (rc={result.returncode}): {result.stderr[:200]}")
    except FileNotFoundError:
        print(f"[nginx] binary not found: {NGINX_BIN}")
    except Exception as e:
        print(f"[nginx] reload error: {e}")


def _render_subdomain(inst: dict) -> str:
    cid = inst["container_name"]
    domain = inst["domain"]
    tls = os.getenv("ST_TLS_ENABLED", "true").lower() in ("true", "1", "yes")
    ssl_cert = os.getenv("ST_SSL_CERT", "/etc/ssl/certs/st.pem")
    ssl_key = os.getenv("ST_SSL_KEY", "/etc/ssl/private/st.key")

    lines = [f"# {cid}"]
    port = "443" if tls else "80"
    listen = f"listen {port} ssl;" if tls else f"listen {port};"

    lines.append("server {")
    lines.append(f"    server_name {domain};")
    lines.append(f"    {listen}")
    if tls:
        lines.append(f"    ssl_certificate {ssl_cert};")
        lines.append(f"    ssl_certificate_key {ssl_key};")
    lines.append("")
    lines.append("    location / {")
    lines.append("        proxy_pass http://127.0.0.1:$st_port;")
    lines.append("        proxy_http_version 1.1;")
    lines.append('        proxy_set_header Upgrade $http_upgrade;')
    lines.append('        proxy_set_header Connection "upgrade";')
    lines.append('        proxy_set_header Host $host;')
    lines.append('        proxy_set_header X-Real-IP $remote_addr;')
    lines.append('        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;')
    lines.append('        proxy_set_header X-Forwarded-Proto $scheme;')
    lines.append('        proxy_set_header Authorization $http_authorization;')
    lines.append('        proxy_read_timeout 86400s;')
    lines.append('        proxy_send_timeout 86400s;')
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines)


def _render_path_group(base_domain: str, instances: list[dict], port_map: dict[str, int]) -> str:
    tls = os.getenv("ST_TLS_ENABLED", "true").lower() in ("true", "1", "yes")
    ssl_cert = os.getenv("ST_SSL_CERT", "/etc/ssl/certs/st.pem")
    ssl_key = os.getenv("ST_SSL_KEY", "/etc/ssl/private/st.key")
    port = "443" if tls else "80"
    listen = f"listen {port} ssl;" if tls else f"listen {port};"

    lines = [f"# path routes for {base_domain}"]
    lines.append("server {")
    lines.append(f"    server_name {base_domain};")
    lines.append(f"    {listen}")
    if tls:
        lines.append(f"    ssl_certificate {ssl_cert};")
        lines.append(f"    ssl_certificate_key {ssl_key};")
    lines.append("")

    for inst in sorted(instances, key=lambda x: x.get("path_prefix", "")):
        cid = inst["container_name"]
        iid = cid.replace("st-", "")
        path_prefix = normalize_path_prefix(inst.get("path_prefix", ""))
        actual_port = port_map.get(iid, 8000)
        if not path_prefix:
            continue

        lines.append(f"    # {cid}")
        lines.append(f"    location = {path_prefix} {{")
        lines.append(f"        return 301 {path_prefix}/;")
        lines.append("    }")
        lines.append("")
        lines.append(f"    location {path_prefix}/ {{")
        lines.append(f"        proxy_pass http://127.0.0.1:{actual_port};")
        lines.append("        proxy_http_version 1.1;")
        lines.append('        proxy_set_header Upgrade $http_upgrade;')
        lines.append('        proxy_set_header Connection "upgrade";')
        lines.append('        proxy_set_header Host $host;')
        lines.append('        proxy_set_header X-Real-IP $remote_addr;')
        lines.append('        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;')
        lines.append('        proxy_set_header X-Forwarded-Proto $scheme;')
        lines.append('        proxy_set_header Authorization $http_authorization;')
        lines.append('        proxy_read_timeout 86400s;')
        lines.append('        proxy_send_timeout 86400s;')
        lines.append("    }")
        lines.append("")

    lines.append("}")
    return "\n".join(lines)


def regenerate() -> int:
    """Rebuild nginx configs from running instances and reload."""
    if not NGINX_SITES_DIR.exists():
        NGINX_SITES_DIR.mkdir(parents=True, exist_ok=True)

    with get_db() as conn:
        rows = conn.execute(
            "SELECT container_name, domain, path_prefix FROM instances WHERE status = 'running'"
        ).fetchall()
    running = [with_access_url(dict(r)) for r in rows]

    # Read port mapping files to build a set of ports
    users_dir = Path(os.getenv("ST_USERS_DIR", "users"))
    if not users_dir.is_absolute():
        users_dir = Path(os.getenv("ST_BASE_DIR", ".")) / users_dir

    # Build instance -> port map
    port_map = {}
    for inst in running:
        iid = inst["container_name"].replace("st-", "")
        pf = users_dir / iid / ".st_port"
        if pf.exists():
            try:
                port_map[iid] = int(pf.read_text().strip())
            except (ValueError, OSError):
                pass

    path_groups = {}
    subdomain_instances = []
    for inst in running:
        if inst.get("path_prefix"):
            path_groups.setdefault(route_base_domain(inst), []).append(inst)
        else:
            subdomain_instances.append(inst)

    # Write path-mode configs grouped by base domain. Nginx only uses one
    # server block per server_name/listen pair, so separate files per path can
    # make newly-created instances 404.
    written = set()
    for base_domain, instances in path_groups.items():
        site_path = _path_site_path(base_domain)
        config = _render_path_group(base_domain, instances, port_map)
        if site_path.exists() and site_path.read_text() == config:
            written.add(str(site_path))
            continue

        site_path.write_text(config, encoding="utf-8")
        written.add(str(site_path))

    # Write subdomain per-instance configs.
    for inst in subdomain_instances:
        cid = inst["container_name"]
        site_path = _site_path(cid)
        config = _render_subdomain(inst)

        # Inject actual port
        iid = cid.replace("st-", "")
        actual_port = port_map.get(iid, 8000)
        config = config.replace("$st_port", str(actual_port))

        # Only write if changed
        if site_path.exists() and site_path.read_text() == config:
            written.add(str(site_path))
            continue

        site_path.write_text(config, encoding="utf-8")
        written.add(str(site_path))

    # Remove stale configs
    for f in NGINX_SITES_DIR.glob("st-*.conf"):
        if str(f) not in written:
            f.unlink(missing_ok=True)

    _reload_nginx()
    return len(running)
