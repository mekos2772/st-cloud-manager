"""Process-based instance management (Dockerless mode).

Each ST instance runs as a Node.js child process on a unique port.
Nginx handles reverse-proxy routing. Processes are tracked via PID files.
"""
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from manager.config import PUBLIC_SCHEME, BASE_DIR

# Allowed mount targets (not used in process mode, kept for interface compat)
ALLOWED_MOUNTS = {
    "/home/node/app/config",
    "/home/node/app/data",
    "/home/node/app/plugins",
}

DEFAULT_PORT_START = int(os.getenv("ST_PORT_RANGE_START", "9000"))
DEFAULT_PORT_END = int(os.getenv("ST_PORT_RANGE_END", "9999"))
NODE_BIN = os.getenv("ST_NODE_BIN", "node")
NODE_MAX_HEAP = os.getenv("ST_NODE_MAX_HEAP", "256")


def _pid_file(instance_id: str) -> Path:
    return BASE_DIR / "users" / instance_id / ".st_pid"


def _port_file(instance_id: str) -> Path:
    return BASE_DIR / "users" / instance_id / ".st_port"


def _next_available_port(used_ports: set[int]) -> int:
    for port in range(DEFAULT_PORT_START, DEFAULT_PORT_END + 1):
        if port not in used_ports:
            return port
    raise RuntimeError(f"No available ports in range {DEFAULT_PORT_START}-{DEFAULT_PORT_END}")


def _get_used_ports() -> set[int]:
    """Collect ports from all running process-mode instances."""
    used = set()
    users_dir = BASE_DIR / "users"
    if users_dir.exists():
        for pf in users_dir.rglob(".st_port"):
            try:
                port = int(pf.read_text().strip())
                used.add(port)
            except (ValueError, OSError):
                pass
    return used


def _ensure_symlink_targets(instance_dir: Path):
    """Share ST source code. Directories are symlinked; small root files are
    copied to avoid CWD-resolution issues on Windows (Node follows symlinks)."""
    st_release = Path(os.getenv("ST_RELEASE_DIR", str(BASE_DIR / "st-release")))
    if not st_release.exists():
        return

    import shutil

    # Files that MUST be regular files (not symlinks) so Node CWD stays correct
    COPY_FILES = {"server.js", "package.json", "package-lock.json", "webpack.config.js",
                  "plugins.js", "recover.js", "jsconfig.json", "index.d.ts",
                  ".eslintrc.cjs", ".npmrc", ".editorconfig"}

    for item in st_release.iterdir():
        target = instance_dir / item.name
        if item.name in ("config", "data", "plugins"):
            continue
        if item.is_dir():
            # Directories: symlink (Node can follow dir symlinks for require/static)
            if not target.exists():
                try:
                    target.symlink_to(item, target_is_directory=True)
                except OSError:
                    pass
        elif item.name in COPY_FILES:
            # Must-copy: remove any stale symlink first, then copy
            if target.is_symlink() or (target.exists() and target.stat().st_size == 0):
                target.unlink(missing_ok=True)
            if not target.exists():
                try:
                    shutil.copy2(str(item), str(target))
                except OSError:
                    pass
        elif not target.exists():
            # Other files: symlink is fine for optional files
            try:
                target.symlink_to(item)
            except OSError:
                pass


def process_exists(instance_id: str) -> bool:
    pf = _pid_file(instance_id)
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, ProcessLookupError):
        return False


def create_container(
    container_name: str,
    domain: str,
    memory: str = "",
    network: str = "",
    image: str = "",
    entrypoint: str = "",
    cert_resolver: str = "",
    tls_enabled: bool = False,
    user_config_dir: str = "",
    user_data_dir: str = "",
    user_plugins_dir: str = "",
    routing_mode: str = "subdomain",
    path_prefix: str = "",
    base_domain: str = "",
    is_trial: bool = False,
) -> bool:
    """Start ST as a Node.js process with a unique port."""
    instance_id = container_name.replace("st-", "")
    instance_dir = BASE_DIR / "users" / instance_id

    if not instance_dir.exists():
        print(f"[ERROR] Instance directory not found: {instance_dir}", file=sys.stderr)
        return False

    _ensure_symlink_targets(instance_dir)

    used = _get_used_ports()
    st_port = _next_available_port(used)
    used.add(st_port)

    # Path mode: need a proxy to rewrite absolute URLs in ST responses
    use_proxy = bool(path_prefix)
    proxy_port = st_port
    if use_proxy:
        proxy_port = st_port
        st_port = _next_available_port(used)

    # Write ST's internal port into config.yaml
    config_yaml = instance_dir / "config" / "config.yaml"
    if config_yaml.exists():
        content = config_yaml.read_text(encoding="utf-8")
        content = content.replace("port: 8000", f"port: {st_port}")
        content = content.replace("port: 8001", f"port: {st_port}")
        config_yaml.write_text(content, encoding="utf-8")

    # config.yaml symlink (matches ST Dockerfile convention)
    root_config = instance_dir / "config.yaml"
    if root_config.is_symlink():
        root_config.unlink()
    if not root_config.exists():
        try:
            root_config.symlink_to(config_yaml)
        except OSError:
            import shutil
            shutil.copy2(str(config_yaml), str(root_config))

    env = os.environ.copy()
    env["NODE_OPTIONS"] = f"--max-old-space-size={NODE_MAX_HEAP}"

    # Start ST on internal port
    try:
        st_proc = subprocess.Popen(
            [NODE_BIN, "server.js"],
            cwd=str(instance_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        print(f"[ERROR] Failed to start ST: {e}", file=sys.stderr)
        return False

    # For path routing: start proxy.js to rewrite absolute URLs in responses
    if use_proxy:
        proxy_js = BASE_DIR / "templates" / "proxy" / "proxy.js"
        if proxy_js.exists():
            penv = env.copy()
            penv["ST_PATH_PREFIX"] = path_prefix
            penv["ST_PORT"] = str(st_port)
            try:
                subprocess.Popen(
                    [NODE_BIN, str(proxy_js)],
                    cwd=str(instance_dir),
                    env=penv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except Exception as e:
                print(f"[ERROR] Failed to start proxy: {e}", file=sys.stderr)
                # Proxy failed but ST is running — continue without rewrite
        else:
            print(f"[WARN] proxy.js not found at {proxy_js}, path rewriting disabled", file=sys.stderr)

    _pid_file(instance_id).write_text(str(st_proc.pid))
    _port_file(instance_id).write_text(str(proxy_port))
    return True


def stop_container(name: str) -> bool:
    instance_id = name.replace("st-", "")
    pf = _pid_file(instance_id)
    if not pf.exists():
        return True  # already stopped
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        pf.unlink(missing_ok=True)
        return True
    except (ValueError, OSError, ProcessLookupError):
        pf.unlink(missing_ok=True)
        return True


def start_container(name: str) -> bool:
    """Re-start a stopped instance. Re-reads port from stored file."""
    instance_id = name.replace("st-", "")
    instance_dir = BASE_DIR / "users" / instance_id
    if not instance_dir.exists():
        return False

    # Get path_prefix from DB
    from manager.db import get_db
    path_prefix = ""
    with get_db() as conn:
        row = conn.execute(
            "SELECT path_prefix FROM instances WHERE instance_id=? AND status='running'",
            (instance_id,),
        ).fetchone()
    if row and row["path_prefix"]:
        path_prefix = row["path_prefix"]

    _ensure_symlink_targets(instance_dir)

    pf = _port_file(instance_id)
    if pf.exists():
        proxy_port = int(pf.read_text().strip())
    else:
        used = _get_used_ports()
        proxy_port = _next_available_port(used)

    use_proxy = bool(path_prefix)
    st_port = proxy_port
    if use_proxy:
        used = _get_used_ports()
        used.add(proxy_port)
        st_port = _next_available_port(used)

    config_yaml = instance_dir / "config" / "config.yaml"
    if config_yaml.exists():
        content = config_yaml.read_text(encoding="utf-8")
        content = content.replace("port: 8000", f"port: {st_port}")
        content = content.replace("port: 8001", f"port: {st_port}")
        config_yaml.write_text(content, encoding="utf-8")

    env = os.environ.copy()
    env["NODE_OPTIONS"] = f"--max-old-space-size={NODE_MAX_HEAP}"

    try:
        proc = subprocess.Popen(
            [NODE_BIN, "server.js"],
            cwd=str(instance_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        print(f"[ERROR] start failed: {e}", file=sys.stderr)
        return False

    if use_proxy:
        proxy_js = BASE_DIR / "templates" / "proxy" / "proxy.js"
        if proxy_js.exists():
            penv = env.copy()
            penv["ST_PATH_PREFIX"] = path_prefix
            penv["ST_PORT"] = str(st_port)
            subprocess.Popen(
                [NODE_BIN, str(proxy_js)],
                cwd=str(instance_dir),
                env=penv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    _pid_file(instance_id).write_text(str(proc.pid))
    _port_file(instance_id).write_text(str(proxy_port))
    return True


def restart_container(name: str) -> bool:
    stop_container(name)
    time.sleep(0.5)
    return start_container(name)


def remove_container(name: str) -> bool:
    return stop_container(name)


def get_container_ip(name: str) -> str | None:
    return "127.0.0.1"


def get_container_port(instance_id: str) -> int | None:
    pf = _port_file(instance_id)
    if pf.exists():
        try:
            return int(pf.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def health_check_container(domain: str, timeout: int = 60, path_prefix: str = "") -> bool:
    if path_prefix:
        url = f"{PUBLIC_SCHEME}://{domain}{path_prefix}"
    else:
        url = f"{PUBLIC_SCHEME}://{domain}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=3)
            if resp.status in (200, 302, 401):
                return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 302):
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def security_audit() -> dict:
    users_dir = BASE_DIR / "users"
    containers = []
    risks = []

    if users_dir.exists():
        for pf in users_dir.rglob(".st_pid"):
            instance_id = pf.parent.name
            alive = False
            try:
                pid = int(pf.read_text().strip())
                os.kill(pid, 0)
                alive = True
            except (ValueError, OSError):
                pass

            containers.append({
                "container_name": f"st-{instance_id}",
                "running": alive,
                "risk_level": "safe",
                "risks": [],
            })

    return {
        "total_containers": len(containers),
        "risk_count": 0,
        "safe_count": len(containers),
        "containers": containers,
        "advice": [],
    }
