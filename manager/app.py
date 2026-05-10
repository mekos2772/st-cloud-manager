"""ST Cloud Manager - FastAPI backend v0.3."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import threading
import shutil
import subprocess
from datetime import datetime, timezone

from manager.config import ADMIN_API_KEY, BASE_DIR, DOCKER_NETWORK, DOCKER_IMAGE
from manager.db import init_db, get_db
from manager.traefik_config_service import regenerate
from manager.key_service import create_keys, list_keys, disable_key, enable_key, delete_key
from manager.instance_service import (
    create_instance, stop_instance, start_instance, restart_instance,
    renew_instance, delete_instance, get_instance, list_instances,
    apply_api_config, apply_api_config_all, check_expired,
    get_summary, check_instance, get_instance_logs, get_instance_inspect,
    create_trial_instance, get_trial_queue_status, release_trial_instance,
    update_trial_activity,
)
from manager.scheduler import run_scheduler
from manager.settings_service import (
    get_api_config_safe, update_api_config, get_all_settings,
)
from manager.api_test_service import test_connection, test_stream
from manager.api_proxy import proxy_chat_completions, proxy_models
from manager.docker_service import security_audit
from manager.cloudflare_service import (
    get_cf_settings, update_cf_settings, test_token, list_zones, verify_zone,
    create_test_record, delete_test_record, create_dns_record, is_cf_enabled,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    yield


app = FastAPI(title="ST Cloud Manager v0.3", lifespan=lifespan)

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


# ─── auth ───

def _verify_admin(api_key: str | None):
    if not api_key or api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin API key")


class ActivateRequest(BaseModel):
    key: str


# ─── user API ───

@app.post("/activate")
def activate(req: ActivateRequest):
    try:
        return create_instance(req.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trial/status")
def trial_status():
    return get_trial_queue_status()


@app.post("/api/trial/create")
def trial_create(request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    try:
        return create_trial_instance(client_ip)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trial/activity/{instance_id}")
def trial_activity(instance_id: str, request: Request):
    """Heartbeat endpoint — ST frontend calls this to signal activity."""
    try:
        update_trial_activity(instance_id)
        return {"ok": True}
    except Exception:
        return {"ok": False}


# ─── admin: summary ───

@app.get("/api/admin/summary")
def admin_summary(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return get_summary()


# ─── admin: settings / API config ───

@app.get("/api/admin/settings/api")
def admin_get_api_config(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return get_api_config_safe()


@app.post("/api/admin/settings/api")
def admin_save_api_config(data: dict, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return update_api_config(data)


@app.post("/api/admin/settings/api/test")
def admin_test_api(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return test_connection()


@app.post("/api/admin/settings/api/test-stream")
def admin_test_stream(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return test_stream()


# ─── admin: instances ───

@app.get("/api/admin/instances")
def admin_list_instances(status: str = "", x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return list_instances(status or None)


@app.get("/api/admin/instances/{instance_id}")
def admin_get_instance(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    inst = get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    # Mask password for admin listing
    pw = inst.get("password", "")
    if pw and len(pw) > 4:
        inst["password_masked"] = pw[:2] + "*" * (len(pw) - 4) + pw[-2:]
    return inst


@app.post("/api/admin/instances/{instance_id}/start")
def admin_start(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    try:
        start_instance(instance_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/instances/{instance_id}/stop")
def admin_stop(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    stop_instance(instance_id)
    return {"status": "ok"}


@app.post("/api/admin/instances/{instance_id}/restart")
def admin_restart(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return restart_instance(instance_id)


class RenewRequest(BaseModel):
    days: int = 30


@app.post("/api/admin/instances/{instance_id}/renew")
def admin_renew(instance_id: str, req: RenewRequest = RenewRequest(), x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    try:
        return renew_instance(instance_id, days=req.days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/instances/{instance_id}")
def admin_delete(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    try:
        delete_instance(instance_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/instances/{instance_id}/logs")
def admin_logs(instance_id: str, tail: int = 100, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return {"logs": get_instance_logs(instance_id, tail)}


@app.get("/api/admin/instances/{instance_id}/inspect")
def admin_inspect(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return get_instance_inspect(instance_id)


@app.post("/api/admin/instances/{instance_id}/check")
def admin_check(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return check_instance(instance_id)


@app.post("/api/admin/instances/{instance_id}/apply-api-config")
def admin_apply_api(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    try:
        return apply_api_config(instance_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/instances/apply-api-config-all")
def admin_apply_api_all(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return apply_api_config_all()


# ─── admin: keys ───

class KeyCreateRequest(BaseModel):
    count: int = 1
    days: int = 30
    plan: str = "default"


@app.post("/api/admin/keys")
def admin_create_keys(req: KeyCreateRequest, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    keys = create_keys(count=req.count, days=req.days, plan=req.plan)
    return {"keys": keys}


@app.get("/api/admin/keys")
def admin_list_keys(status: str = "", x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return list_keys(status or None)


@app.post("/api/admin/keys/{key_id}/disable")
def admin_disable_key(key_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    disable_key(key_id)
    return {"status": "ok"}


@app.post("/api/admin/keys/{key_id}/enable")
def admin_enable_key(key_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    enable_key(key_id)
    return {"status": "ok"}


@app.delete("/api/admin/keys/{key_id}")
def admin_delete_key(key_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    delete_key(key_id)
    return {"status": "ok"}


# ─── admin: Cloudflare ───

@app.get("/api/admin/cloudflare/settings")
def admin_cf_settings(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return get_cf_settings()


@app.post("/api/admin/cloudflare/settings")
def admin_cf_save(data: dict, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return update_cf_settings(data)


@app.post("/api/admin/cloudflare/test-token")
def admin_cf_test_token(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return test_token()


@app.get("/api/admin/cloudflare/zones")
def admin_cf_zones(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return list_zones()


@app.get("/api/admin/cloudflare/verify-zone")
def admin_cf_verify_zone(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return verify_zone()


@app.post("/api/admin/cloudflare/test-record")
def admin_cf_test_record(data: dict, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    name = data.get("name", "test")
    content = data.get("content", "127.0.0.1")
    return create_test_record(name, content)


@app.delete("/api/admin/cloudflare/test-record/{record_id}")
def admin_cf_delete_test_record(record_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return delete_test_record(record_id)


@app.post("/api/admin/instances/{instance_id}/sync-domain")
def admin_sync_domain(instance_id: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    inst = get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    if not is_cf_enabled():
        raise HTTPException(status_code=400, detail="Cloudflare 未启用")
    try:
        cf = create_dns_record(instance_id)
        import subprocess
        subprocess.run(["docker", "restart", inst["container_name"]], capture_output=True, timeout=10)
        # Update DB
        with get_db() as conn:
            conn.execute(
                "UPDATE instances SET cf_record_id=?, custom_domain=?, domain=? WHERE instance_id=?",
                (cf["record_id"], cf["name"], cf["name"], instance_id),
            )
        regenerate()
        return {"ok": True, "cf_record_id": cf["record_id"], "domain": cf["name"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── admin: security ───

@app.get("/api/admin/security/docker")
def admin_security_docker(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return security_audit()


# ─── admin: health ───

@app.get("/api/admin/health/docker")
def health_docker(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=5)
        return {"ok": True, "message": "Docker 正常"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/admin/health/traefik")
def health_traefik(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return {"ok": True, "message": "Traefik routes active"}


@app.get("/api/admin/health/manager")
def health_manager(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    return {"ok": True, "uptime": "running"}


@app.get("/api/admin/health/templates")
def health_templates(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    tpl = BASE_DIR / "templates" / "sillytavern"
    data = tpl / "data" / "default-user"
    config_tpl = tpl / "config" / "config.yaml.tpl"
    ok = tpl.exists() and config_tpl.exists() and data.exists()
    has_pk = data.exists() and any("PROXY_API_KEY" in f.read_text(errors="ignore") for f in data.rglob("*") if f.is_file() and f.suffix in (".json",))
    has_m  = data.exists() and any("API_MODEL" in f.read_text(errors="ignore") for f in data.rglob("*") if f.is_file() and f.suffix in (".json",))
    has_url = data.exists() and any(x in f.read_text(errors="ignore") for f in data.rglob("*") if f.is_file() and f.suffix in (".json",) for x in ("API_BASE_URL", "MANAGER_PROXY_URL"))
    return {
        "ok": ok and has_pk and has_m and has_url,
        "templates_exist": tpl.exists(),
        "config_tpl_exists": config_tpl.exists(),
        "data_default_user_exists": data.exists(),
        "has_proxy_key_placeholder": has_pk,
        "has_api_model_placeholder": has_m,
        "has_api_base_url_placeholder": has_url,
    }


# ─── admin: backup ───

BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/admin/backup/create")
def backup_create(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"st-cloud-backup-{ts}"
    path = BACKUP_DIR / name
    path.mkdir(parents=True, exist_ok=True)

    db_path = BASE_DIR / "data.db"
    if db_path.exists():
        shutil.copy2(str(db_path), str(path / "data.db"))
    users_path = BASE_DIR / "users"
    if users_path.exists():
        shutil.copytree(str(users_path), str(path / "users"),
                        ignore=shutil.ignore_patterns(".gitkeep"))
    import tarfile, os
    tgz_path = BACKUP_DIR / f"{name}.tar.gz"
    with tarfile.open(tgz_path, "w:gz") as tar:
        tar.add(str(path), arcname=name)
    shutil.rmtree(str(path))
    return {"ok": True, "name": f"{name}.tar.gz", "size": os.path.getsize(tgz_path)}


@app.get("/api/admin/backup/list")
def backup_list(x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    files = sorted(BACKUP_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat()} for f in files[:30]]


@app.delete("/api/admin/backup/{name}")
def backup_delete(name: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    path.unlink()
    return {"status": "ok"}


@app.post("/api/admin/backup/{name}/restore")
def backup_restore(name: str, x_api_key: str | None = Header(None)):
    _verify_admin(x_api_key)
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    import tarfile, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(tmp)
        extracted = Path(tmp) / path.stem  # strip .tar.gz
        if not extracted.exists():
            extracted = next(Path(tmp).iterdir())
        # Restore DB
        db_src = extracted / "data.db"
        if db_src.exists():
            shutil.copy2(str(db_src), str(BASE_DIR / "data.db"))
        # Restore users
        users_src = extracted / "users"
        if users_src.exists():
            users_dst = BASE_DIR / "users"
            if users_dst.exists():
                shutil.rmtree(str(users_dst))
            shutil.copytree(str(users_src), str(users_dst))
    # Re-init to apply any migrations
    init_db()
    return {"ok": True, "message": f"已从 {name} 恢复，请重启 Manager"}


# ─── API Proxy routes (for ST instances) ───

@app.api_route("/v1/chat/completions", methods=["POST", "OPTIONS"])
async def v1_chat_completions(request: Request):
    return await proxy_chat_completions(request)


@app.api_route("/v1/models", methods=["GET", "OPTIONS"])
async def v1_models(request: Request):
    return await proxy_models(request)


# ─── frontend pages ───

@app.get("/")
def root():
    return RedirectResponse(url="/activate")


@app.get("/activate")
def activate_page():
    return FileResponse(str(static_dir / "activate.html"))


@app.get("/admin")
def admin_page():
    return FileResponse(str(static_dir / "admin.html"))
