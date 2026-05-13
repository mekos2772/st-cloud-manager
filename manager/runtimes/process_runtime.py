"""Process runtime adapter — wraps process_service as a RuntimeAdapter.

No hasattr() guards. Every method the protocol requires is present.
"""
from __future__ import annotations

from manager import process_service


class ProcessRuntime:
    """Full RuntimeAdapter implementation backed by native Node.js processes."""

    @staticmethod
    def supports_trial_isolation() -> bool:
        return False  # Processes share the host — no cgroup isolation

    @staticmethod
    def supports_resource_limits() -> bool:
        return False  # No memory/CPU enforcement in process mode

    @staticmethod
    def supports_dynamic_port_allocation() -> bool:
        return True

    # ── lifecycle ──

    def create_container(self, **kwargs) -> bool:
        return process_service.create_container(**kwargs)

    def stop_container(self, name: str) -> bool:
        return process_service.stop_container(name)

    def start_container(self, name: str) -> bool:
        return process_service.start_container(name)

    def restart_container(self, name: str) -> bool:
        return process_service.restart_container(name)

    def remove_container(self, name: str) -> bool:
        return process_service.remove_container(name)

    # ── introspection ──

    def health_check_container(self, domain: str, timeout: int = 60, path_prefix: str = "") -> bool:
        return process_service.health_check_container(domain, timeout, path_prefix)

    def get_logs(self, instance_id: str, tail: int = 100) -> str:
        return process_service.get_logs(instance_id, tail)

    def inspect_container(self, name: str) -> dict:
        return process_service.inspect_container(name)

    def process_exists(self, instance_id: str) -> bool:
        return process_service.process_exists(instance_id)

    def security_audit(self) -> dict:
        return process_service.security_audit()

    def get_container_ip(self, name: str) -> str | None:
        return process_service.get_container_ip(name)

    def get_container_port(self, instance_id: str) -> int | None:
        return process_service.get_container_port(instance_id)
