"""Fake RuntimeAdapter — used by tests and mock-st E2E mode.

When ST_E2E_FAKE_SERVER=1, create_container starts a real FakeSTServer
HTTP server so path_proxy has a real target to proxy to.

All other methods are no-ops that return success values.
"""
from __future__ import annotations

import os
from typing import Any


class FakeE2ERuntime:
    """RuntimeAdapter that records operations and optionally hosts fake ST servers."""

    _use_fake_server = os.environ.get("ST_E2E_FAKE_SERVER", "0") == "1"

    def __init__(self):
        self.created: list[str] = []
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.restarted: list[str] = []
        self.removed: list[str] = []
        self._running: set[str] = set()
        self._ports: dict[str, int] = {}
        self._logs: dict[str, str] = {}
        self._port_counter = 9100
        self._servers: dict[str, Any] = {}

    @staticmethod
    def supports_trial_isolation() -> bool:
        return False

    @staticmethod
    def supports_resource_limits() -> bool:
        return False

    @staticmethod
    def supports_dynamic_port_allocation() -> bool:
        return True

    def create_container(self, **kwargs) -> bool:
        name = kwargs.get("container_name", "")
        self.created.append(name)
        self._running.add(name)
        inst_id = name.replace("st-", "", 1) if name.startswith("st-") else name
        self._port_counter += 1

        if self._use_fake_server:
            from tests.helpers.fake_st_server import FakeSTServer
            server = FakeSTServer()
            server.start()
            self._servers[inst_id] = server
            port = server.port
        else:
            port = self._port_counter

        self._ports[inst_id] = port
        from manager.config import BASE_DIR
        port_file = BASE_DIR / "users" / inst_id / ".st_port"
        port_file.parent.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(port))
        # Create required dirs so _wait_st_initialized returns immediately
        user_dir = BASE_DIR / "users" / inst_id
        (user_dir / "data" / "default-user").mkdir(parents=True, exist_ok=True)
        (user_dir / "config").mkdir(parents=True, exist_ok=True)
        (user_dir / "plugins").mkdir(parents=True, exist_ok=True)
        (user_dir / "config" / "config.yaml").write_text("listen: true")
        # _wait_st_initialized checks if default-user dir exists and is non-empty
        (user_dir / "data" / "default-user" / "settings.json").write_text("{}")
        return True

    def stop_container(self, name: str) -> bool:
        self.stopped.append(name)
        self._running.discard(name)
        return True

    def start_container(self, name: str) -> bool:
        self.started.append(name)
        self._running.add(name)
        return True

    def restart_container(self, name: str) -> bool:
        self.restarted.append(name)
        self._running.add(name)
        return True

    def remove_container(self, name: str) -> bool:
        self.removed.append(name)
        self._running.discard(name)
        inst_id = name.replace("st-", "", 1) if name.startswith("st-") else name
        if inst_id in self._servers:
            try:
                self._servers[inst_id].stop()
            except Exception:
                pass
            del self._servers[inst_id]
        return True

    def health_check_container(self, domain: str, timeout: int = 60, path_prefix: str = "") -> bool:
        return True

    def get_logs(self, instance_id: str, tail: int = 100) -> str:
        return self._logs.get(instance_id, "(no logs)")

    def inspect_container(self, name: str) -> dict:
        inst_id = name.replace("st-", "", 1) if name.startswith("st-") else name
        return {"running": name in self._running, "port": self._ports.get(inst_id, 0)}

    def process_exists(self, instance_id: str) -> bool:
        return instance_id in {n.replace("st-", "", 1) for n in self._running}

    def security_audit(self) -> dict:
        return {"containers": [], "risks": []}

    def get_container_ip(self, name: str) -> str | None:
        return "127.0.0.1"

    def get_container_port(self, instance_id: str) -> int | None:
        return self._ports.get(instance_id)

    @property
    def running_count(self) -> int:
        return len(self._running)
