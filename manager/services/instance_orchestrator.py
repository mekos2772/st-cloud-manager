"""Instance lifecycle orchestrator — create / renew / delete / start / stop.

All business-level instance lifecycle operations live here.
This module does NOT know about trial queue, idle detection, or trial-specific policies.
"""
from __future__ import annotations

import re
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

INSTANCE_ID_RE = re.compile(r"^[a-z0-9]{6,12}$")

from manager.db import get_db
from manager.config import (
    USERS_DIR, ARCHIVE_DIR, TEMPLATES_DIR,
    DOCKER_NETWORK, DOCKER_IMAGE, DOCKER_MEMORY,
    DOMAIN_SUFFIX, PUBLIC_SCHEME,
    TRAEFIK_ENTRYPOINT, TRAEFIK_CERT_RESOLVER, TRAEFIK_TLS,
    DEFAULT_DAYS,
    ROUTING_MODE, BASE_DOMAIN, PATH_PREFIX_LENGTH,
    LOCAL_BACKEND_HOST,
)
from manager.template_service import (
    copy_template, render_config, archive_instance,
)
from manager.key_service import validate_key, mark_key_used
from manager.proxy_service import create_proxy_key, delete_proxy_key
from manager.settings_service import get_all_settings
from manager.cloudflare_service import is_cf_enabled, create_dns_record, delete_dns_record
from manager.instance_model import build_access_url
from manager.router_service import get_runtime_service, sync_routes
from manager.instance_repository import (
    get_instance as repo_get_instance,
    insert_instance,
    insert_trial_instance,
    list_instances as repo_list_instances,
    mark_expired,
    renew_instance_record,
    update_api_key,
    update_status,
    update_web_check,
)
from manager.repositories.summary_repository import get_summary as summary_get_summary

ID_LENGTH = 6
USERNAME_LENGTH = 8
PASSWORD_LENGTH = 12


# ─── helpers ───

def _get_runtime_svc():
    return get_runtime_service()


def _create_container(**kwargs):
    return _get_runtime_svc().create_container(**kwargs)


def _stop_container(name: str):
    return _get_runtime_svc().stop_container(name)


def _start_container(name: str):
    return _get_runtime_svc().start_container(name)


def _restart_container(name: str):
    return _get_runtime_svc().restart_container(name)


def _remove_container(name: str):
    return _get_runtime_svc().remove_container(name)


def _health_check_container(domain: str, timeout: int = 60, path_prefix: str = ""):
    return _get_runtime_svc().health_check_container(domain, timeout, path_prefix)


def _regenerate():
    return sync_routes()


def _generate_id() -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(ID_LENGTH))


def _generate_domain(instance_id: str) -> str:
    return f"{instance_id}.{DOMAIN_SUFFIX}"


def _generate_path_prefix(instance_id: str) -> str:
    extra = "".join(secrets.choice(string.ascii_lowercase + string.digits)
                    for _ in range(PATH_PREFIX_LENGTH - len(instance_id)))
    return f"/st-{instance_id}{extra}"


def _resolve_routing_mode() -> str:
    s = get_all_settings()
    return s.get("routing_mode", ROUTING_MODE)


def _resolve_base_domain() -> str:
    s = get_all_settings()
    return s.get("base_domain", BASE_DOMAIN) or BASE_DOMAIN


def _resolve_route(instance_id: str, *, require_cf: bool = True) -> dict:
    settings = get_all_settings()
    routing_mode = _resolve_routing_mode()
    cf_record_id = None
    custom_domain = None
    path_prefix = ""

    if routing_mode == "path":
        base_domain = _resolve_base_domain()
        if not base_domain:
            raise RuntimeError("路由模式为 path，但 base_domain 未配置")
        path_prefix = _generate_path_prefix(instance_id)
        domain = base_domain
    elif settings.get("domain_mode") == "cloudflare":
        domain = _generate_domain(instance_id)
        if not is_cf_enabled():
            if require_cf:
                raise RuntimeError("域名模式为 cloudflare，但 CF Token / Zone ID 未配置")
        else:
            try:
                cf_result = create_dns_record(instance_id)
                cf_record_id = cf_result["record_id"]
                custom_domain = cf_result["name"]
                domain = custom_domain
            except Exception as e:
                if require_cf:
                    raise RuntimeError(f"Cloudflare DNS 创建失败: {e}")
    else:
        domain = _generate_domain(instance_id)

    return {
        "routing_mode": routing_mode,
        "domain": domain,
        "base_domain": domain if routing_mode == "path" else "",
        "path_prefix": path_prefix,
        "cf_record_id": cf_record_id,
        "custom_domain": custom_domain,
    }


def _generate_username() -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(USERNAME_LENGTH))


def _generate_password() -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(PASSWORD_LENGTH))


def _container_name(instance_id: str) -> str:
    return f"st-{instance_id}"


def _api_template_vars(instance_id: str, username: str, password: str, api_key: str,
                       path_prefix: str = "") -> dict:
    from manager.settings_service import get_effective_api_settings
    api = get_effective_api_settings()
    s = get_all_settings()
    return {
        "INSTANCE_ID": instance_id,
        "USERNAME": username,
        "PASSWORD": password,
        "API_BASE_URL": api["api_base_url"],
        "API_MODEL": api["api_model"],
        "PROXY_API_KEY": api_key,
        "MASTER_API_KEY": api["upstream_api_key"],
        "MANAGER_PROXY_URL": api["manager_proxy_url"],
        "API_HOST": api["api_host"],
        "STREAMING_ENABLED": api["streaming_enabled"],
        "DEFAULT_TEMPERATURE": api["default_temperature"],
        "DEFAULT_CONTEXT_SIZE": api["default_context_size"],
        "DEFAULT_MAX_TOKENS": api["default_max_tokens"],
        "BASE_DOMAIN": DOMAIN_SUFFIX,
        "PUBLIC_SCHEME": PUBLIC_SCHEME,
        "PATH_PREFIX": path_prefix,
        "ROUTING_MODE": s.get("routing_mode", ROUTING_MODE),
        "FULL_DOMAIN": _resolve_base_domain() if s.get("routing_mode") == "path" else DOMAIN_SUFFIX,
        "LOCAL_BACKEND_HOST": LOCAL_BACKEND_HOST,
    }


def _wait_st_initialized(instance_id: str, timeout: int = 60) -> bool:
    user_dir = USERS_DIR / instance_id / "data" / "default-user"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if user_dir.exists() and any(user_dir.iterdir()):
            return True
        time.sleep(2)
    return False


def _rollback(instance_id: str, container: str):
    try:
        _remove_container(container)
    except Exception as e:
        print(f"[rollback] remove container failed for {instance_id}: {e}")
    user_dir = USERS_DIR / instance_id
    if user_dir.exists():
        import shutil
        archive = ARCHIVE_DIR / f"{instance_id}-failed-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(user_dir), str(archive))
        except Exception as e:
            print(f"[rollback] archive failed for {instance_id}: {e}")


def _render_initial_config(config_dir: Path, vars_: dict):
    config_tpl = config_dir / "config.yaml"
    if not config_tpl.exists():
        return
    content = config_tpl.read_text(encoding="utf-8")
    for k, v in vars_.items():
        content = content.replace("{{" + k + "}}", v)
    config_tpl.write_text(content, encoding="utf-8")


def _create_runtime_instance(
    *,
    container: str,
    domain: str,
    routing_mode: str,
    path_prefix: str,
    base_domain: str,
    config_dir: Path,
    data_dir: Path,
    plugins_dir: Path,
    is_trial: bool = False,
):
    ok = _create_container(
        container_name=container,
        domain=domain,
        memory=DOCKER_MEMORY,
        network=DOCKER_NETWORK,
        image=DOCKER_IMAGE,
        entrypoint=TRAEFIK_ENTRYPOINT,
        cert_resolver=TRAEFIK_CERT_RESOLVER,
        tls_enabled=TRAEFIK_TLS,
        user_config_dir=str(config_dir),
        user_data_dir=str(data_dir),
        user_plugins_dir=str(plugins_dir),
        routing_mode=routing_mode,
        path_prefix=path_prefix,
        base_domain=base_domain if routing_mode == "path" else "",
        is_trial=is_trial,
    )
    if not ok:
        raise RuntimeError("Failed to create Docker container")


def _initialize_instance_runtime(
    *,
    instance_id: str,
    container: str,
    domain: str,
    path_prefix: str,
    instance_dir: Path,
    vars_: dict,
) -> tuple[bool, str | None]:
    if not _wait_st_initialized(instance_id, timeout=60):
        raise RuntimeError("ST initialization timed out")

    _stop_container(container)

    warning = None
    api_template = TEMPLATES_DIR / "sillytavern" / "data" / "default-user"
    if not api_template.exists():
        warning = "未检测到 API 配置模板，请手动配置一次酒馆并复制 data/default-user 作为模板。"
    else:
        render_config(instance_dir, vars_)

    _start_container(container)
    ready = _health_check_container(domain, timeout=60, path_prefix=path_prefix)
    return ready, warning


# ─── create ───

def create_instance(activation_key: str) -> dict:
    steps_done = []
    instance_id = None
    container = None
    cf_record_id = None
    proxy_key_alias = ""

    try:
        key_info = validate_key(activation_key)
        if not key_info:
            raise ValueError("Invalid or already used activation key")
        steps_done.append("validate key")

        instance_id = _generate_id()
        if not INSTANCE_ID_RE.match(instance_id):
            raise ValueError(f"Invalid instance_id format: {instance_id}")

        route = _resolve_route(instance_id, require_cf=True)
        routing_mode = route["routing_mode"]
        domain = route["domain"]
        base_domain = route["base_domain"]
        path_prefix = route["path_prefix"]
        cf_record_id = route["cf_record_id"]
        custom_domain = route["custom_domain"]
        cf_warning = None

        username = _generate_username()
        password = _generate_password()
        container = _container_name(instance_id)
        days = key_info.get("days", DEFAULT_DAYS)
        api_key, proxy_key_alias = create_proxy_key(instance_id)
        steps_done.append("generate instance")

        config_dir, data_dir, plugins_dir = copy_template(instance_id)
        steps_done.append("copy template")

        instance_dir = USERS_DIR / instance_id
        vars_ = _api_template_vars(instance_id, username, password, api_key, path_prefix)
        _render_initial_config(config_dir, vars_)
        steps_done.append("render config")

        _create_runtime_instance(
            container=container,
            domain=domain,
            routing_mode=routing_mode,
            path_prefix=path_prefix,
            base_domain=base_domain,
            config_dir=config_dir,
            data_dir=data_dir,
            plugins_dir=plugins_dir,
        )
        steps_done.append("docker create")

        ready, warning = _initialize_instance_runtime(
            instance_id=instance_id,
            container=container,
            domain=domain,
            path_prefix=path_prefix,
            instance_dir=instance_dir,
            vars_=vars_,
        )
        steps_done.append("wait init")
        steps_done.append("docker stop")
        steps_done.append("apply api config")
        steps_done.append("docker restart")
        steps_done.append("wait ready")

        url = build_access_url(domain, path_prefix)
        steps_done.append("api test")
        steps_done.append("stream test")

        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        insert_instance({
            "instance_id": instance_id,
            "domain": domain,
            "container_name": container,
            "username": username,
            "password": password,
            "api_key": api_key,
            "ready": ready,
            "cf_record_id": cf_record_id,
            "custom_domain": custom_domain,
            "path_prefix": path_prefix,
            "proxy_key_alias": proxy_key_alias,
            "created_at": now,
            "expires_at": expires,
        })
        steps_done.append("db insert")

        mark_key_used(activation_key, instance_id)
        steps_done.append("mark key used")

        _regenerate()

        result = {
            "instance_id": instance_id,
            "url": url,
            "username": username,
            "password": password,
            "expires_at": expires,
            "ready": ready,
            "api_status": "unchecked",
            "stream_status": "unchecked",
            "steps": steps_done,
        }
        if warning:
            result["warning"] = warning
        if cf_warning:
            result["warning"] = (result.get("warning", "") + " | " + cf_warning).strip(" |")
        return result

    except ValueError:
        raise
    except Exception as e:
        if instance_id:
            if cf_record_id:
                try:
                    delete_dns_record(cf_record_id)
                except Exception as dns_err:
                    print(f"[create] CF DNS cleanup failed for {instance_id}: {dns_err}")
            try:
                delete_proxy_key(proxy_key_alias)
            except Exception as pk_err:
                print(f"[create] proxy key cleanup failed for {instance_id}: {pk_err}")
            _rollback(instance_id, container or _container_name(instance_id))
        raise RuntimeError(f"create failed at '{steps_done[-1] if steps_done else 'init'}': {e}")


# ─── trial create helper (shared with trial_service) ───

def create_trial_instance_raw(client_ip: str) -> dict:
    instance_id = None
    container = None
    cf_record_id = None
    proxy_key_alias = ""

    try:
        instance_id = _generate_id()
        if not INSTANCE_ID_RE.match(instance_id):
            raise ValueError(f"Invalid instance_id: {instance_id}")

        route = _resolve_route(instance_id, require_cf=False)
        routing_mode = route["routing_mode"]
        domain = route["domain"]
        base_domain = route["base_domain"]
        path_prefix = route["path_prefix"]
        cf_record_id = route["cf_record_id"]
        custom_domain = route["custom_domain"]

        username = _generate_username()
        password = _generate_password()
        container = _container_name(instance_id)
        api_key, proxy_key_alias = create_proxy_key(instance_id)

        config_dir, data_dir, plugins_dir = copy_template(instance_id)
        instance_dir = USERS_DIR / instance_id
        vars_ = _api_template_vars(instance_id, username, password, api_key, path_prefix)

        _render_initial_config(config_dir, vars_)

        _create_runtime_instance(
            container=container,
            domain=domain,
            routing_mode=routing_mode,
            path_prefix=path_prefix,
            base_domain=base_domain,
            config_dir=config_dir,
            data_dir=data_dir,
            plugins_dir=plugins_dir,
            is_trial=True,
        )

        ready, _ = _initialize_instance_runtime(
            instance_id=instance_id,
            container=container,
            domain=domain,
            path_prefix=path_prefix,
            instance_dir=instance_dir,
            vars_=vars_,
        )
        url = build_access_url(domain, path_prefix)

        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        insert_trial_instance({
            "instance_id": instance_id,
            "domain": domain,
            "container_name": container,
            "username": username,
            "password": password,
            "api_key": api_key,
            "ready": ready,
            "cf_record_id": cf_record_id,
            "custom_domain": custom_domain,
            "path_prefix": path_prefix,
            "last_activity": now,
            "client_ip": client_ip,
            "proxy_key_alias": proxy_key_alias,
            "created_at": now,
            "expires_at": expires,
        })

        _regenerate()

        return {
            "instance_id": instance_id,
            "url": url,
            "username": username,
            "password": password,
            "expires_at": expires,
            "ready": ready,
            "is_trial": True,
            "api_key": api_key,
            "proxy_key_alias": proxy_key_alias,
            "cf_record_id": cf_record_id,
        }

    except ValueError:
        raise
    except Exception as e:
        if instance_id:
            if cf_record_id:
                try: delete_dns_record(cf_record_id)
                except Exception as dns_err:
                    print(f"[trial-create] CF DNS cleanup failed for {instance_id}: {dns_err}")
            try: delete_proxy_key(proxy_key_alias)
            except Exception as pk_err:
                print(f"[trial-create] proxy key cleanup failed for {instance_id}: {pk_err}")
            _rollback(instance_id, container or _container_name(instance_id))
        raise RuntimeError(f"Trial create failed: {e}")


# ─── instance operations ───

def stop_instance(instance_id: str):
    container = _container_name(instance_id)
    _stop_container(container)
    update_status(instance_id, "stopped")
    _regenerate()


def start_instance(instance_id: str):
    container = _container_name(instance_id)
    row = get_instance(instance_id)
    if not row:
        raise ValueError(f"Instance not found: {instance_id}")
    if row["status"] == "expired":
        raise ValueError("Cannot start expired instance, renew it first")
    _start_container(container)
    update_status(instance_id, "running")
    _regenerate()


def restart_instance(instance_id: str):
    container = _container_name(instance_id)
    _restart_container(container)
    update_status(instance_id, "running", ready=1)
    _regenerate()
    return {"ok": True}


def renew_instance(instance_id: str, days: int = DEFAULT_DAYS):
    row = get_instance(instance_id)
    if not row:
        raise ValueError(f"Instance not found: {instance_id}")
    current_expires = datetime.fromisoformat(row["expires_at"])
    if current_expires.tzinfo is None:
        current_expires = current_expires.replace(tzinfo=timezone.utc)
    new_expires = max(current_expires, datetime.now(timezone.utc)) + timedelta(days=days)
    expires_str = new_expires.isoformat()

    if row["api_key"]:
        try:
            new_key, new_alias = create_proxy_key(instance_id)
            update_api_key(instance_id, new_key, new_alias)
            instance_dir = USERS_DIR / instance_id
            vars_ = _api_template_vars(instance_id, row["username"], row["password"],
                                       new_key, row.get("path_prefix", ""))
            render_config(instance_dir, vars_)
        except Exception:
            pass

    _start_container(_container_name(instance_id))
    renew_instance_record(instance_id, expires_str)
    _regenerate()
    return {"instance_id": instance_id, "expires_at": expires_str}


def delete_instance(instance_id: str):
    row = get_instance(instance_id)
    if not row:
        raise ValueError(f"Instance not found: {instance_id}")
    container = _container_name(instance_id)
    _remove_container(container)

    if row["api_key"]:
        try:
            delete_proxy_key(row.get("proxy_key_alias", ""))
        except Exception:
            pass

    cf_sync = get_all_settings().get("cf_sync_delete", "true")
    cf_record = row.get("cf_record_id")
    if cf_sync == "true" and cf_record:
        try:
            delete_dns_record(cf_record)
        except Exception:
            pass

    archive_instance(instance_id, ARCHIVE_DIR)
    update_status(instance_id, "deleted")
    _regenerate()


def get_instance(instance_id: str) -> dict | None:
    return repo_get_instance(instance_id)


def list_instances(status: str | None = None) -> list[dict]:
    return repo_list_instances(status)


def apply_api_config(instance_id: str) -> dict:
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")
    instance_dir = USERS_DIR / instance_id
    api_template = TEMPLATES_DIR / "sillytavern" / "data" / "default-user"
    if not api_template.exists():
        return {"ok": False, "error": "API 配置模板不存在"}

    _stop_container(inst["container_name"])
    vars_ = _api_template_vars(instance_id, inst["username"], inst["password"], inst["api_key"],
                               inst.get("path_prefix", ""))
    render_config(instance_dir, vars_)
    _start_container(inst["container_name"])
    _regenerate()
    return {"ok": True, "message": "API 配置已重新下发并重启容器"}


def apply_api_config_all() -> dict:
    results = []
    insts = list_instances()
    for inst in insts:
        if inst["status"] in ("running", "stopped"):
            try:
                r = apply_api_config(inst["instance_id"])
                results.append({"instance_id": inst["instance_id"], "ok": r["ok"], "message": r.get("message", r.get("error"))})
            except Exception as e:
                results.append({"instance_id": inst["instance_id"], "ok": False, "error": str(e)})
    return {"total": len(results), "results": results}


def check_expired() -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        expired = conn.execute(
            "SELECT * FROM instances WHERE status = 'running' AND expires_at <= ?", (now,),
        ).fetchall()
    for row in expired:
        inst_id = row["instance_id"]
        container = _container_name(inst_id)
        try:
            _stop_container(container)
        except Exception as e:
            print(f"[check_expired] stop failed for {inst_id}: {e}")
        if row["api_key"]:
            try:
                delete_proxy_key(row.get("proxy_key_alias", ""))
            except Exception as e:
                print(f"[check_expired] delete proxy key failed for {inst_id}: {e}")
        # Clean up Cloudflare DNS — expired instances should not keep DNS records alive
        cf_sync = get_all_settings().get("cf_sync_delete", "true")
        cf_record = row.get("cf_record_id")
        if cf_sync == "true" and cf_record:
            try:
                delete_dns_record(cf_record)
            except Exception as e:
                print(f"[check_expired] delete CF DNS failed for {inst_id}: {e}")
        mark_expired(inst_id)
    _regenerate()
    return len(expired)


def check_crashed() -> int:
    restarted = 0
    with get_db() as conn:
        rows = conn.execute(
            "SELECT instance_id, container_name FROM instances WHERE status='running'"
        ).fetchall()

    for row in rows:
        inst = dict(row)
        cid = inst["container_name"]
        try:
            svc = _get_runtime_svc()
            if not svc.process_exists(inst["instance_id"]):
                print(f"[scheduler] instance {inst['instance_id']} process dead, restarting...")
                _start_container(cid)
                restarted += 1
        except Exception:
            pass

    return restarted


def get_summary() -> dict:
    return summary_get_summary()


def check_instance(instance_id: str) -> dict:
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")

    results = {}
    try:
        ready = _health_check_container(inst["domain"], timeout=10, path_prefix=inst.get("path_prefix", ""))
        update_web_check(
            instance_id,
            "ready" if ready else "failed",
            datetime.now(timezone.utc).isoformat(),
        )
        results["web"] = "ready" if ready else "failed"
    except Exception as e:
        results["web"] = f"error: {e}"

    return results


def get_instance_logs(instance_id: str, tail: int = 100) -> str:
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")
    svc = _get_runtime_svc()
    return svc.get_logs(instance_id, tail)


def get_instance_inspect(instance_id: str) -> dict:
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")

    user_dir = USERS_DIR / instance_id
    svc = _get_runtime_svc()

    info = svc.inspect_container(inst["container_name"])
    container_running = info.get("running", False)
    container_exists_flag = info.get("running", False) or user_dir.exists()

    return {
        "container_exists": container_exists_flag,
        "container_running": container_running,
        "domain": inst["domain"],
        "url": build_access_url(inst["domain"], inst.get("path_prefix", "")),
        "user_dir_exists": user_dir.exists(),
        "config_yaml_exists": (user_dir / "config" / "config.yaml").exists(),
        "default_user_exists": (user_dir / "data" / "default-user").exists(),
        "docker_network": DOCKER_NETWORK,
        "container_name": inst["container_name"],
    }


def release_instance_runtime(instance_id: str):
    """Release runtime resources for an instance (container, proxy key, archive, DB)."""
    inst = get_instance(instance_id)
    if not inst:
        return

    container = _container_name(instance_id)
    try:
        _remove_container(container)
    except Exception:
        pass

    if inst.get("api_key"):
        try:
            delete_proxy_key(inst.get("proxy_key_alias", ""))
        except Exception:
            pass

    archive_instance(instance_id, ARCHIVE_DIR)
    _regenerate()
