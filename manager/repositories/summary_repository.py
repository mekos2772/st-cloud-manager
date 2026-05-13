"""Dashboard aggregation queries."""
from __future__ import annotations

from datetime import datetime, timezone

from manager.db import get_db


def get_summary() -> dict:
    with get_db() as conn:
        total_instances = conn.execute("SELECT COUNT(*) FROM instances").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM instances WHERE status='running'").fetchone()[0]
        stopped = conn.execute("SELECT COUNT(*) FROM instances WHERE status='stopped'").fetchone()[0]
        expired = conn.execute("SELECT COUNT(*) FROM instances WHERE status='expired'").fetchone()[0]
        deleted = conn.execute("SELECT COUNT(*) FROM instances WHERE status='deleted'").fetchone()[0]
        unused_keys = conn.execute("SELECT COUNT(*) FROM activation_keys WHERE status='unused'").fetchone()[0]
        used_keys = conn.execute("SELECT COUNT(*) FROM activation_keys WHERE status='used'").fetchone()[0]
        api_ok = conn.execute("SELECT COUNT(*) FROM instances WHERE api_status='ok'").fetchone()[0]
        api_fail = conn.execute("SELECT COUNT(*) FROM instances WHERE api_status='failed'").fetchone()[0]
        stream_ok = conn.execute("SELECT COUNT(*) FROM instances WHERE stream_status='ok'").fetchone()[0]
        stream_fail = conn.execute("SELECT COUNT(*) FROM instances WHERE stream_status='failed'").fetchone()[0]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) FROM instances WHERE created_at LIKE ?", (today + "%",)
        ).fetchone()[0]

    return {
        "total_instances": total_instances,
        "running": running, "stopped": stopped, "expired": expired, "deleted": deleted,
        "unused_keys": unused_keys, "used_keys": used_keys,
        "today_created": today_count,
        "api_ok": api_ok, "api_failed": api_fail,
        "stream_ok": stream_ok, "stream_failed": stream_fail,
    }


def get_running_instances_for_expiry_check(now_iso: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM instances WHERE status = 'running' AND expires_at <= ?", (now_iso,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_running_instances_for_crash_check() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT instance_id, container_name FROM instances WHERE status='running'"
        ).fetchall()
    return [dict(r) for r in rows]
