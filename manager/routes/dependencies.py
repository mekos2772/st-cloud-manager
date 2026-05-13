"""Shared route dependencies."""
from __future__ import annotations

from fastapi import Header, HTTPException

from manager.config import ADMIN_API_KEY


def verify_admin(x_api_key: str | None = Header(None)):
    if not x_api_key or x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin API key")
