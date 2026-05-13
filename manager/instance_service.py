"""Compatibility facade — delegates to services/instance_orchestrator and services/trial_service.

All old imports from instance_service.py still work, but logic now lives in:
  - services.instance_orchestrator  (lifecycle operations)
  - services.trial_service          (queue, idle, heartbeat)
  - repositories.summary_repository (dashboard aggregation)
"""
from __future__ import annotations

from manager.services.instance_orchestrator import (
    create_instance,
    stop_instance,
    start_instance,
    restart_instance,
    renew_instance,
    delete_instance,
    get_instance,
    list_instances,
    apply_api_config,
    apply_api_config_all,
    check_expired,
    get_summary,
    check_instance,
    get_instance_logs,
    get_instance_inspect,
    check_crashed,
)

from manager.services.trial_service import (
    create_trial_instance,
    get_trial_queue_status,
    release_trial_instance,
    update_trial_activity,
    check_trial_idle,
    process_trial_queue,
)
