"""E2E: Runtime capability flags — docker vs process mode.

Verifies that RuntimeAdapter subclasses correctly report:
  - supports_trial_isolation()
  - supports_resource_limits()
  - supports_dynamic_port_allocation()

And that trial_service respects these flags.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root))


@pytest.fixture(autouse=True)
def _ensure_db():
    from manager.db import init_db
    init_db()


class TestRuntimeCapabilities:
    """Deterministic checks on runtime capability flags."""

    def test_docker_runtime_flags(self):
        from manager.runtimes.docker_runtime import DockerRuntime
        r = DockerRuntime()
        assert r.supports_trial_isolation() is True, "Docker must support trial isolation"
        assert r.supports_resource_limits() is True, "Docker must support resource limits"
        assert r.supports_dynamic_port_allocation() is False, "Docker uses Traefik labels, not port range"

    def test_process_runtime_flags(self):
        from manager.runtimes.process_runtime import ProcessRuntime
        r = ProcessRuntime()
        assert r.supports_trial_isolation() is False, "Process mode shares host — no isolation"
        assert r.supports_resource_limits() is False, "Process mode has no mem/cpu enforcement"
        assert r.supports_dynamic_port_allocation() is True, "Process mode uses port range"

    def test_router_service_returns_correct_runtime(self):
        from manager.router_service import effective_runtime_mode, get_runtime_service
        from manager.runtimes.base import RuntimeAdapter

        mode = effective_runtime_mode()
        runtime = get_runtime_service()
        assert isinstance(runtime, RuntimeAdapter.__class__) or hasattr(runtime, "create_container"), \
            f"get_runtime_service() must return a RuntimeAdapter, got {type(runtime)}"

        if mode == "docker":
            from manager.runtimes.docker_runtime import DockerRuntime
            assert isinstance(runtime, DockerRuntime), f"Expected DockerRuntime in docker mode, got {type(runtime)}"
        else:
            from manager.runtimes.process_runtime import ProcessRuntime
            assert isinstance(runtime, ProcessRuntime), f"Expected ProcessRuntime in process mode, got {type(runtime)}"

    def test_runtime_has_all_required_methods(self):
        """Every method in RuntimeAdapter Protocol must exist on actual runtimes."""
        required = [
            "create_container", "stop_container", "start_container",
            "restart_container", "remove_container", "health_check_container",
            "get_logs", "inspect_container", "process_exists",
            "security_audit", "get_container_ip", "get_container_port",
        ]
        from manager.runtimes.docker_runtime import DockerRuntime
        from manager.runtimes.process_runtime import ProcessRuntime

        for cls in (DockerRuntime, ProcessRuntime):
            instance = cls()
            for method in required:
                assert hasattr(instance, method), f"{cls.__name__} missing method: {method}"

    def test_trial_service_respects_isolation_flag(self):
        """trial_queue_status must report weak_isolation in process mode."""
        from manager.router_service import get_runtime_service
        runtime = get_runtime_service()
        weak = not runtime.supports_trial_isolation()

        from manager.services.trial_service import get_trial_queue_status
        status = get_trial_queue_status()

        assert status["weak_isolation"] == weak, \
            f"trial_queue_status.weak_isolation={status['weak_isolation']}, runtime says {weak}"

    def test_trial_effective_max_clamped_in_process_mode(self):
        """In process mode (no isolation), max trials should be clamped."""
        from manager.router_service import get_runtime_service
        runtime = get_runtime_service()

        from manager.services.trial_service import _effective_trial_max
        effective = _effective_trial_max()

        if not runtime.supports_trial_isolation():
            assert effective <= 2, f"Process mode trial max must be <= 2, got {effective}"
        else:
            # Docker mode — can be anything, just check it's positive
            assert effective > 0, f"Docker mode trial max must be > 0, got {effective}"

    def test_no_hasattr_in_business_code(self):
        """Business layer must not use hasattr() to probe runtime capabilities."""
        import ast
        business_files = [
            "manager/services/instance_orchestrator.py",
            "manager/services/trial_service.py",
            "manager/routes/admin.py",
            "manager/routes/public.py",
            "manager/routes/proxy.py",
        ]
        for path in business_files:
            full = _project_root / path
            if not full.exists():
                continue
            tree = ast.parse(full.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hasattr":
                    # Only flag hasattr called on 'svc' or runtime objects
                    if len(node.args) >= 1:
                        arg = ast.dump(node.args[0])
                        if any(kw in arg for kw in ("svc", "runtime", "_get_runtime")):
                            raise AssertionError(
                                f"{path}: hasattr() found on runtime object — use capability flags instead"
                            )
