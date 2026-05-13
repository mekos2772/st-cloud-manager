"""Public routes — activate, trial, static pages."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, FileResponse
from pydantic import BaseModel

from manager.config import BASE_DIR
from manager.services.instance_orchestrator import create_instance
from manager.services.trial_service import (
    create_trial_instance,
    get_trial_queue_status,
    update_trial_activity,
)

router = APIRouter(tags=["public"])
static_dir = BASE_DIR / "static"


class ActivateRequest(BaseModel):
    key: str


@router.post("/activate")
def activate(req: ActivateRequest):
    try:
        return create_instance(req.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/trial/status")
def trial_status():
    return get_trial_queue_status()


@router.post("/api/trial/create")
def trial_create(request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    try:
        return create_trial_instance(client_ip)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/trial/activity/{instance_id}")
def trial_activity(instance_id: str, request: Request):
    try:
        update_trial_activity(instance_id)
        return {"ok": True}
    except Exception:
        return {"ok": False}


@router.get("/")
def root():
    return RedirectResponse(url="/activate")


@router.get("/activate")
def activate_page():
    return FileResponse(str(static_dir / "activate.html"))


@router.get("/admin")
def admin_page():
    return FileResponse(str(static_dir / "admin.html"))
