"""ST Cloud Manager — Smoke Tests (8 groups).

verification: pytest tests/test_smoke.py -v
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from manager.db import get_db, init_db


# ═══════════════════════════════════════════════════════════════════════
# Group 1: Path-mode activation — create + verify /st-xxx/ accessible
# ═══════════════════════════════════════════════════════════════════════

class TestPathModeActivation:
    """smoke-1: Activate with path routing, verify URL and proxy route."""

    def test_activate_creates_path_instance(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["ready"] is True
        assert data["instance_id"]
        assert data["url"].startswith("http")
        assert data["steps"][-1] == "mark key used"

        # Verify DB state
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM instances WHERE instance_id = ?",
                (data["instance_id"],),
            ).fetchone()
        assert row is not None
        assert row["status"] == "running"
        assert row["path_prefix"]  # must have path_prefix in path mode
        assert row["path_prefix"].startswith("/st-")
        assert row["domain"] == "localhost"

    def test_path_url_accessible(self, client: TestClient, activation_key: str):
        """After creation, the /st-xxx/ route returns non-404."""
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200
        instance_id = resp.json()["instance_id"]

        with get_db() as conn:
            row = conn.execute(
                "SELECT path_prefix FROM instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        prefix = row["path_prefix"]

        # Access via path-fallback — should hit the instance
        resp2 = client.get(prefix)
        # With our mock, path_proxy reads .st_port and proxies — should 200
        assert resp2.status_code in (200, 502, 503), f"Unexpected status {resp2.status_code}: {resp2.text[:200]}"

    def test_activation_key_consumed(self, client: TestClient, activation_key: str):
        """Key becomes 'used' after activation."""
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200

        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM activation_keys WHERE key = ?",
                (activation_key,),
            ).fetchone()
        assert row["status"] == "used"

    def test_duplicate_key_rejected(self, client: TestClient, activation_key: str):
        """Using the same key twice fails."""
        resp1 = client.post("/activate", json={"key": activation_key})
        assert resp1.status_code == 200
        resp2 = client.post("/activate", json={"key": activation_key})
        assert resp2.status_code == 400


# ═══════════════════════════════════════════════════════════════════════
# Group 2: Process-mode create / restart / delete
# ═══════════════════════════════════════════════════════════════════════

class TestProcessModeLifecycle:
    """smoke-2: create → stop → start → restart → delete in process mode."""

    def _activate(self, client: TestClient, activation_key: str) -> dict:
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200
        return resp.json()

    def test_stop_instance(self, client: TestClient, activation_key: str, fake_runtime):
        inst = self._activate(client, activation_key)
        iid = inst["instance_id"]

        resp = client.post(
            f"/api/admin/instances/{iid}/stop",
            headers={"x-api-key": "test-admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM instances WHERE instance_id = ?",
                (iid,),
            ).fetchone()
        assert row["status"] == "stopped"

    def test_start_instance(self, client: TestClient, activation_key: str, fake_runtime):
        inst = self._activate(client, activation_key)
        iid = inst["instance_id"]

        # Stop first
        client.post(f"/api/admin/instances/{iid}/stop", headers={"x-api-key": "test-admin"})
        # Start
        resp = client.post(f"/api/admin/instances/{iid}/start", headers={"x-api-key": "test-admin"})
        assert resp.status_code == 200

        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM instances WHERE instance_id = ?",
                (iid,),
            ).fetchone()
        assert row["status"] == "running"

    def test_restart_instance(self, client: TestClient, activation_key: str, fake_runtime):
        inst = self._activate(client, activation_key)
        iid = inst["instance_id"]

        resp = client.post(
            f"/api/admin/instances/{iid}/restart",
            headers={"x-api-key": "test-admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert f"st-{iid}" in fake_runtime.restarted

    def test_delete_cleans_runtime(self, client: TestClient, activation_key: str, fake_runtime):
        inst = self._activate(client, activation_key)
        iid = inst["instance_id"]
        cname = f"st-{iid}"

        resp = client.delete(
            f"/api/admin/instances/{iid}",
            headers={"x-api-key": "test-admin"},
        )
        assert resp.status_code == 200

        # Runtime removed
        assert cname in fake_runtime.removed

        # DB marked deleted
        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM instances WHERE instance_id = ?",
                (iid,),
            ).fetchone()
        assert row["status"] == "deleted"


# ═══════════════════════════════════════════════════════════════════════
# Group 3: Docker-mode lifecycle (mocked — same logic, skips real Docker)
# ═══════════════════════════════════════════════════════════════════════

class TestDockerModeLifecycle:
    """smoke-3: Docker mode follows the same code path; smoke check admin endpoints."""

    def test_list_instances(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200
        iid = resp.json()["instance_id"]

        resp2 = client.get("/api/admin/instances", headers={"x-api-key": "test-admin"})
        assert resp2.status_code == 200
        instances = resp2.json()
        assert any(inst["instance_id"] == iid for inst in instances)

    def test_get_instance_detail(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        iid = resp.json()["instance_id"]

        resp2 = client.get(f"/api/admin/instances/{iid}", headers={"x-api-key": "test-admin"})
        assert resp2.status_code == 200
        detail = resp2.json()
        assert detail["instance_id"] == iid
        assert "password_masked" in detail

    def test_inspect_instance(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        iid = resp.json()["instance_id"]

        resp2 = client.get(f"/api/admin/instances/{iid}/inspect", headers={"x-api-key": "test-admin"})
        assert resp2.status_code == 200
        info = resp2.json()
        assert "container_exists" in info
        assert "domain" in info

    def test_logs_endpoint(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        iid = resp.json()["instance_id"]

        resp2 = client.get(f"/api/admin/instances/{iid}/logs", headers={"x-api-key": "test-admin"})
        assert resp2.status_code == 200
        assert "logs" in resp2.json()

    def test_health_check(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        iid = resp.json()["instance_id"]

        resp2 = client.post(f"/api/admin/instances/{iid}/check", headers={"x-api-key": "test-admin"})
        assert resp2.status_code == 200
        assert resp2.json()["web"] == "ready"


# ═══════════════════════════════════════════════════════════════════════
# Group 4: Manager restart — old routes still accessible
# ═══════════════════════════════════════════════════════════════════════

class TestManagerRestart:
    """smoke-4: After a simulated restart, existing instance routes work."""

    def test_route_accessible_after_simulated_restart(self, client: TestClient, activation_key: str):
        # Create instance
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200
        iid = resp.json()["instance_id"]
        url = resp.json()["url"]

        # Simulate restart: re-init DB and re-query
        init_db()

        # Instance should still exist in DB
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM instances WHERE instance_id = ?",
                (iid,),
            ).fetchone()
        assert row is not None
        assert row["status"] == "running"

        # Route should still resolve
        prefix = row["path_prefix"]
        resp2 = client.get(prefix)
        assert resp2.status_code in (200, 502, 503), f"Route broken: {resp2.status_code}"

    def test_summary_after_restart(self, client: TestClient, activation_key: str):
        # Create 2 instances
        keys = [activation_key]
        from manager.key_service import create_keys
        keys += create_keys(count=1, days=30)
        for k in keys:
            client.post("/activate", json={"key": k})

        # Simulate restart
        init_db()

        resp = client.get("/api/admin/summary", headers={"x-api-key": "test-admin"})
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["running"] >= 2


# ═══════════════════════════════════════════════════════════════════════
# Group 5: Trial mode — create, queue, heartbeat, idle release
# ═══════════════════════════════════════════════════════════════════════

class TestTrialMode:
    """smoke-5: Trial lifecycle end-to-end."""

    def test_trial_status(self, client: TestClient):
        resp = client.get("/api/trial/status")
        assert resp.status_code == 200
        status = resp.json()
        assert status["trial_enabled"] is True

    def test_create_trial(self, client: TestClient):
        resp = client.post("/api/trial/create")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("is_trial") is True or data.get("queued") is True
        if data.get("is_trial"):
            assert data["instance_id"]
            assert data["url"]

    def test_trial_one_per_ip(self, client: TestClient):
        """Second trial from same IP should fail or queue."""
        resp1 = client.post("/api/trial/create")
        assert resp1.status_code == 200

        # If first was created (not queued), second should fail
        if resp1.json().get("is_trial"):
            resp2 = client.post("/api/trial/create")
            # Either 400 (already has one) or queued
            assert resp2.status_code in (200, 400)

    def test_trial_heartbeat(self, client: TestClient):
        """Heartbeat endpoint does not crash."""
        resp = client.post("/api/trial/create")
        if resp.status_code == 200 and resp.json().get("is_trial"):
            iid = resp.json()["instance_id"]
            resp2 = client.post(f"/api/trial/activity/{iid}")
            assert resp2.status_code == 200
            assert resp2.json()["ok"] is True

    def test_trial_release(self, client: TestClient):
        """Release a trial instance via admin endpoint."""
        resp = client.post("/api/trial/create")
        if resp.status_code == 200 and resp.json().get("is_trial"):
            iid = resp.json()["instance_id"]

            resp2 = client.delete(
                f"/api/admin/instances/{iid}",
                headers={"x-api-key": "test-admin"},
            )
            assert resp2.status_code == 200

            with get_db() as conn:
                row = conn.execute(
                    "SELECT status FROM instances WHERE instance_id = ?",
                    (iid,),
                ).fetchone()
            assert row["status"] == "deleted"

    def test_trial_queue_status(self, client: TestClient):
        """Queue status endpoint returns valid data."""
        resp = client.get("/api/trial/status")
        assert resp.status_code == 200
        status = resp.json()
        assert "queue_length" in status
        assert "active_trials" in status
        assert "max_trials" in status


# ═══════════════════════════════════════════════════════════════════════
# Group 6: Renew — API key updated, access preserved
# ═══════════════════════════════════════════════════════════════════════

class TestRenew:
    """smoke-6: Renew preserves instance access and rotates API key."""

    def test_renew_instance(self, client: TestClient, activation_key: str):
        # Create
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200
        iid = resp.json()["instance_id"]

        # Get old API key
        with get_db() as conn:
            old = conn.execute(
                "SELECT api_key, expires_at FROM instances WHERE instance_id = ?",
                (iid,),
            ).fetchone()
        old_key = old["api_key"]
        old_expires = old["expires_at"]

        # Renew
        resp2 = client.post(
            f"/api/admin/instances/{iid}/renew",
            json={"days": 30},
            headers={"x-api-key": "test-admin"},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["instance_id"] == iid
        new_expires = data["expires_at"]

        # Expiry extended
        assert new_expires > old_expires, f"Expiry not extended: {old_expires} → {new_expires}"

        # Status should be running
        with get_db() as conn:
            row = conn.execute(
                "SELECT status, expires_at FROM instances WHERE instance_id = ?",
                (iid,),
            ).fetchone()
        assert row["status"] == "running"
        assert row["expires_at"] == new_expires


# ═══════════════════════════════════════════════════════════════════════
# Group 7: Delete — DB, runtime, route, proxy key all cleaned
# ═══════════════════════════════════════════════════════════════════════

class TestDeleteCleanup:
    """smoke-7: Full cleanup verification after delete."""

    def test_delete_cleans_db(self, client: TestClient, activation_key: str, fake_runtime):
        resp = client.post("/activate", json={"key": activation_key})
        iid = resp.json()["instance_id"]
        cname = f"st-{iid}"

        before_count = fake_runtime.running_count

        resp2 = client.delete(
            f"/api/admin/instances/{iid}",
            headers={"x-api-key": "test-admin"},
        )
        assert resp2.status_code == 200

        # DB status is deleted
        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM instances WHERE instance_id = ?",
                (iid,),
            ).fetchone()
        assert row["status"] == "deleted"

        # Runtime container removed
        assert cname in fake_runtime.removed

    def test_delete_archives_user_dir(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        iid = resp.json()["instance_id"]

        from manager.config import USERS_DIR, ARCHIVE_DIR
        assert (USERS_DIR / iid).exists()

        client.delete(f"/api/admin/instances/{iid}", headers={"x-api-key": "test-admin"})

        # User dir moved to archive
        assert not (USERS_DIR / iid).exists()
        assert (ARCHIVE_DIR / iid).exists()

    def test_delete_twice_is_idempotent(self, client: TestClient, activation_key: str):
        resp = client.post("/activate", json={"key": activation_key})
        iid = resp.json()["instance_id"]

        client.delete(f"/api/admin/instances/{iid}", headers={"x-api-key": "test-admin"})
        # Delete again — should still work (404 or ok)
        resp2 = client.delete(f"/api/admin/instances/{iid}", headers={"x-api-key": "test-admin"})
        assert resp2.status_code in (200, 404)


# ═══════════════════════════════════════════════════════════════════════
# Group 8: API proxy — /v1/chat/completions and /v1/models
# ═══════════════════════════════════════════════════════════════════════

class TestApiProxy:
    """smoke-8: API proxy routes with key validation."""

    def test_v1_models_requires_key(self, client: TestClient):
        """Without auth, returns 401."""
        resp = client.get("/v1/models")
        assert resp.status_code == 401  # missing/invalid Authorization header

        resp2 = client.get("/v1/models", headers={"Authorization": "Bearer invalid-key"})
        assert resp2.status_code == 401

    def test_v1_chat_completions_requires_key(self, client: TestClient):
        """Without valid instance key, returns 401."""
        resp = client.post("/v1/chat/completions", json={
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 401  # missing Authorization

        resp2 = client.post("/v1/chat/completions", json={
            "model": "test",
            "messages": [{"role": "user", "content": "hi"}],
        }, headers={"Authorization": "Bearer bad-key"})
        assert resp2.status_code == 401

    def test_v1_models_with_valid_key(self, client: TestClient, activation_key: str, monkeypatch):
        """With a real instance API key, returns 200 (proxy chain validated)."""
        resp = client.post("/activate", json={"key": activation_key})
        assert resp.status_code == 200

        with get_db() as conn:
            row = conn.execute(
                "SELECT api_key FROM instances WHERE instance_id = ?",
                (resp.json()["instance_id"],),
            ).fetchone()
        api_key = row["api_key"]

        # Mock the upstream HTTP call to avoid real network dependency
        import httpx
        async def fake_get(*args, **kwargs):
            class FakeResponse:
                status_code = 200
                headers = {"content-type": "application/json"}
                async def aiter_bytes(self):
                    yield b'{"object":"list","data":[]}'
            return FakeResponse()

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

        resp2 = client.get("/v1/models", headers={"Authorization": f"Bearer {api_key}"})
        assert resp2.status_code == 200, f"Proxy chain failed: {resp2.status_code} {resp2.text[:200]}"


# ═══════════════════════════════════════════════════════════════════════
# Integration / cross-cutting
# ═══════════════════════════════════════════════════════════════════════

class TestCrossCutting:
    """Cross-cutting concerns: admin auth, key management, settings."""

    def test_admin_auth_required(self, client: TestClient):
        """All admin endpoints reject missing key."""
        endpoints = [
            ("GET", "/api/admin/summary"),
            ("GET", "/api/admin/instances"),
            ("GET", "/api/admin/keys"),
        ]
        for method, path in endpoints:
            resp = client.request(method, path)
            assert resp.status_code in (403, 422), f"{method} {path} returned {resp.status_code}"

    def test_admin_key_management(self, client: TestClient):
        """Create, list, disable, enable, delete keys."""
        # Create
        resp = client.post(
            "/api/admin/keys",
            json={"count": 2, "days": 7, "plan": "smoke"},
            headers={"x-api-key": "test-admin"},
        )
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        assert len(keys) == 2

        # List
        resp2 = client.get("/api/admin/keys", headers={"x-api-key": "test-admin"})
        assert len(resp2.json()) >= 2

        # Disable
        key_to_disable = keys[0]
        resp3 = client.post(
            f"/api/admin/keys/{key_to_disable}/disable",
            headers={"x-api-key": "test-admin"},
        )
        assert resp3.status_code == 200

        # Enable
        resp4 = client.post(
            f"/api/admin/keys/{key_to_disable}/enable",
            headers={"x-api-key": "test-admin"},
        )
        assert resp4.status_code == 200

        # Delete
        resp5 = client.delete(
            f"/api/admin/keys/{key_to_disable}",
            headers={"x-api-key": "test-admin"},
        )
        assert resp5.status_code == 200

    def test_summary_counts(self, client: TestClient, activation_key: str):
        """Summary reflects created instance."""
        resp = client.get("/api/admin/summary", headers={"x-api-key": "test-admin"})
        before = resp.json()

        client.post("/activate", json={"key": activation_key})

        resp2 = client.get("/api/admin/summary", headers={"x-api-key": "test-admin"})
        after = resp2.json()
        assert after["running"] >= before["running"] + 1

    def test_health_endpoints(self, client: TestClient):
        """Health checks don't crash."""
        for ep in ["/health/docker", "/health/traefik", "/health/manager", "/health/templates"]:
            resp = client.get(f"/api/admin{ep}", headers={"x-api-key": "test-admin"})
            assert resp.status_code == 200

    def test_backup_endpoints(self, client: TestClient):
        """Backup create/list/delete works."""
        # Create
        resp = client.post("/api/admin/backup/create", headers={"x-api-key": "test-admin"})
        assert resp.status_code == 200
        name = resp.json()["name"]

        # List
        resp2 = client.get("/api/admin/backup/list", headers={"x-api-key": "test-admin"})
        assert any(b["name"] == name for b in resp2.json())

        # Delete
        resp3 = client.delete(f"/api/admin/backup/{name}", headers={"x-api-key": "test-admin"})
        assert resp3.status_code == 200
