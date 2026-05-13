"""ST Cloud Manager - FastAPI backend v0.4 (refactored).

Thin HTTP app — wires routers and lifecycle hooks only.
Business logic lives in services/, repositories/, runtimes/, routers/.
"""
from contextlib import asynccontextmanager
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from manager.config import BASE_DIR
from manager.db import init_db
from manager.router_service import sync_routes_safely
from manager.scheduler import run_scheduler

from manager.routes.public import router as public_router
from manager.routes.admin import router as admin_router
from manager.routes.proxy import router as proxy_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sync_routes_safely("startup")
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    yield


app = FastAPI(title="ST Cloud Manager v0.4", lifespan=lifespan)

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public_router)
app.include_router(admin_router)
app.include_router(proxy_router)
