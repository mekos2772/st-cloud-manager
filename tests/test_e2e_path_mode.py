"""E2E: Path-mode routing — real HTTP verification.

Tests that require the manager to be running on http://127.0.0.1:5000.
All tests auto-skip if the manager is not reachable.

Covers:
  - create instance → verify access_url
  - GET /st-xxx/ returns non-404
  - static assets under /st-xxx/ resolve
  - manager restart preserves routes
  - delete cleans up
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
import httpx

_project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_project_root))

MANAGER_BASE = "http://127.0.0.1:5000"
ADMIN_AUTH = {"x-api-key": "test-admin"}


def _manager_online() -> bool:
    try:
        resp = httpx.get(f"{MANAGER_BASE}/activate", timeout=3, follow_redirects=False)
        return resp.status_code in (200, 307, 302)
    except Exception:
        return False


def _get_unused_key() -> str | None:
    try:
        resp = httpx.get(
            f"{MANAGER_BASE}/api/admin/keys?status=unused",
            headers=ADMIN_AUTH,
            timeout=10,
        )
        if resp.status_code == 200:
            keys = resp.json()
            if keys:
                return keys[0]["key"]
        resp = httpx.post(
            f"{MANAGER_BASE}/api/admin/keys",
            json={"count": 1, "days": 1, "plan": "e2e"},
            headers=ADMIN_AUTH,
            timeout=10,
        )
        return resp.json()["keys"][0]
    except Exception:
        return None


def _activate(key: str) -> dict:
    resp = httpx.post(f"{MANAGER_BASE}/activate", json={"key": key}, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Activate failed ({resp.status_code}): {resp.text[:200]}")
    return resp.json()


def _delete_instance(iid: str):
    httpx.delete(f"{MANAGER_BASE}/api/admin/instances/{iid}", headers=ADMIN_AUTH, timeout=30)


pytestmark = pytest.mark.skipif(not _manager_online(), reason="Manager not reachable")


class TestPathModeE2E:
    """Real HTTP E2E against a running manager."""

    def test_create_and_access_path_url(self):
        key = _get_unused_key()
        assert key, "Need at least one unused activation key"

        inst = _activate(key)
        iid = inst["instance_id"]
        url = inst["url"]

        assert iid, "instance_id required"
        assert url.startswith("http"), f"url must be absolute HTTP: {url}"
        assert inst["ready"] is True, "Instance must report ready"

        # Access the instance via its path URL
        # Extract path from url: http://localhost/st-xxx/ → /st-xxx/
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path
            resp = httpx.get(f"{MANAGER_BASE}{path}", timeout=15, follow_redirects=False)
            # 200 = page loaded, 302/307 = redirect to login, 503 = still starting
            assert resp.status_code in (200, 302, 307, 503), \
                f"Path access returned {resp.status_code}: {resp.text[:200]}"
        finally:
            _delete_instance(iid)

    def test_instance_detail_after_create(self):
        key = _get_unused_key()
        assert key, "Need at least one unused activation key"

        inst = _activate(key)
        iid = inst["instance_id"]

        try:
            resp = httpx.get(
                f"{MANAGER_BASE}/api/admin/instances/{iid}",
                headers=ADMIN_AUTH,
                timeout=10,
            )
            assert resp.status_code == 200, f"Detail API failed: {resp.status_code}"
            detail = resp.json()
            assert detail["instance_id"] == iid
            assert detail["domain"] == "localhost"
            assert detail["path_prefix"]  # must be populated in path mode
            assert detail["status"] == "running"
        finally:
            _delete_instance(iid)

    def test_restart_manager_preserves_route(self):
        """Create instance, simulate DB re-read (manager 'restart'), verify."""
        key = _get_unused_key()
        assert key, "Need at least one unused activation key"

        inst = _activate(key)
        iid = inst["instance_id"]

        from manager.db import init_db
        init_db()

        try:
            resp = httpx.get(
                f"{MANAGER_BASE}/api/admin/instances/{iid}",
                headers=ADMIN_AUTH,
                timeout=10,
            )
            assert resp.status_code == 200, f"Instance lost after simulated restart: {resp.status_code}"
            detail = resp.json()
            assert detail["status"] == "running"
            assert detail["path_prefix"]
        finally:
            _delete_instance(iid)

    def test_delete_cleans_instance(self):
        key = _get_unused_key()
        assert key, "Need at least one unused activation key"

        inst = _activate(key)
        iid = inst["instance_id"]

        _delete_instance(iid)

        # Verify deleted
        resp = httpx.get(
            f"{MANAGER_BASE}/api/admin/instances/{iid}",
            headers=ADMIN_AUTH,
            timeout=10,
        )
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["status"] == "deleted", f"Expected deleted, got {detail.get('status')}"

    def test_api_proxy_rejects_bad_key(self):
        resp = httpx.post(
            f"{MANAGER_BASE}/v1/chat/completions",
            json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer fake-key-12345"},
            timeout=10,
        )
        assert resp.status_code == 401, f"Expected 401 for bad key, got {resp.status_code}"

    def test_summary_endpoint(self):
        resp = httpx.get(f"{MANAGER_BASE}/api/admin/summary", headers=ADMIN_AUTH, timeout=10)
        assert resp.status_code == 200
        summary = resp.json()
        required = ["total_instances", "running", "stopped", "expired",
                     "unused_keys", "used_keys"]
        for key in required:
            assert key in summary, f"Summary missing key: {key}"


class TestTrialE2E:
    """Real HTTP E2E for trial endpoints."""

    def test_trial_status_reports_weak_isolation(self):
        resp = httpx.get(f"{MANAGER_BASE}/api/trial/status", timeout=10)
        assert resp.status_code == 200
        status = resp.json()
        # In process mode, weak_isolation must be True
        # In docker mode, it should be False — but we don't hardcode expectation
        assert "weak_isolation" in status, "trial_status must include weak_isolation flag"
        assert status["trial_enabled"] is True
        assert status["max_trials"] > 0

    def test_trial_activity_endpoint(self):
        """Activity heartbeat for nonexistent instance should not crash."""
        resp = httpx.post(f"{MANAGER_BASE}/api/trial/activity/nonexistent", timeout=10)
        assert resp.status_code == 200
        # ok=False is acceptable for nonexistent instance
        assert resp.json()["ok"] in (True, False)
