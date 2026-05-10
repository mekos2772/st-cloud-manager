"""Docker container operations with security hardening.

User containers are locked down:
- No privileged mode, no host network/pid/ipc
- No Docker socket or sensitive host mounts
- Capabilities dropped, no-new-privileges
- Memory/CPU/pid limits enforced
- Read-only rootfs with ephemeral /tmp (configurable)
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from manager.config import PUBLIC_SCHEME, BASE_DIR

# Allowed mount targets — anything else is rejected
ALLOWED_MOUNTS = {
    "/home/node/app/config",
    "/home/node/app/data",
    "/home/node/app/plugins",
}

# Sensitive host paths that must never be mounted
FORBIDDEN_HOST_PATHS = {"/", "/root", "/etc", "/proc", "/sys", "/dev", "/home", "/var/run", "/tmp", "/boot"}

# Security defaults
DEFAULT_MEMORY = os.getenv("ST_DOCKER_MEMORY", "512m")
DEFAULT_MEMORY_SWAP = os.getenv("ST_DOCKER_MEMORY_SWAP", "768m")
DEFAULT_CPUS = os.getenv("ST_DOCKER_CPUS", "0.5")
DEFAULT_PIDS_LIMIT = os.getenv("ST_DOCKER_PIDS_LIMIT", "128")
READ_ONLY_ROOTFS = os.getenv("ST_CONTAINER_READ_ONLY", "true").lower() in ("true", "1", "yes")
TRIAL_MEMORY = os.getenv("ST_TRIAL_MEMORY", "256m")
TRIAL_MEMORY_SWAP = os.getenv("ST_TRIAL_MEMORY_SWAP", "384m")
NODE_MAX_HEAP = os.getenv("ST_NODE_MAX_HEAP", "256")


def _validate_mounts(volumes: list[tuple[str, str]]) -> None:
    for host_path, container_path in volumes:
        # Check forbidden host paths
        host_abs = os.path.abspath(host_path).replace("\\", "/").lower()
        for forbidden in FORBIDDEN_HOST_PATHS:
            if host_abs == forbidden or host_abs.startswith(forbidden + "/") and forbidden != "/":
                # Allow paths under our own project directory
                if "/st-cloud-manager/" not in host_abs.replace("\\", "/"):
                    raise ValueError(f"Forbidden host mount: {host_path}")
        # Check allowed container targets
        if container_path not in ALLOWED_MOUNTS:
            raise ValueError(f"Mount target not allowed: {container_path} (allowed: {sorted(ALLOWED_MOUNTS)})")


def check_docker() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def container_exists(name: str) -> bool:
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", f"name={name}"],
        capture_output=True,
    )
    output = result.stdout.decode("utf-8", errors="replace").strip()
    return name in output.split("\n")


def create_container(
    container_name: str,
    domain: str,
    memory: str,
    network: str,
    image: str,
    entrypoint: str,
    cert_resolver: str,
    tls_enabled: bool,
    user_config_dir: str,
    user_data_dir: str,
    user_plugins_dir: str,
    routing_mode: str = "subdomain",
    path_prefix: str = "",
    base_domain: str = "",
    is_trial: bool = False,
) -> bool:
    # Validate mounts before creating container
    _validate_mounts([
        (user_config_dir, "/home/node/app/config"),
        (user_data_dir, "/home/node/app/data"),
        (user_plugins_dir, "/home/node/app/plugins"),
    ])

    # Trial instances get lower resource limits
    mem = TRIAL_MEMORY if is_trial else memory
    memswap = TRIAL_MEMORY_SWAP if is_trial else DEFAULT_MEMORY_SWAP
    cpus = DEFAULT_CPUS
    pids = DEFAULT_PIDS_LIMIT

    cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--restart", "unless-stopped",
        "--network", network,
        # Resource limits
        "--memory", mem,
        "--memory-swap", memswap,
        "--cpus", cpus,
        "--pids-limit", str(pids),
        "--ulimit", "nofile=4096:4096",
        # Limit Node.js heap to leave room for OS
        "--env", f"NODE_OPTIONS=--max-old-space-size={NODE_MAX_HEAP}",
        # Security hardening
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        # Volumes (only allowed mounts)
        "-v", f"{user_config_dir}:/home/node/app/config",
        "-v", f"{user_data_dir}:/home/node/app/data",
        "-v", f"{user_plugins_dir}:/home/node/app/plugins",
        # Traefik labels
        "--label", "traefik.enable=true",
    ]

    # Route rule differs by routing mode
    if routing_mode == "path" and path_prefix:
        # Path-based: Host(base_domain) && PathPrefix(/st-xxx)
        cmd.extend([
            "--label", f"traefik.http.routers.{container_name}.rule=Host(`{base_domain}`) && PathPrefix(`{path_prefix}`)",
            "--label", f"traefik.http.routers.{container_name}.entrypoints={entrypoint}",
            "--label", f"traefik.http.services.{container_name}.loadbalancer.server.port=8000",
            # Middleware to strip prefix before reaching container proxy
            "--label", f"traefik.http.middlewares.{container_name}-strip.stripprefix.prefixes={path_prefix}",
            "--label", f"traefik.http.routers.{container_name}.middlewares={container_name}-strip",
        ])
        # Mount proxy script and override entrypoint
        proxy_dir = BASE_DIR / "templates" / "proxy"
        cmd.extend([
            "-v", f"{proxy_dir}:/proxy:ro",
            "--entrypoint", "/proxy/entrypoint.sh",
            "-e", f"ST_PATH_PREFIX={path_prefix}",
        ])
    else:
        # Subdomain-based: Host(domain)
        cmd.extend([
            "--label", f"traefik.http.routers.{container_name}.rule=Host(`{domain}`)",
            "--label", f"traefik.http.routers.{container_name}.entrypoints={entrypoint}",
            "--label", f"traefik.http.services.{container_name}.loadbalancer.server.port=8000",
        ])

    # Read-only rootfs (optional — ST may fail to start)
    if READ_ONLY_ROOTFS:
        cmd.extend([
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
        ])

    if tls_enabled:
        cmd.extend([
            "--label", f"traefik.http.routers.{container_name}.tls=true",
            "--label", f"traefik.http.routers.{container_name}.tls.certresolver={cert_resolver}",
        ])

    cmd.append(image)
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"[ERROR] docker run failed: {result.stderr.decode('utf-8', errors='replace')}", file=sys.stderr)
        return False
    return True


def stop_container(name: str) -> bool:
    result = subprocess.run(["docker", "stop", name], capture_output=True, text=True)
    return result.returncode == 0


def start_container(name: str) -> bool:
    result = subprocess.run(["docker", "start", name], capture_output=True, text=True)
    return result.returncode == 0


def restart_container(name: str) -> bool:
    result = subprocess.run(["docker", "restart", name], capture_output=True, text=True)
    return result.returncode == 0


def remove_container(name: str) -> bool:
    result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
    return result.returncode == 0


def get_container_ip(name: str) -> str | None:
    result = subprocess.run(
        ["docker", "inspect", name, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
        capture_output=True, text=True,
    )
    ip = result.stdout.strip()
    return ip if ip else None


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
    """Audit all user containers for security risks."""
    containers = []
    risks = []

    # Get all st-* containers
    result = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", "name=st-"],
        capture_output=True,
    )
    output = result.stdout.decode("utf-8", errors="replace").strip()
    names = [n.strip() for n in output.split("\n") if n.strip()]

    for name in names:
        # Skip Traefik and Manager containers
        if name in ("st-traefik", "st-manager"):
            continue

        info = {}
        try:
            def _inspect_val(path: str, default=""):
                r = subprocess.run(
                    ["docker", "inspect", name, "--format", "{{" + path + "}}"],
                    capture_output=True, timeout=10,
                )
                if r.returncode != 0:
                    return default
                return r.stdout.decode("utf-8", errors="replace").strip()

            running = _inspect_val(".State.Running") == "true"
            privileged = _inspect_val(".HostConfig.Privileged") == "true"
            network_mode = _inspect_val(".HostConfig.NetworkMode")
            pid_mode = _inspect_val(".HostConfig.PidMode")
            ipc_mode = _inspect_val(".HostConfig.IpcMode")
            security_opts = _inspect_val(".HostConfig.SecurityOpt")
            cap_drop = _inspect_val(".HostConfig.CapDrop")
            memory = _inspect_val(".HostConfig.Memory")
            nano_cpus = _inspect_val(".HostConfig.NanoCpus")
            pids_limit = _inspect_val(".HostConfig.PidsLimit")
            mounts_raw = _inspect_val("range .Mounts}}{{.Destination}}|{{end}}")
            binds_raw = _inspect_val("range .HostConfig.Binds}}{{.}}{{end}}")
            networks_raw = _inspect_val("range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}")

            # Check mounts for docker.sock and non-allowed targets
            docker_sock_mount = "docker.sock" in mounts_raw.lower() or "docker.sock" in binds_raw.lower()
            sensitive_mounts = []
            for target in mounts_raw.replace("}}", "").split("|"):
                target = target.strip()
                if not target:
                    continue
                if target not in ALLOWED_MOUNTS:
                    sensitive_mounts.append(target)

            checks = {
                "container_name": name,
                "running": running,
                "privileged": privileged,
                "host_network": network_mode == "host",
                "host_pid": pid_mode == "host",
                "host_ipc": ipc_mode == "host",
                "docker_sock_mount": docker_sock_mount,
                "sensitive_mounts": sensitive_mounts,
                "missing_memory_limit": not memory or memory == "0",
                "missing_cpu_limit": not nano_cpus or nano_cpus == "0",
                "missing_pids_limit": not pids_limit or pids_limit == "0",
                "missing_no_new_priv": "no-new-privileges" not in security_opts,
                "missing_cap_drop": "ALL" not in cap_drop.upper(),
                "st_internal_network": "st_internal" in networks_raw,
            }

            # Determine if there are any risks
            risk_items = []
            if checks["privileged"]:
                risk_items.append("privileged=true — 容器有特权模式，可逃逸")
            if checks["host_network"]:
                risk_items.append("host network — 容器可访问宿主机网络栈")
            if checks["host_pid"]:
                risk_items.append("host pid — 容器可看到宿主机进程")
            if checks["host_ipc"]:
                risk_items.append("host ipc — 共享宿主机 IPC")
            if checks["docker_sock_mount"]:
                risk_items.append("Docker socket 挂载 — 容器可控制 Docker")
            if checks["sensitive_mounts"]:
                risk_items.append(f"敏感挂载: {', '.join(checks['sensitive_mounts'])}")
            if checks["missing_memory_limit"]:
                risk_items.append("缺少 memory 限制")
            if checks["missing_cpu_limit"]:
                risk_items.append("缺少 CPU 限制")
            if checks["missing_pids_limit"]:
                risk_items.append("缺少 PIDs 限制")
            if checks["missing_no_new_priv"]:
                risk_items.append("缺少 no-new-privileges")
            if checks["missing_cap_drop"]:
                risk_items.append("缺少 cap-drop ALL")
            if checks["st_internal_network"]:
                risk_items.append("加入了 st_internal 网络")

            checks["risks"] = risk_items
            checks["risk_level"] = "high" if len(risk_items) > 0 else "safe"

            # Sanitize string values for JSON (Windows paths)
            for k, v in checks.items():
                if isinstance(v, str):
                    checks[k] = v.replace("\\", "/")
                elif isinstance(v, list):
                    checks[k] = [x.replace("\\", "/") if isinstance(x, str) else x for x in v]

            containers.append(checks)

        except Exception as e:
            containers.append({"container_name": name, "error": str(e), "risk_level": "error"})

    high_risk = [c for c in containers if c.get("risk_level") == "high"]
    return {
        "total_containers": len(containers),
        "risk_count": len(high_risk),
        "safe_count": len([c for c in containers if c.get("risk_level") == "safe"]),
        "containers": containers,
        "advice": [
            "确保所有用户容器使用 --security-opt no-new-privileges:true",
            "确保所有用户容器使用 --cap-drop ALL",
            "确保所有用户容器不挂载 /var/run/docker.sock",
            "确保所有用户容器不加入 st_internal 网络",
            "确保所有用户容器有 memory/cpu/pids 限制",
        ] if high_risk else [],
    }
