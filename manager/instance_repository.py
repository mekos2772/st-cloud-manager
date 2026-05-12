from __future__ import annotations

from manager.db import get_db
from manager.instance_model import with_access_url


def insert_instance(record: dict):
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO instances
                (instance_id, domain, container_name, username, password, api_key,
                 status, ready, api_status, stream_status, web_status,
                 cf_record_id, custom_domain, path_prefix, proxy_key_alias,
                 created_at, expires_at)
            VALUES
                (?, ?, ?, ?, ?, ?, 'running', ?, 'unchecked', 'unchecked', 'unchecked',
                 ?, ?, ?, ?, ?, ?)
            """,
            (
                record["instance_id"],
                record["domain"],
                record["container_name"],
                record["username"],
                record["password"],
                record["api_key"],
                1 if record.get("ready") else 0,
                record.get("cf_record_id"),
                record.get("custom_domain"),
                record.get("path_prefix", ""),
                record.get("proxy_key_alias", ""),
                record["created_at"],
                record["expires_at"],
            ),
        )


def insert_trial_instance(record: dict):
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO instances
                (instance_id, domain, container_name, username, password, api_key,
                 status, ready, api_status, stream_status, web_status,
                 cf_record_id, custom_domain, path_prefix, is_trial, last_activity,
                 client_ip, proxy_key_alias, created_at, expires_at)
            VALUES
                (?, ?, ?, ?, ?, ?, 'running', ?, 'unchecked', 'unchecked', 'unchecked',
                 ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                record["instance_id"],
                record["domain"],
                record["container_name"],
                record["username"],
                record["password"],
                record["api_key"],
                1 if record.get("ready") else 0,
                record.get("cf_record_id"),
                record.get("custom_domain"),
                record.get("path_prefix", ""),
                record.get("last_activity"),
                record.get("client_ip"),
                record.get("proxy_key_alias", ""),
                record["created_at"],
                record["expires_at"],
            ),
        )


def get_instance(instance_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
    return with_access_url(dict(row)) if row else None


def list_instances(status: str | None = None) -> list[dict]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM instances WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM instances ORDER BY created_at DESC"
            ).fetchall()
    return [with_access_url(dict(r)) for r in rows]


def update_status(instance_id: str, status: str, *, ready: int | None = None):
    with get_db() as conn:
        if ready is None:
            conn.execute(
                "UPDATE instances SET status = ? WHERE instance_id = ?",
                (status, instance_id),
            )
        else:
            conn.execute(
                "UPDATE instances SET status = ?, ready = ? WHERE instance_id = ?",
                (status, ready, instance_id),
            )


def update_api_key(instance_id: str, api_key: str, proxy_key_alias: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET api_key=?, proxy_key_alias=? WHERE instance_id=?",
            (api_key, proxy_key_alias, instance_id),
        )


def renew_instance_record(instance_id: str, expires_at: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET status = 'running', expires_at = ? WHERE instance_id = ?",
            (expires_at, instance_id),
        )


def mark_expired(instance_id: str):
    update_status(instance_id, "expired")


def update_web_check(instance_id: str, status: str, checked_at: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET web_status=?, web_checked_at=? WHERE instance_id=?",
            (status, checked_at, instance_id),
        )
