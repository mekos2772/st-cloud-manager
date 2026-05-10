import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file if it exists
_env_path = BASE_DIR / ".env"
if _env_path.exists():
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val

USERS_DIR = BASE_DIR / os.getenv("ST_USERS_DIR", "users")
ARCHIVE_DIR = BASE_DIR / os.getenv("ST_ARCHIVE_DIR", "archive")
TEMPLATES_DIR = BASE_DIR / os.getenv("ST_TEMPLATES_DIR", "templates")
DB_PATH = BASE_DIR / os.getenv("ST_DB_PATH", "data.db")

# Docker
DOCKER_NETWORK = os.getenv("ST_DOCKER_NETWORK", "st_proxy")
DOCKER_IMAGE = os.getenv("ST_DOCKER_IMAGE", "ghcr.io/sillytavern/sillytavern:latest")
DOCKER_MEMORY = os.getenv("ST_DOCKER_MEMORY", "512m")

# Traefik
TRAEFIK_ENTRYPOINT = os.getenv("ST_TRAEFIK_ENTRYPOINT", "websecure")
TRAEFIK_CERT_RESOLVER = os.getenv("ST_TRAEFIK_CERT_RESOLVER", "le")
TRAEFIK_TLS = os.getenv("ST_TRAEFIK_TLS", "true").lower() in ("true", "1", "yes")
TRAEFIK_DYNAMIC_CONFIG = BASE_DIR / os.getenv("ST_TRAEFIK_DYNAMIC_CONFIG", "traefik-dynamic.yml")
DOMAIN_SUFFIX = os.getenv("ST_DOMAIN_SUFFIX", "st.example.com")
PUBLIC_SCHEME = os.getenv("ST_PUBLIC_SCHEME", "https" if TRAEFIK_TLS else "http")

# Routing mode: "subdomain" (default) or "path"
ROUTING_MODE = os.getenv("ST_ROUTING_MODE", "subdomain")
BASE_DOMAIN = os.getenv("ST_BASE_DOMAIN", "st.example.com")
PATH_PREFIX_LENGTH = int(os.getenv("ST_PATH_PREFIX_LENGTH", "8"))

# API Proxy
PROXY_BASE_URL = os.getenv("ST_PROXY_BASE_URL", "https://api-proxy.example.com/v1")
PROXY_MASTER_KEY = os.getenv("ST_PROXY_MASTER_KEY", "")
API_BASE_URL = os.getenv("ST_API_BASE_URL", "http://api.lordfa.top")
API_MODEL = os.getenv("ST_API_MODEL", "deepseek-v4-pro")
# Extract hostname from API_BASE_URL for requestOverrides
API_HOST = API_BASE_URL.split("://")[-1].split("/")[0]
# Real API key injected via config.yaml requestOverrides (hidden from user)
MASTER_API_KEY = os.getenv("ST_MASTER_API_KEY", "")
# URL for ST instances to reach the Manager proxy (from inside Docker)
MANAGER_PROXY_URL = os.getenv("ST_MANAGER_PROXY_URL", "http://host.docker.internal:5000")

# Instance defaults
DEFAULT_PLAN = os.getenv("ST_DEFAULT_PLAN", "default")
DEFAULT_DAYS = int(os.getenv("ST_DEFAULT_DAYS", "30"))

# Admin
ADMIN_API_KEY = os.getenv("ST_ADMIN_API_KEY", "")

# Trial mode
TRIAL_ENABLED = os.getenv("ST_TRIAL_ENABLED", "false").lower() in ("true", "1", "yes")
TRIAL_MAX_INSTANCES = int(os.getenv("ST_TRIAL_MAX_INSTANCES", "3"))
TRIAL_IDLE_TIMEOUT = int(os.getenv("ST_TRIAL_IDLE_TIMEOUT", "600"))
TRIAL_MAX_MEMORY_PCT = int(os.getenv("ST_TRIAL_MAX_MEMORY_PCT", "85"))
TRIAL_QUEUE_ENABLED = os.getenv("ST_TRIAL_QUEUE_ENABLED", "true").lower() in ("true", "1", "yes")
