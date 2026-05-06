import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from manager.db import get_db
from manager.config import DEFAULT_PLAN, DEFAULT_DAYS

KEY_PREFIX = "ST-"
KEY_SEGMENT_LENGTH = 4
KEY_SEGMENTS = 2


def _generate_key_string() -> str:
    chars = string.ascii_uppercase + string.digits
    segments = [
        "".join(secrets.choice(chars) for _ in range(KEY_SEGMENT_LENGTH))
        for _ in range(KEY_SEGMENTS)
    ]
    return KEY_PREFIX + "-".join(segments)


def create_keys(count: int = 1, days: int = DEFAULT_DAYS, plan: str = DEFAULT_PLAN) -> list[str]:
    now = datetime.now(timezone.utc).isoformat()
    keys = []
    with get_db() as conn:
        for _ in range(count):
            key_str = _generate_key_string()
            conn.execute(
                "INSERT INTO activation_keys (key, plan, days, created_at) VALUES (?, ?, ?, ?)",
                (key_str, plan, days, now),
            )
            keys.append(key_str)
    return keys


def list_keys(status: str | None = None) -> list[dict]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM activation_keys WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM activation_keys ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def validate_key(key_str: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM activation_keys WHERE key = ? AND status = 'unused'",
            (key_str,),
        ).fetchone()
    return dict(row) if row else None


def mark_key_used(key_str: str, instance_id: str):
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE activation_keys SET status = 'used', used_at = ?, instance_id = ? WHERE key = ?",
            (now, instance_id, key_str),
        )


def disable_key(key_str: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE activation_keys SET status = 'disabled' WHERE key = ?",
            (key_str,),
        )


def enable_key(key_str: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE activation_keys SET status = 'unused', used_at = NULL, instance_id = NULL WHERE key = ?",
            (key_str,),
        )


def delete_key(key_str: str):
    with get_db() as conn:
        conn.execute("DELETE FROM activation_keys WHERE key = ?", (key_str,))
