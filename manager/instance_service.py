import re
import secrets
import string
import time
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

INSTANCE_ID_RE = re.compile(r"^[a-z0-9]{6,12}$")

from manager.db import get_db
from manager.config import (
    USERS_DIR, ARCHIVE_DIR, TEMPLATES_DIR,
    DOCKER_NETWORK, DOCKER_IMAGE, DOCKER_MEMORY,
    DOMAIN_SUFFIX, PUBLIC_SCHEME,
    TRAEFIK_ENTRYPOINT, TRAEFIK_CERT_RESOLVER, TRAEFIK_TLS,
    API_BASE_URL, API_MODEL, API_HOST, MASTER_API_KEY, MANAGER_PROXY_URL,
    DEFAULT_PLAN, DEFAULT_DAYS,
    ROUTING_MODE, BASE_DOMAIN, PATH_PREFIX_LENGTH,
)
from manager.docker_service import (
    create_container, stop_container, start_container, restart_container,
    remove_container, health_check_container,
)
from manager.template_service import (
    copy_template, render_config, archive_instance,
)
from manager.key_service import validate_key, mark_key_used
from manager.proxy_service import create_proxy_key, delete_proxy_key
from manager.traefik_config_service import regenerate
from manager.settings_service import get_all_settings
from manager.cloudflare_service import is_cf_enabled, create_dns_record, delete_dns_record

ID_LENGTH = 6
USERNAME_LENGTH = 8
PASSWORD_LENGTH = 12

_STEPS = [
    "validate key", "generate instance", "copy template", "render config",
    "docker create", "docker start", "wait init", "docker stop",
    "apply api config", "docker restart", "wait ready", "api test",
    "stream test", "db insert", "mark key used",
]


def _generate_id() -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(ID_LENGTH))


def _generate_domain(instance_id: str) -> str:
    return f"{instance_id}.{DOMAIN_SUFFIX}"


def _generate_path_prefix(instance_id: str) -> str:
    """Generate a complex path prefix for path-based routing."""
    extra = "".join(secrets.choice(string.ascii_lowercase + string.digits)
                    for _ in range(PATH_PREFIX_LENGTH - len(instance_id)))
    return f"/st-{instance_id}{extra}"


def _resolve_routing_mode() -> str:
    """Resolve routing mode from settings (env or DB)."""
    s = get_all_settings()
    return s.get("routing_mode", ROUTING_MODE)


def _resolve_base_domain() -> str:
    """Resolve base domain from settings (env or DB)."""
    s = get_all_settings()
    return s.get("base_domain", BASE_DOMAIN) or BASE_DOMAIN


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
    s = get_all_settings()
    return {
        "INSTANCE_ID": instance_id,
        "USERNAME": username,
        "PASSWORD": password,
        "API_BASE_URL": s["api_base_url"],
        "API_MODEL": s["api_model"],
        "PROXY_API_KEY": api_key,
        "MASTER_API_KEY": MASTER_API_KEY,
        "MANAGER_PROXY_URL": MANAGER_PROXY_URL,
        "API_HOST": API_HOST,
        "STREAMING_ENABLED": s["streaming_enabled"],
        "DEFAULT_TEMPERATURE": s["default_temperature"],
        "DEFAULT_CONTEXT_SIZE": s["default_context_size"],
        "DEFAULT_MAX_TOKENS": s["default_max_tokens"],
        "BASE_DOMAIN": DOMAIN_SUFFIX,
        "PUBLIC_SCHEME": PUBLIC_SCHEME,
        "PATH_PREFIX": path_prefix,
        "ROUTING_MODE": s.get("routing_mode", ROUTING_MODE),
        "FULL_DOMAIN": _resolve_base_domain() if s.get("routing_mode") == "path" else DOMAIN_SUFFIX,
    }


def _wait_st_initialized(instance_id: str, timeout: int = 60) -> bool:
    """Wait until ST creates data/default-user with files."""
    user_dir = USERS_DIR / instance_id / "data" / "default-user"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if user_dir.exists() and any(user_dir.iterdir()):
            return True
        time.sleep(2)
    return False


def _rollback(instance_id: str, container: str):
    """Clean up a failed instance creation."""
    try:
        remove_container(container)
    except Exception:
        pass
    user_dir = USERS_DIR / instance_id
    if user_dir.exists():
        import shutil
        archive = ARCHIVE_DIR / f"{instance_id}-failed-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(user_dir), str(archive))


def create_instance(activation_key: str) -> dict:
    steps_done = []
    instance_id = None
    container = None

    try:
        # validate key (don't mark used yet)
        key_info = validate_key(activation_key)
        if not key_info:
            raise ValueError("Invalid or already used activation key")
        steps_done.append("validate key")

        # generate identifiers
        instance_id = _generate_id()
        if not INSTANCE_ID_RE.match(instance_id):
            raise ValueError(f"Invalid instance_id format: {instance_id}")

        settings = get_all_settings()
        routing_mode = _resolve_routing_mode()
        cf_record_id = None
        custom_domain = None
        cf_warning = None
        path_prefix = ""

        if routing_mode == "path":
            # Path-based routing — domain stores full access URL for uniqueness
            base_domain = _resolve_base_domain()
            if not base_domain:
                raise RuntimeError("路由模式为 path，但 base_domain 未配置")
            path_prefix = _generate_path_prefix(instance_id)
            domain = f"{base_domain}{path_prefix}"
        elif settings.get("domain_mode") == "cloudflare":
            # Subdomain + Cloudflare DNS
            domain = _generate_domain(instance_id)
            if not is_cf_enabled():
                raise RuntimeError("域名模式为 cloudflare，但 CF Token / Zone ID 未配置")
            try:
                cf_result = create_dns_record(instance_id)
                cf_record_id = cf_result["record_id"]
                custom_domain = cf_result["name"]
                domain = custom_domain
            except Exception as e:
                raise RuntimeError(f"Cloudflare DNS 创建失败: {e}")
        else:
            # Subdomain + local
            domain = _generate_domain(instance_id)

        username = _generate_username()
        password = _generate_password()
        container = _container_name(instance_id)
        days = key_info.get("days", DEFAULT_DAYS)
        api_key = create_proxy_key(instance_id)
        steps_done.append("generate instance")

        # copy template
        config_dir, data_dir, plugins_dir = copy_template(instance_id)
        steps_done.append("copy template")

        # render config.yaml (first pass — just BasicAuth)
        instance_dir = USERS_DIR / instance_id
        vars_ = _api_template_vars(instance_id, username, password, api_key, path_prefix)
        # Only render config.yaml now, API template later
        config_tpl = config_dir / "config.yaml"
        if config_tpl.exists():
            content = config_tpl.read_text(encoding="utf-8")
            for k, v in vars_.items():
                content = content.replace("{{" + k + "}}", v)
            config_tpl.write_text(content, encoding="utf-8")
        steps_done.append("render config")

        # create container
        ok = create_container(
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
        )
        if not ok:
            raise RuntimeError("Failed to create Docker container")
        steps_done.append("docker create")

        # wait for ST to initialize data/default-user
        if not _wait_st_initialized(instance_id, timeout=60):
            raise RuntimeError("ST initialization timed out")
        steps_done.append("wait init")

        # stop container and apply full API config
        stop_container(container)
        steps_done.append("docker stop")

        # apply API template (second pass — data/default-user)
        api_template = TEMPLATES_DIR / "sillytavern" / "data" / "default-user"
        warning = None
        if not api_template.exists():
            warning = "未检测到 API 配置模板，请手动配置一次酒馆并复制 data/default-user 作为模板。"
        else:
            render_config(instance_dir, vars_)
        steps_done.append("apply api config")

        # restart container
        start_container(container)
        steps_done.append("docker restart")

        # wait for ST ready (path mode: domain already contains the full URL)
        ready = health_check_container(domain, timeout=60)
        steps_done.append("wait ready")

        # test API and stream
        api_ok = True
        api_error = None
        stream_ok = True
        stream_error = None

        url = f"{PUBLIC_SCHEME}://{domain}"
        steps_done.append("api test")
        steps_done.append("stream test")

        # write to database
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        with get_db() as conn:
            conn.execute(
                """INSERT INTO instances
                   (instance_id, domain, container_name, username, password, api_key,
                    status, ready, api_status, stream_status, web_status,
                    cf_record_id, custom_domain, path_prefix, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'running', ?, 'unchecked', 'unchecked', 'unchecked', ?, ?, ?, ?, ?)""",
                (instance_id, domain, container, username, password, api_key,
                 1 if ready else 0, cf_record_id, custom_domain, path_prefix, now, expires),
            )
        steps_done.append("db insert")

        # mark key used
        mark_key_used(activation_key, instance_id)
        steps_done.append("mark key used")

        # update Traefik
        regenerate()

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

    except Exception as e:
        if instance_id:
            _rollback(instance_id, container or _container_name(instance_id))
        raise RuntimeError(f"create failed at '{steps_done[-1] if steps_done else 'init'}': {e}")


# ─── trial mode ───

def _get_active_trial_count() -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM instances WHERE is_trial=1 AND status='running'"
        ).fetchone()
    return row[0] if row else 0


def _get_trial_by_ip(client_ip: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE is_trial=1 AND client_ip=? AND status='running' ORDER BY created_at DESC LIMIT 1",
            (client_ip,),
        ).fetchone()
    return dict(row) if row else None


def create_trial_instance(client_ip: str) -> dict:
    """Create a trial instance without activation key."""
    settings = get_all_settings()

    if settings.get("trial_enabled", "false") != "true":
        raise ValueError("体验模式未启用")

    # One trial per IP
    existing = _get_trial_by_ip(client_ip)
    if existing:
        raise ValueError(f"您的 IP 已有体验实例 ({existing['instance_id']})，请等待其释放后再创建")

    trial_max = int(settings.get("trial_max_instances", "3"))
    current = _get_active_trial_count()
    if current >= trial_max:
        # Check if queue is enabled
        if settings.get("trial_queue_enabled", "true") == "true":
            return _enqueue_trial(client_ip)
        raise ValueError(f"体验实例已满 ({current}/{trial_max})，请稍后再试")

    # Check resources
    trial_max_mem = int(settings.get("trial_max_memory_pct", "85"))
    from manager.resource_service import can_create_instance
    can, reason = can_create_instance(trial_max_mem, trial_max)
    if not can:
        if settings.get("trial_queue_enabled", "true") == "true":
            return _enqueue_trial(client_ip)
        raise ValueError(reason)

    # Create instance with short expiry (1 day) and trial flag
    return _create_trial_instance_inner(client_ip)


def _create_trial_instance_inner(client_ip: str) -> dict:
    """Internal: create a trial instance."""
    settings = get_all_settings()
    instance_id = None
    container = None
    steps_done = []

    try:
        instance_id = _generate_id()
        if not INSTANCE_ID_RE.match(instance_id):
            raise ValueError(f"Invalid instance_id: {instance_id}")

        routing_mode = _resolve_routing_mode()
        cf_record_id = None
        custom_domain = None
        path_prefix = ""

        if routing_mode == "path":
            base_domain = _resolve_base_domain()
            if not base_domain:
                raise RuntimeError("路由模式为 path，但 base_domain 未配置")
            path_prefix = _generate_path_prefix(instance_id)
            domain = f"{base_domain}{path_prefix}"
        elif settings.get("domain_mode") == "cloudflare":
            domain = _generate_domain(instance_id)
            if is_cf_enabled():
                try:
                    cf_result = create_dns_record(instance_id)
                    cf_record_id = cf_result["record_id"]
                    custom_domain = cf_result["name"]
                    domain = custom_domain
                except Exception:
                    pass
        else:
            domain = _generate_domain(instance_id)

        username = _generate_username()
        password = _generate_password()
        container = _container_name(instance_id)
        api_key = create_proxy_key(instance_id)

        config_dir, data_dir, plugins_dir = copy_template(instance_id)
        instance_dir = USERS_DIR / instance_id
        vars_ = _api_template_vars(instance_id, username, password, api_key, path_prefix)

        config_tpl = config_dir / "config.yaml"
        if config_tpl.exists():
            content = config_tpl.read_text(encoding="utf-8")
            for k, v in vars_.items():
                content = content.replace("{{" + k + "}}", v)
            config_tpl.write_text(content, encoding="utf-8")

        ok = create_container(
            container_name=container, domain=domain, memory=DOCKER_MEMORY,
            network=DOCKER_NETWORK, image=DOCKER_IMAGE,
            entrypoint=TRAEFIK_ENTRYPOINT, cert_resolver=TRAEFIK_CERT_RESOLVER,
            tls_enabled=TRAEFIK_TLS,
            user_config_dir=str(config_dir), user_data_dir=str(data_dir),
            user_plugins_dir=str(plugins_dir),
            routing_mode=routing_mode, path_prefix=path_prefix,
            base_domain=base_domain if routing_mode == "path" else "",
        )
        if not ok:
            raise RuntimeError("Failed to create Docker container")
        steps_done.append("docker create")

        if not _wait_st_initialized(instance_id, timeout=60):
            raise RuntimeError("ST initialization timed out")

        stop_container(container)

        api_template = TEMPLATES_DIR / "sillytavern" / "data" / "default-user"
        if api_template.exists():
            render_config(instance_dir, vars_)

        start_container(container)
        ready = health_check_container(domain, timeout=60)
        url = f"{PUBLIC_SCHEME}://{domain}"

        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        with get_db() as conn:
            conn.execute(
                """INSERT INTO instances
                   (instance_id, domain, container_name, username, password, api_key,
                    status, ready, api_status, stream_status, web_status,
                    cf_record_id, custom_domain, path_prefix, is_trial, last_activity, client_ip,
                    created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'running', ?, 'unchecked', 'unchecked', 'unchecked', ?, ?, ?, 1, ?, ?, ?, ?)""",
                (instance_id, domain, container, username, password, api_key,
                 1 if ready else 0, cf_record_id, custom_domain, path_prefix, now, client_ip, now, expires),
            )

        regenerate()

        return {
            "instance_id": instance_id,
            "url": url,
            "username": username,
            "password": password,
            "expires_at": expires,
            "ready": ready,
            "is_trial": True,
        }

    except Exception as e:
        if instance_id:
            _rollback(instance_id, container or _container_name(instance_id))
        raise RuntimeError(f"Trial create failed: {e}")


def _enqueue_trial(client_ip: str) -> dict:
    """Add trial request to queue when resources are full."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        # Check if this IP is already in queue
        existing = conn.execute(
            "SELECT id FROM trial_queue WHERE client_ip=? AND status='waiting'",
            (client_ip,),
        ).fetchone()
        if existing:
            pos = conn.execute(
                "SELECT COUNT(*) FROM trial_queue WHERE status='waiting' AND id <= ?",
                (existing["id"],),
            ).fetchone()[0]
            return {"queued": True, "position": pos, "message": f"排队中，前方 {pos - 1} 人"}

        conn.execute(
            "INSERT INTO trial_queue (client_ip, status, created_at) VALUES (?, 'waiting', ?)",
            (client_ip, now),
        )
        pos = conn.execute(
            "SELECT COUNT(*) FROM trial_queue WHERE status='waiting'"
        ).fetchone()[0]
    return {"queued": True, "position": pos, "message": f"已加入排队，前方 {pos - 1} 人"}


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
    with get_db() as conn:
        waiting = conn.execute(
            "SELECT * FROM trial_queue WHERE status='waiting' ORDER BY id ASC LIMIT 1"
        ).fetchall()

    for entry in waiting:
        can, _ = can_create_instance(trial_max_mem, trial_max)
        if not can:
            break
        try:
            result = _create_trial_instance_inner(entry["client_ip"])
            with get_db() as conn:
                conn.execute(
                    "UPDATE trial_queue SET status='done', instance_id=?, processed_at=? WHERE id=?",
                    (result["instance_id"], now, entry["id"]),
                )
            created += 1
        except Exception as e:
            with get_db() as conn:
                conn.execute(
                    "UPDATE trial_queue SET status='failed', error=?, processed_at=? WHERE id=?",
                    (str(e)[:200], now, entry["id"]),
                )

    # Cleanup old queue entries (older than 1 hour)
    with get_db() as conn:
        conn.execute(
            "DELETE FROM trial_queue WHERE status IN ('done','failed') AND created_at < ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),),
        )

    return created


def check_trial_idle() -> int:
    """Release trial instances that have been idle too long. Returns count released."""
    settings = get_all_settings()
    if settings.get("trial_enabled", "false") != "true":
        return 0

    idle_timeout = int(settings.get("trial_idle_timeout", "600"))
    now = datetime.now(timezone.utc)
    released = 0

    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM instances WHERE is_trial=1 AND status='running'"
        ).fetchall()

    for row in rows:
        inst = dict(row)
        if _is_instance_idle(inst, idle_timeout, now):
            try:
                release_trial_instance(inst["instance_id"])
                released += 1
            except Exception:
                pass

    return released


def _is_instance_idle(inst: dict, idle_timeout: int, now) -> bool:
    """Check if an instance is idle by last_activity and data dir file times."""
    # Check DB last_activity first
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

    # Check filesystem: latest modification time in user data dir
    user_dir = USERS_DIR / inst["instance_id"]
    if user_dir.exists():
        latest = _latest_mtime(user_dir)
        if latest:
            latest_dt = datetime.fromtimestamp(latest, tz=timezone.utc)
            if (now - latest_dt).total_seconds() < idle_timeout:
                # Update last_activity for next check
                with get_db() as conn:
                    conn.execute(
                        "UPDATE instances SET last_activity=? WHERE instance_id=?",
                        (latest_dt.isoformat(), inst["instance_id"]),
                    )
                return False

    return True


def _latest_mtime(directory: Path) -> float | None:
    """Get the most recent mtime in directory, skipping caches and temp files."""
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
    """Release a trial instance: stop container, archive, mark released."""
    inst = get_instance(instance_id)
    if not inst or not inst.get("is_trial"):
        raise ValueError(f"Not a trial instance: {instance_id}")

    container = _container_name(instance_id)
    try:
        remove_container(container)
    except Exception:
        pass

    if inst.get("api_key"):
        try:
            delete_proxy_key(instance_id)
        except Exception:
            pass

    archive_instance(instance_id, ARCHIVE_DIR)

    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET status='released', ready=0 WHERE instance_id=?",
            (instance_id,),
        )
    regenerate()


def update_trial_activity(instance_id: str):
    """Update last_activity timestamp for a trial instance."""
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET last_activity=? WHERE instance_id=? AND is_trial=1",
            (now, instance_id),
        )


def get_trial_queue_status() -> dict:
    """Return current queue status."""
    with get_db() as conn:
        waiting = conn.execute(
            "SELECT COUNT(*) FROM trial_queue WHERE status='waiting'"
        ).fetchone()[0]
        active = _get_active_trial_count()
    settings = get_all_settings()
    return {
        "queue_length": waiting,
        "active_trials": active,
        "max_trials": int(settings.get("trial_max_instances", "3")),
        "idle_timeout": int(settings.get("trial_idle_timeout", "600")),
        "trial_enabled": settings.get("trial_enabled", "false") == "true",
    }


# ─── instance operations ───

def stop_instance(instance_id: str):
    container = _container_name(instance_id)
    stop_container(container)
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET status = 'stopped' WHERE instance_id = ?",
            (instance_id,),
        )
    regenerate()


def start_instance(instance_id: str):
    container = _container_name(instance_id)
    row = get_instance(instance_id)
    if not row:
        raise ValueError(f"Instance not found: {instance_id}")
    if row["status"] == "expired":
        raise ValueError("Cannot start expired instance, renew it first")
    start_container(container)
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET status = 'running' WHERE instance_id = ?",
            (instance_id,),
        )
    regenerate()


def restart_instance(instance_id: str):
    container = _container_name(instance_id)
    restart_container(container)
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET status = 'running', ready = 1 WHERE instance_id = ?",
            (instance_id,),
        )
    regenerate()
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
            create_proxy_key(instance_id)
        except Exception:
            pass

    start_container(_container_name(instance_id))
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET status = 'running', expires_at = ? WHERE instance_id = ?",
            (expires_str, instance_id),
        )
    regenerate()
    return {"instance_id": instance_id, "expires_at": expires_str}


def delete_instance(instance_id: str):
    row = get_instance(instance_id)
    if not row:
        raise ValueError(f"Instance not found: {instance_id}")
    container = _container_name(instance_id)
    remove_container(container)
    if row["api_key"]:
        try:
            delete_proxy_key(instance_id)
        except Exception:
            pass
    # Sync delete Cloudflare DNS record
    cf_sync = get_all_settings().get("cf_sync_delete", "true")
    cf_record = row.get("cf_record_id")
    if cf_sync == "true" and cf_record:
        try:
            delete_dns_record(cf_record)
        except Exception:
            pass
    archive_instance(instance_id, ARCHIVE_DIR)
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET status = 'deleted' WHERE instance_id = ?",
            (instance_id,),
        )
    regenerate()


def get_instance(instance_id: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE instance_id = ?", (instance_id,),
        ).fetchone()
    return dict(row) if row else None


def list_instances(status: str | None = None) -> list[dict]:
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM instances WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM instances ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def apply_api_config(instance_id: str) -> dict:
    """Re-apply API template to an existing instance."""
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")
    instance_dir = USERS_DIR / instance_id
    api_template = TEMPLATES_DIR / "sillytavern" / "data" / "default-user"
    if not api_template.exists():
        return {"ok": False, "error": "API 配置模板不存在"}

    stop_container(inst["container_name"])
    vars_ = _api_template_vars(instance_id, inst["username"], inst["password"], inst["api_key"],
                               inst.get("path_prefix", ""))
    render_config(instance_dir, vars_)
    start_container(inst["container_name"])
    regenerate()
    return {"ok": True, "message": "API 配置已重新下发并重启容器"}


def apply_api_config_all() -> dict:
    """Re-apply API config to all running/stopped instances."""
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


def check_expired():
    now = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        expired = conn.execute(
            "SELECT * FROM instances WHERE status = 'running' AND expires_at <= ?", (now,),
        ).fetchall()
    for row in expired:
        inst_id = row["instance_id"]
        container = _container_name(inst_id)
        try:
            stop_container(container)
        except Exception:
            pass
        if row["api_key"]:
            try:
                delete_proxy_key(inst_id)
            except Exception:
                pass
        with get_db() as conn:
            conn.execute(
                "UPDATE instances SET status = 'expired' WHERE instance_id = ?",
                (inst_id,),
            )
    regenerate()
    return len(expired)


def get_summary() -> dict:
    with get_db() as conn:
        total_instances = conn.execute("SELECT COUNT(*) FROM instances").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM instances WHERE status='running'").fetchone()[0]
        stopped = conn.execute("SELECT COUNT(*) FROM instances WHERE status='stopped'").fetchone()[0]
        expired = conn.execute("SELECT COUNT(*) FROM instances WHERE status='expired'").fetchone()[0]
        deleted = conn.execute("SELECT COUNT(*) FROM instances WHERE status='deleted'").fetchone()[0]
        unused_keys = conn.execute("SELECT COUNT(*) FROM activation_keys WHERE status='unused'").fetchone()[0]
        used_keys = conn.execute("SELECT COUNT(*) FROM activation_keys WHERE status='used'").fetchone()[0]
        api_ok = conn.execute("SELECT COUNT(*) FROM instances WHERE api_status='ok'").fetchone()[0]
        api_fail = conn.execute("SELECT COUNT(*) FROM instances WHERE api_status='failed'").fetchone()[0]
        stream_ok = conn.execute("SELECT COUNT(*) FROM instances WHERE stream_status='ok'").fetchone()[0]
        stream_fail = conn.execute("SELECT COUNT(*) FROM instances WHERE stream_status='failed'").fetchone()[0]
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_count = conn.execute(
            "SELECT COUNT(*) FROM instances WHERE created_at LIKE ?", (today + "%",)
        ).fetchone()[0]

    return {
        "total_instances": total_instances,
        "running": running, "stopped": stopped, "expired": expired, "deleted": deleted,
        "unused_keys": unused_keys, "used_keys": used_keys,
        "today_created": today_count,
        "api_ok": api_ok, "api_failed": api_fail,
        "stream_ok": stream_ok, "stream_failed": stream_fail,
    }


def check_instance(instance_id: str) -> dict:
    """Run a health check on the instance (web + api + stream)."""
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")

    results = {}
    # Web check
    try:
        ready = health_check_container(inst["domain"], timeout=10)
        with get_db() as conn:
            conn.execute(
                "UPDATE instances SET web_status=?, web_checked_at=? WHERE instance_id=?",
                ("ready" if ready else "failed", datetime.now(timezone.utc).isoformat(), instance_id),
            )
        results["web"] = "ready" if ready else "failed"
    except Exception as e:
        results["web"] = f"error: {e}"

    return results


def get_instance_logs(instance_id: str, tail: int = 100) -> str:
    import subprocess
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")
    result = subprocess.run(
        ["docker", "logs", inst["container_name"], "--tail", str(tail)],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout[-5000:] if result.stdout else "(no logs)"


def get_instance_inspect(instance_id: str) -> dict:
    import subprocess
    inst = get_instance(instance_id)
    if not inst:
        raise ValueError(f"Instance not found: {instance_id}")

    container_exists_flag = False
    container_running = False
    try:
        result = subprocess.run(
            ["docker", "inspect", inst["container_name"]],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)[0]
            container_exists_flag = True
            container_running = data.get("State", {}).get("Running", False)
    except Exception:
        pass

    user_dir = USERS_DIR / instance_id
    return {
        "container_exists": container_exists_flag,
        "container_running": container_running,
        "domain": inst["domain"],
        "url": f"{PUBLIC_SCHEME}://{inst['domain']}",
        "user_dir_exists": user_dir.exists(),
        "config_yaml_exists": (user_dir / "config" / "config.yaml").exists(),
        "default_user_exists": (user_dir / "data" / "default-user").exists(),
        "docker_network": DOCKER_NETWORK,
        "container_name": inst["container_name"],
    }
