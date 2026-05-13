"""Trial queue persistence operations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from manager.db import get_db


def get_active_trial_count() -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM instances WHERE is_trial=1 AND status='running'"
        ).fetchone()
    return row[0] if row else 0


def get_trial_by_ip(client_ip: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE is_trial=1 AND client_ip=? AND status='running' ORDER BY created_at DESC LIMIT 1",
            (client_ip,),
        ).fetchone()
    return dict(row) if row else None


def enqueue_trial(client_ip: str) -> dict:
    """Add trial request to queue when resources are full. Returns position dict."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM trial_queue WHERE client_ip=? AND status='waiting'",
            (client_ip,),
        ).fetchone()
        if existing:
            pos = conn.execute(
                "SELECT COUNT(*) FROM trial_queue WHERE status='waiting' AND id <= ?",
                (existing["id"],),
            ).fetchone()[0]
            return {"queued": True, "position": pos, "message": f"排队中，前方 {pos - 1} 人"}

        conn.execute(
            "INSERT INTO trial_queue (client_ip, status, created_at) VALUES (?, 'waiting', ?)",
            (client_ip, now),
        )
        pos = conn.execute(
            "SELECT COUNT(*) FROM trial_queue WHERE status='waiting'"
        ).fetchone()[0]
    return {"queued": True, "position": pos, "message": f"已加入排队，前方 {pos - 1} 人"}


def get_next_waiting() -> list[dict]:
    """Get the first waiting entry in the queue."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM trial_queue WHERE status='waiting' ORDER BY id ASC LIMIT 1"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_queue_entry_done(entry_id: int, instance_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE trial_queue SET status='done', instance_id=?, processed_at=? WHERE id=?",
            (instance_id, now, entry_id),
        )


def mark_queue_entry_failed(entry_id: int, error: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE trial_queue SET status='failed', error=?, processed_at=? WHERE id=?",
            (error[:200], now, entry_id),
        )


def cleanup_old_queue_entries():
    with get_db() as conn:
        conn.execute(
            "DELETE FROM trial_queue WHERE status IN ('done','failed') AND created_at < ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
        )


def get_queue_waiting_count() -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM trial_queue WHERE status='waiting'"
        ).fetchone()
    return row[0] if row else 0


def get_running_trial_ids() -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT instance_id FROM instances WHERE is_trial=1 AND status='running'"
        ).fetchall()
    return [r["instance_id"] for r in rows]


def get_running_trial_rows() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM instances WHERE is_trial=1 AND status='running'"
        ).fetchall()
    return [dict(r) for r in rows]


def update_instance_activity(instance_id: str, activity_time: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET last_activity=? WHERE instance_id=? AND is_trial=1",
            (activity_time, instance_id),
        )


def get_activity_timestamp(instance_id: str) -> str | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_activity FROM instances WHERE instance_id=?",
            (instance_id,),
        ).fetchone()
    return row["last_activity"] if row else None
