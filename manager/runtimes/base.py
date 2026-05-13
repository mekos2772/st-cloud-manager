"""Runtime adapter contract.

Upper-layer services depend on this interface, not on docker/process internals.
Both DockerRuntime and ProcessRuntime implement this contract.
Business code asks the runtime its capabilities, never checks hasattr().
"""
from __future__ import annotations
from typing import Protocol, Any


class RuntimeAdapter(Protocol):
    """Unified protocol that all runtime implementations must satisfy.

    Methods are split into two groups:
    - REQUIRED: every runtime must implement
    - OPTIONAL (capability flags): runtimes declare what they can do
    """

    # ── REQUIRED: lifecycle ──
    def create_container(
        self,
        *,
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
    ) -> bool: ...

    def stop_container(self, name: str) -> bool: ...

    def start_container(self, name: str) -> bool: ...

    def restart_container(self, name: str) -> bool: ...

    def remove_container(self, name: str) -> bool: ...

    # ── REQUIRED: introspection ──
    def health_check_container(self, domain: str, timeout: int = 60, path_prefix: str = "") -> bool: ...

    def get_logs(self, instance_id: str, tail: int = 100) -> str: ...

    def inspect_container(self, name: str) -> dict[str, Any]: ...

    def process_exists(self, instance_id: str) -> bool: ...

    def security_audit(self) -> dict: ...

    def get_container_ip(self, name: str) -> str | None: ...

    def get_container_port(self, instance_id: str) -> int | None: ...

    # ── CAPABILITY FLAGS (subclass overrides) ──

    @staticmethod
    def supports_trial_isolation() -> bool:
        """Docker mode isolates CPU/memory per container. Process mode shares the host."""
        return False

    @staticmethod
    def supports_resource_limits() -> bool:
        """Docker mode enforces memory/CPU/PID limits. Process mode does not."""
        return False

    @staticmethod
    def supports_dynamic_port_allocation() -> bool:
        """Process mode assigns ports from a range. Docker mode uses Traefik/nginx."""
        return False
