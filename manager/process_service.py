"""Process-based instance management (Dockerless mode).

Each ST instance runs as a Node.js child process on a unique port.
Nginx handles reverse-proxy routing. Processes are tracked via PID files.
"""
import os
import re
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


def _port_lock():
    """Deduplicate port allocation across concurrent create requests."""
    import threading
    return threading.Lock()

_port_alloc_lock = _port_lock()


def _ensure_symlink_targets(instance_dir: Path):
    """Copy ST source into instance directory. No symlinks — ST's import.meta.dirname
    follows symlinks, causing all instances to share CWD and data."""
    st_release = Path(os.getenv("ST_RELEASE_DIR", str(BASE_DIR / "st-release")))
    if not st_release.exists():
        return

    import shutil

    SKIP_NAMES = {"config", "data", "plugins", "config.yaml", ".st_pid", ".st_port"}

    for item in st_release.iterdir():
        target = instance_dir / item.name
        if item.name in SKIP_NAMES:
            continue
        if target.exists():
            continue
        try:
            if item.is_dir():
                shutil.copytree(str(item), str(target), symlinks=False)
            else:
                shutil.copy2(str(item), str(target))
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

    with _port_alloc_lock:
        used = _get_used_ports()
        st_port = _next_available_port(used)
        used.add(st_port)

        # Path mode: need a proxy to rewrite absolute URLs in ST responses
        use_proxy = bool(path_prefix)
        proxy_port = st_port
        if use_proxy:
            proxy_port = st_port
            st_port = _next_available_port(used)
            used.add(st_port)

        # Write port file IMMEDIATELY so concurrent allocations see it
        _port_file(instance_id).write_text(str(proxy_port))

    # Write ST's internal port into config.yaml
    config_yaml = instance_dir / "config" / "config.yaml"
    if config_yaml.exists():
        content = config_yaml.read_text(encoding="utf-8")
        content = re.sub(r'^port:\s*\d+', f'port: {st_port}', content, flags=re.MULTILINE)
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
            try:
                px = subprocess.Popen(
                    [NODE_BIN, str(proxy_js), instance_id, str(proxy_port), str(st_port), path_prefix],
                    cwd=str(instance_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                (instance_dir / ".st_proxy_pid").write_text(str(px.pid))
            except Exception as e:
                print(f"[ERROR] Failed to start proxy: {e}", file=sys.stderr)
        else:
            print(f"[WARN] proxy.js not found at {proxy_js}, path rewriting disabled", file=sys.stderr)

    _pid_file(instance_id).write_text(str(st_proc.pid))
    return True


def _kill_pid_file(pf: Path):
    if not pf.exists():
        return
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    except (ValueError, OSError, ProcessLookupError):
        pass
    finally:
        pf.unlink(missing_ok=True)


def stop_container(name: str) -> bool:
    instance_id = name.replace("st-", "")
    instance_dir = BASE_DIR / "users" / instance_id

    # Kill proxy first (so no new requests reach ST), then ST
    _kill_pid_file(instance_dir / ".st_proxy_pid")
    _kill_pid_file(_pid_file(instance_id))
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
            "SELECT path_prefix FROM instances WHERE instance_id=?",
            (instance_id,),
        ).fetchone()
    if row and row["path_prefix"]:
        path_prefix = row["path_prefix"]

    _ensure_symlink_targets(instance_dir)

    pf = _port_file(instance_id)
    if pf.exists():
        proxy_port = int(pf.read_text().strip())
    else:
        with _port_alloc_lock:
            used = _get_used_ports()
            proxy_port = _next_available_port(used)

    use_proxy = bool(path_prefix)
    st_port = proxy_port
    if use_proxy:
        with _port_alloc_lock:
            used = _get_used_ports()
            used.add(proxy_port)
            st_port = _next_available_port(used)
            used.add(st_port)
            _port_file(instance_id).write_text(str(proxy_port))

    config_yaml = instance_dir / "config" / "config.yaml"
    if config_yaml.exists():
        content = config_yaml.read_text(encoding="utf-8")
        content = re.sub(r'^port:\s*\d+', f'port: {st_port}', content, flags=re.MULTILINE)
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
            px = subprocess.Popen(
                [NODE_BIN, str(proxy_js), instance_id, str(proxy_port), str(st_port), path_prefix],
                cwd=str(instance_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            (instance_dir / ".st_proxy_pid").write_text(str(px.pid))

    _pid_file(instance_id).write_text(str(proc.pid))
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
