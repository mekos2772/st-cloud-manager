"""Trial instance service — queue, idle detection, heartbeat, release.

All trial-specific policies live here: one-trial-per-IP, queue management,
idle release, resource gating, heartbeat tracking.

Capability-aware: when the runtime does NOT support trial isolation (process
mode), the effective max trial count is clamped to a safe ceiling and the
response carries a weak_isolation flag so the frontend can inform the user.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from manager.config import USERS_DIR, ARCHIVE_DIR
from manager.settings_service import get_all_settings
from manager.proxy_service import delete_proxy_key
from manager.router_service import get_runtime_service
from manager.services.instance_orchestrator import (
    create_trial_instance_raw,
    release_instance_runtime,
    get_instance,
)
from manager.instance_repository import update_status
from manager.repositories.trial_repository import (
    get_active_trial_count,
    get_trial_by_ip,
    enqueue_trial,
    get_next_waiting,
    mark_queue_entry_done,
    mark_queue_entry_failed,
    cleanup_old_queue_entries,
    get_queue_waiting_count,
    get_running_trial_rows,
    update_instance_activity,
    get_activity_timestamp,
)

# Process mode cap — without container-level isolation we limit concurrency.
_PROCESS_MODE_MAX_TRIALS = 2


def _effective_trial_max() -> int:
    """Return the effective max trial count after capability degradation."""
    settings = get_all_settings()
    base = int(settings.get("trial_max_instances", "3"))
    runtime = get_runtime_service()
    if not runtime.supports_trial_isolation():
        return min(base, _PROCESS_MODE_MAX_TRIALS)
    return base


def create_trial_instance(client_ip: str) -> dict:
    """Create a trial instance without activation key. Handles IP limits,
    resource gating, queuing, and capability-aware degradation."""
    settings = get_all_settings()
    runtime = get_runtime_service()

    if settings.get("trial_enabled", "false") != "true":
        raise ValueError("体验模式未启用")

    existing = get_trial_by_ip(client_ip)
    if existing:
        raise ValueError(f"您的 IP 已有体验实例 ({existing['instance_id']})，请等待其释放后再创建")

    trial_max = _effective_trial_max()
    current = get_active_trial_count()
    weak_isolation = not runtime.supports_trial_isolation()

    if current >= trial_max:
        if settings.get("trial_queue_enabled", "true") == "true":
            return enqueue_trial(client_ip)
        raise ValueError(f"体验实例已满 ({current}/{trial_max})，请稍后再试")

    # Resource check — skip when no resource limits available
    if runtime.supports_resource_limits():
        trial_max_mem = int(settings.get("trial_max_memory_pct", "85"))
        from manager.resource_service import can_create_instance
        can, reason = can_create_instance(trial_max_mem, trial_max)
        if not can:
            if settings.get("trial_queue_enabled", "true") == "true":
                return enqueue_trial(client_ip)
            raise ValueError(reason)

    result = create_trial_instance_raw(client_ip)

    if weak_isolation:
        result["weak_isolation"] = True

    return result


def process_trial_queue() -> int:
    """Process waiting trial queue entries. Returns number created."""
    settings = get_all_settings()
    if settings.get("trial_enabled", "false") != "true":
        return 0

    trial_max = int(settings.get("trial_max_instances", "3"))
    trial_max_mem = int(settings.get("trial_max_memory_pct", "85"))

    from manager.resource_service import can_create_instance
    can, _ = can_create_instance(trial_max_mem, trial_max)
    if not can:
        return 0

    created = 0
    now = datetime.now(timezone.utc).isoformat()
    waiting = get_next_waiting()

    for entry in waiting:
        can, _ = can_create_instance(trial_max_mem, trial_max)
        if not can:
            break
        try:
            result = create_trial_instance_raw(entry["client_ip"])
            mark_queue_entry_done(entry["id"], result["instance_id"])
            created += 1
        except Exception as e:
            mark_queue_entry_failed(entry["id"], str(e))

    cleanup_old_queue_entries()
    return created


def check_trial_idle() -> int:
    """Release trial instances that have been idle too long. Returns count released."""
    settings = get_all_settings()
    if settings.get("trial_enabled", "false") != "true":
        return 0

    idle_timeout = int(settings.get("trial_idle_timeout", "600"))
    now = datetime.now(timezone.utc)
    released = 0

    rows = get_running_trial_rows()
    for inst in rows:
        if _is_instance_idle(inst, idle_timeout, now):
            try:
                release_trial_instance(inst["instance_id"])
                released += 1
            except Exception:
                pass

    return released


def _is_instance_idle(inst: dict, idle_timeout: int, now) -> bool:
    last_act = inst.get("last_activity")
    if last_act:
        try:
            last_dt = datetime.fromisoformat(last_act)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (now - last_dt).total_seconds() < idle_timeout:
                return False
        except (ValueError, TypeError):
            pass

    user_dir = USERS_DIR / inst["instance_id"]
    if user_dir.exists():
        latest = _latest_mtime(user_dir)
        if latest:
            latest_dt = datetime.fromtimestamp(latest, tz=timezone.utc)
            if (now - latest_dt).total_seconds() < idle_timeout:
                update_instance_activity(inst["instance_id"], latest_dt.isoformat())
                return False

    return True


def _latest_mtime(directory: Path) -> float | None:
    max_mtime = 0.0
    skip_dirs = {"backups", "thumbnails", "vectors", "__pycache__", ".cache"}
    try:
        for p in directory.rglob("*"):
            if p.is_dir():
                continue
            if any(s in p.parts for s in skip_dirs):
                continue
            try:
                mtime = p.stat().st_mtime
                if mtime > max_mtime:
                    max_mtime = mtime
            except OSError:
                pass
    except (OSError, IOError):
        pass
    return max_mtime if max_mtime > 0 else None


def release_trial_instance(instance_id: str):
    """Release a trial instance: stop runtime, archive, mark released."""
    inst = get_instance(instance_id)
    if not inst or not inst.get("is_trial"):
        raise ValueError(f"Not a trial instance: {instance_id}")

    release_instance_runtime(instance_id)
    update_status(instance_id, "released", ready=0)


def update_trial_activity(instance_id: str):
    """Update last_activity timestamp for a trial instance."""
    now = datetime.now(timezone.utc).isoformat()
    update_instance_activity(instance_id, now)


def get_trial_queue_status() -> dict:
    """Return current queue status, reflecting capability degradation."""
    waiting = get_queue_waiting_count()
    active = get_active_trial_count()
    settings = get_all_settings()
    runtime = get_runtime_service()
    return {
        "queue_length": waiting,
        "active_trials": active,
        "max_trials": _effective_trial_max(),
        "idle_timeout": int(settings.get("trial_idle_timeout", "600")),
        "trial_enabled": settings.get("trial_enabled", "false") == "true",
        "weak_isolation": not runtime.supports_trial_isolation(),
    }
