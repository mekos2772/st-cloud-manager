"""Admin routes — instances, keys, settings, Cloudflare, health, backup."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from manager.config import BASE_DIR, DOCKER_NETWORK, DOCKER_IMAGE
from manager.db import get_db
from manager.router_service import sync_routes_safely, get_runtime_service
from manager.routes.dependencies import verify_admin
from manager.services.instance_orchestrator import (
    start_instance,
    stop_instance,
    restart_instance,
    renew_instance,
    delete_instance,
    get_instance,
    list_instances,
    apply_api_config,
    apply_api_config_all,
    get_summary,
    check_instance,
    get_instance_logs,
    get_instance_inspect,
)
from manager.key_service import create_keys, list_keys, disable_key, enable_key, delete_key
from manager.settings_service import get_api_config_safe, update_api_config
from manager.api_test_service import test_connection, test_stream
from manager.cloudflare_service import (
    get_cf_settings, update_cf_settings, test_token, list_zones, verify_zone,
    create_test_record, delete_test_record, create_dns_record, is_cf_enabled,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
BACKUP_DIR = BASE_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _rmtree(path: Path):
    """shutil.rmtree that handles Windows read-only files (.git pack files etc.)."""
    if not path.exists():
        return
    def _onerror(func, fp, exc):
        os.chmod(fp, stat.S_IWRITE)
        func(fp)
    shutil.rmtree(str(path), onerror=_onerror)


# ─── summary ───

@router.get("/summary")
def admin_summary(_: None = Depends(verify_admin)):
    return get_summary()


# ─── settings / API config ───

@router.get("/settings/api")
def admin_get_api_config(_: None = Depends(verify_admin)):
    return get_api_config_safe()


@router.post("/settings/api")
def admin_save_api_config(data: dict, _: None = Depends(verify_admin)):
    return update_api_config(data)


@router.post("/settings/api/test")
def admin_test_api(_: None = Depends(verify_admin)):
    return test_connection()


@router.post("/settings/api/test-stream")
def admin_test_stream(_: None = Depends(verify_admin)):
    return test_stream()


# ─── instances ───

@router.get("/instances")
def admin_list_instances(status: str = "", _: None = Depends(verify_admin)):
    return list_instances(status or None)


@router.get("/instances/{instance_id}")
def admin_get_instance(instance_id: str, _: None = Depends(verify_admin)):
    inst = get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    pw = inst.get("password", "")
    if pw and len(pw) > 4:
        inst["password_masked"] = pw[:2] + "*" * (len(pw) - 4) + pw[-2:]
    return inst


@router.post("/instances/{instance_id}/start")
def admin_start(instance_id: str, _: None = Depends(verify_admin)):
    try:
        start_instance(instance_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/instances/{instance_id}/stop")
def admin_stop(instance_id: str, _: None = Depends(verify_admin)):
    stop_instance(instance_id)
    return {"status": "ok"}


@router.post("/instances/{instance_id}/restart")
def admin_restart(instance_id: str, _: None = Depends(verify_admin)):
    return restart_instance(instance_id)


class RenewRequest(BaseModel):
    days: int = 30


@router.post("/instances/{instance_id}/renew")
def admin_renew(instance_id: str, req: RenewRequest = RenewRequest(), _: None = Depends(verify_admin)):
    try:
        return renew_instance(instance_id, days=req.days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/instances/{instance_id}")
def admin_delete(instance_id: str, _: None = Depends(verify_admin)):
    try:
        delete_instance(instance_id)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/instances/{instance_id}/logs")
def admin_logs(instance_id: str, tail: int = 100, _: None = Depends(verify_admin)):
    return {"logs": get_instance_logs(instance_id, tail)}


@router.get("/instances/{instance_id}/inspect")
def admin_inspect(instance_id: str, _: None = Depends(verify_admin)):
    return get_instance_inspect(instance_id)


@router.post("/instances/{instance_id}/check")
def admin_check(instance_id: str, _: None = Depends(verify_admin)):
    return check_instance(instance_id)


@router.post("/instances/{instance_id}/apply-api-config")
def admin_apply_api(instance_id: str, _: None = Depends(verify_admin)):
    try:
        return apply_api_config(instance_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/instances/apply-api-config-all")
def admin_apply_api_all(_: None = Depends(verify_admin)):
    return apply_api_config_all()


# ─── keys ───

class KeyCreateRequest(BaseModel):
    count: int = 1
    days: int = 30
    plan: str = "default"


@router.post("/keys")
def admin_create_keys(req: KeyCreateRequest, _: None = Depends(verify_admin)):
    keys = create_keys(count=req.count, days=req.days, plan=req.plan)
    return {"keys": keys}


@router.get("/keys")
def admin_list_keys(status: str = "", _: None = Depends(verify_admin)):
    return list_keys(status or None)


@router.post("/keys/{key_id}/disable")
def admin_disable_key(key_id: str, _: None = Depends(verify_admin)):
    disable_key(key_id)
    return {"status": "ok"}


@router.post("/keys/{key_id}/enable")
def admin_enable_key(key_id: str, _: None = Depends(verify_admin)):
    enable_key(key_id)
    return {"status": "ok"}


@router.delete("/keys/{key_id}")
def admin_delete_key(key_id: str, _: None = Depends(verify_admin)):
    delete_key(key_id)
    return {"status": "ok"}


# ─── Cloudflare ───

@router.get("/cloudflare/settings")
def admin_cf_settings(_: None = Depends(verify_admin)):
    return get_cf_settings()


@router.post("/cloudflare/settings")
def admin_cf_save(data: dict, _: None = Depends(verify_admin)):
    return update_cf_settings(data)


@router.post("/cloudflare/test-token")
def admin_cf_test_token(_: None = Depends(verify_admin)):
    return test_token()


@router.get("/cloudflare/zones")
def admin_cf_zones(_: None = Depends(verify_admin)):
    return list_zones()


@router.get("/cloudflare/verify-zone")
def admin_cf_verify_zone(_: None = Depends(verify_admin)):
    return verify_zone()


@router.post("/cloudflare/test-record")
def admin_cf_test_record(data: dict, _: None = Depends(verify_admin)):
    name = data.get("name", "test")
    content = data.get("content", "127.0.0.1")
    return create_test_record(name, content)


@router.delete("/cloudflare/test-record/{record_id}")
def admin_cf_delete_test_record(record_id: str, _: None = Depends(verify_admin)):
    return delete_test_record(record_id)


@router.post("/instances/{instance_id}/sync-domain")
def admin_sync_domain(instance_id: str, _: None = Depends(verify_admin)):
    inst = get_instance(instance_id)
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    if not is_cf_enabled():
        raise HTTPException(status_code=400, detail="Cloudflare 未启用")
    try:
        cf = create_dns_record(instance_id)
        restart_instance(instance_id)
        with get_db() as conn:
            conn.execute(
                "UPDATE instances SET cf_record_id=?, custom_domain=?, domain=? WHERE instance_id=?",
                (cf["record_id"], cf["name"], cf["name"], instance_id),
            )
        sync_routes_safely("sync-domain")
        return {"ok": True, "cf_record_id": cf["record_id"], "domain": cf["name"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── security ───

@router.get("/security/docker")
def admin_security_docker(_: None = Depends(verify_admin)):
    return get_runtime_service().security_audit()


# ─── health ───

@router.get("/health/docker")
def health_docker(_: None = Depends(verify_admin)):
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=5)
        return {"ok": True, "message": "Docker 正常"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/health/traefik")
def health_traefik(_: None = Depends(verify_admin)):
    return {"ok": True, "message": "Traefik routes active"}


@router.get("/health/manager")
def health_manager(_: None = Depends(verify_admin)):
    return {"ok": True, "uptime": "running"}


@router.get("/health/templates")
def health_templates(_: None = Depends(verify_admin)):
    tpl = BASE_DIR / "templates" / "sillytavern"
    data = tpl / "data" / "default-user"
    config_tpl = tpl / "config" / "config.yaml.tpl"
    ok = tpl.exists() and config_tpl.exists() and data.exists()

    def _any_file_contains(root: Path, needle: str) -> bool:
        if not root.exists():
            return False
        for f in root.rglob("*"):
            if f.is_file() and f.suffix in (".json",):
                try:
                    if needle in f.read_text(errors="ignore"):
                        return True
                except Exception:
                    pass
        return False

    has_pk = _any_file_contains(data, "PROXY_API_KEY")
    has_m = _any_file_contains(data, "API_MODEL")
    has_url = _any_file_contains(data, "API_BASE_URL") or _any_file_contains(data, "MANAGER_PROXY_URL")

    return {
        "ok": ok and has_pk and has_m and has_url,
        "templates_exist": tpl.exists(),
        "config_tpl_exists": config_tpl.exists(),
        "data_default_user_exists": data.exists(),
        "has_proxy_key_placeholder": has_pk,
        "has_api_model_placeholder": has_m,
        "has_api_base_url_placeholder": has_url,
    }


# ─── backup ───

@router.post("/backup/create")
def backup_create(_: None = Depends(verify_admin)):
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
    import tarfile
    tgz_path = BACKUP_DIR / f"{name}.tar.gz"
    with tarfile.open(tgz_path, "w:gz") as tar:
        tar.add(str(path), arcname=name)
    _rmtree(path)
    return {"ok": True, "name": f"{name}.tar.gz", "size": os.path.getsize(tgz_path)}

@router.get("/backup/list")
def backup_list(_: None = Depends(verify_admin)):
    files = sorted(BACKUP_DIR.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size, "mtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat()} for f in files[:30]]


@router.delete("/backup/{name}")
def backup_delete(name: str, _: None = Depends(verify_admin)):
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    path.unlink()
    return {"status": "ok"}


@router.post("/backup/{name}/restore")
def backup_restore(name: str, _: None = Depends(verify_admin)):
    path = BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    import tarfile, tempfile
    from manager.db import init_db
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(path, "r:gz") as tar:
            tar.extractall(tmp)
        extracted = Path(tmp) / path.stem
        if not extracted.exists():
            extracted = next(Path(tmp).iterdir())
        db_src = extracted / "data.db"
        if db_src.exists():
            shutil.copy2(str(db_src), str(BASE_DIR / "data.db"))
        users_src = extracted / "users"
        if users_src.exists():
            users_dst = BASE_DIR / "users"
            if users_dst.exists():
                _rmtree(users_dst)
            shutil.copytree(str(users_src), str(users_dst))
    init_db()
    return {"ok": True, "message": f"已从 {name} 恢复，请重启 Manager"}
