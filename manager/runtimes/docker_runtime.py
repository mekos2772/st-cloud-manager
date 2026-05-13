"""Docker runtime adapter — wraps docker_service as a RuntimeAdapter.

No hasattr() guards. Every method the protocol requires is present.
Methods docker_service doesn't have natively are provided inline (docker CLI).
"""
from __future__ import annotations

import subprocess
from manager import docker_service


class DockerRuntime:
    """Full RuntimeAdapter implementation backed by Docker."""

    @staticmethod
    def supports_trial_isolation() -> bool:
        return True

    @staticmethod
    def supports_resource_limits() -> bool:
        return True

    @staticmethod
    def supports_dynamic_port_allocation() -> bool:
        return False

    # ── lifecycle ──

    def create_container(self, **kwargs) -> bool:
        return docker_service.create_container(**kwargs)

    def stop_container(self, name: str) -> bool:
        return docker_service.stop_container(name)

    def start_container(self, name: str) -> bool:
        return docker_service.start_container(name)

    def restart_container(self, name: str) -> bool:
        return docker_service.restart_container(name)

    def remove_container(self, name: str) -> bool:
        return docker_service.remove_container(name)

    # ── introspection ──

    def health_check_container(self, domain: str, timeout: int = 60, path_prefix: str = "") -> bool:
        return docker_service.health_check_container(domain, timeout, path_prefix)

    def get_logs(self, instance_id: str, tail: int = 100) -> str:
        container = f"st-{instance_id}"
        result = subprocess.run(
            ["docker", "logs", container, "--tail", str(tail)],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout[-5000:] if result.stdout else "(no logs)"

    def inspect_container(self, name: str) -> dict:
        import json
        try:
            result = subprocess.run(
                ["docker", "inspect", name],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)[0]
                return {"running": data.get("State", {}).get("Running", False)}
        except Exception:
            pass
        return {"running": False}

    def process_exists(self, instance_id: str) -> bool:
        return docker_service.container_exists(f"st-{instance_id}")

    def security_audit(self) -> dict:
        return docker_service.security_audit()

    def get_container_ip(self, name: str) -> str | None:
        return docker_service.get_container_ip(name)

    def get_container_port(self, instance_id: str) -> int | None:
        return None  # Docker mode uses host network / Traefik labels
