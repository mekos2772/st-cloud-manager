"""Compatibility facade — delegates to repositories/settings_repository."""
from manager.repositories.settings_repository import (
    get_all_settings,
    get_setting,
    set_settings,
    get_effective_api_settings,
    get_api_config_safe,
    update_api_config,
    get_real_upstream_key,
    _mask_key,
)
