"""Shared fixtures for smoke tests.

Mocks the runtime layer (Docker/process) so we can test
business logic and HTTP handlers without real containers.

The FakeRuntime used here is the same FakeE2ERuntime shared with
router_service for --mock-st E2E mode.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from tests.helpers.fake_runtime import FakeE2ERuntime


class FakeRuntime(FakeE2ERuntime):
    """Test-friendly subclass with the same behavior as FakeE2ERuntime."""
    pass


def _fake_regenerate():
    return 0


def _fake_proxy_key(instance_id: str) -> tuple[str, str]:
    import secrets, string
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"sk-st-{instance_id}-{suffix}", f"st-{instance_id}-{suffix}"


def _fake_wait_init(*args, **kwargs) -> bool:
    return True


def _setup_single_instance_dir(instance_id: str):
    """Create a minimal ST directory so path_proxy can find .st_port."""
    from manager.config import BASE_DIR
    user_dir = BASE_DIR / "users" / instance_id
    user_dir.mkdir(parents=True, exist_ok=True)
    (user_dir / "data" / "default-user").mkdir(parents=True, exist_ok=True)
    (user_dir / "config").mkdir(parents=True, exist_ok=True)
    (user_dir / "plugins").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def fake_runtime(monkeypatch):
    """Provide a FakeRuntime and patch it into the orchestrator and instance_service."""
    runtime = FakeRuntime()

    # Patch orchestrator-level helpers
    monkeypatch.setattr("manager.services.instance_orchestrator._get_runtime_svc", lambda: runtime)
    monkeypatch.setattr("manager.services.instance_orchestrator._wait_st_initialized", _fake_wait_init)
    monkeypatch.setattr("manager.services.instance_orchestrator._regenerate", _fake_regenerate)
    monkeypatch.setattr("manager.services.instance_orchestrator.create_proxy_key", _fake_proxy_key)
    monkeypatch.setattr("manager.services.instance_orchestrator.delete_proxy_key", lambda _: None)

    # instance_service is now a compatibility facade — no internal helpers to patch

    # Patch trial_service imports of orchestrator
    monkeypatch.setattr("manager.services.trial_service.create_trial_instance_raw",
                        lambda client_ip: _fake_trial_create(runtime, client_ip))

    # Patch resource checking to always pass
    monkeypatch.setattr("manager.resource_service.can_create_instance",
                        lambda *a, **kw: (True, "ok"))

    # Patch router_service so admin.py / security audit uses FakeRuntime
    monkeypatch.setattr("manager.router_service.get_runtime_service", lambda: runtime)

    return runtime


def _fake_trial_create(runtime: FakeRuntime, client_ip: str) -> dict:
    """Fake trial creation that uses the orchestrator but avoids real wait."""
    from manager.services.instance_orchestrator import create_trial_instance_raw
    return create_trial_instance_raw(client_ip)


@pytest.fixture(autouse=True)
def clean_users_and_archive():
    """Clean test artifact directories before and after each test."""
    from manager.config import USERS_DIR, ARCHIVE_DIR
    import shutil

    dirs_to_clean = [USERS_DIR, ARCHIVE_DIR]
    for d in dirs_to_clean:
        if d.exists():
            for child in d.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(str(child), ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except Exception:
                    pass
    yield
    for d in dirs_to_clean:
        if d.exists():
            for child in d.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(str(child), ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                except Exception:
                    pass


@pytest.fixture
def test_db():
    """Use a temp SQLite DB for tests."""
    from manager.config import DB_PATH as _ORIG_DB_PATH
    import manager.config
    import manager.db

    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_db_path = Path(tmp_path)
    test_db_path.unlink(missing_ok=True)

    manager.config.DB_PATH = test_db_path
    # Also patch the DB_PATH that db.py captured at import time
    manager.db.DB_PATH = test_db_path

    from manager.db import init_db, get_db
    init_db()
    yield test_db_path

    try:
        test_db_path.unlink(missing_ok=True)
    except Exception:
        pass
    manager.config.DB_PATH = _ORIG_DB_PATH
    manager.db.DB_PATH = _ORIG_DB_PATH


@pytest.fixture
def test_settings(test_db):
    """Enable trial mode and set path routing."""
    from manager.db import get_db
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    settings = {
        "trial_enabled": "true",
        "trial_max_instances": "5",
        "trial_idle_timeout": "2",
        "trial_max_memory_pct": "95",
        "trial_queue_enabled": "true",
        "routing_mode": "path",
        "base_domain": "localhost",
        "path_prefix_length": "8",
        "runtime_mode": "process",
    }
    with get_db() as conn:
        for k, v in settings.items():
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (k, v, now),
            )


@pytest.fixture
def activation_key(test_db):
    """Create a valid activation key."""
    from manager.key_service import create_keys
    keys = create_keys(count=1, days=30, plan="test")
    return keys[0]


@pytest.fixture
def client(fake_runtime, test_settings):
    """FastAPI TestClient with mocked runtime."""
    from manager.app import app
    from fastapi.testclient import TestClient
    with TestClient(app) as tc:
        yield tc
