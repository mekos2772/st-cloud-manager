"""Compatibility facade — delegates to repositories/key_repository."""
from manager.repositories.key_repository import (
    create_keys,
    list_keys,
    validate_key,
    mark_key_used,
    disable_key,
    enable_key,
    delete_key,
)
