#!/usr/bin/env bash
set -euo pipefail

# ================================================================
#  ST Cloud Manager — Interactive Installer
# ================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# ── Colors ──
C_R='\033[0;31m'; C_G='\033[0;32m'; C_Y='\033[1;33m'; C_B='\033[0;34m'; C_C='\033[0;36m'; C_W='\033[1;37m'; C_N='\033[0m'
ok()   { echo -e "  ${C_G}[OK]${C_N} $*"; }
warn() { echo -e "  ${C_Y}[WARN]${C_N} $*"; }
fail() { echo -e "  ${C_R}[FAIL]${C_N} $*"; }
info() { echo -e "  ${C_B}[>>>]${C_N} $*"; }
ask()  { echo -en "  ${C_C}[?]${C_N} $1 "; }

PY_CMD="python3"

# ── system check ──
check_system() {
    echo ""
    echo -e "${C_W}═══════════════════════════════════════${C_N}"
    echo -e "${C_W}  System Check${C_N}"
    echo -e "${C_W}═══════════════════════════════════════${C_N}"

    # OS
    if grep -qi microsoft /proc/version 2>/dev/null; then
        ok "OS: WSL (Linux on Windows)"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        ok "OS: Linux ($(uname -r))"
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        warn "OS: macOS — Docker Desktop required"
    else
        fail "Unsupported OS: $OSTYPE"
    fi

    # docker
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        ok "docker: $(docker --version)"
    else
        fail "docker: not available"
        echo "       Install: curl -fsSL https://get.docker.com | sh"
    fi

    # docker compose
    if docker compose version &>/dev/null 2>&1; then
        ok "docker compose: available"
    elif docker-compose --version &>/dev/null 2>&1; then
        ok "docker-compose: available (legacy)"
    else
        fail "docker compose: not available"
    fi

    # python3
    if command -v python3 &>/dev/null; then
        ok "python3: $(python3 --version)"
    elif command -v python &>/dev/null; then
        PY_CMD="python"
        ok "python: $(python --version)"
    else
        fail "python3: not found"
        echo "       Install: sudo apt install python3 python3-pip (Debian/Ubuntu)"
    fi

    # curl
    command -v curl &>/dev/null && ok "curl: available" || fail "curl: not found"

    # git
    command -v git &>/dev/null && ok "git: $(git --version)" || warn "git: not found"

    echo ""
}

# ── directories ──
init_dirs() {
    info "Creating directories..."
    mkdir -p users archive backups logs templates/sillytavern/config templates/sillytavern/data templates/sillytavern/plugins
    ok "Directory structure ready"

    if [ ! -f "templates/sillytavern/config/config.yaml.tpl" ]; then
        warn "config.yaml.tpl missing — creating default"
        cat > templates/sillytavern/config/config.yaml.tpl << 'YAML'
port: 8000
listen: true
basicAuthMode: true
basicAuthUser:
  username: "{{USERNAME}}"
  password: "{{PASSWORD}}"
whitelistMode: false
enableUserAccounts: false
allowKeysExposure: false
enableServerPlugins: false
YAML
    fi
}

# ── random key ──
rand_hex() { python3 -c "import secrets;print(secrets.token_hex(${1:-16}))" 2>/dev/null || openssl rand -hex "${1:-16}" 2>/dev/null; }

# ── .env ──
init_env() {
    local MODE="${1:-local}"
    echo ""
    echo -e "${C_W}═══════════════════════════════════════${C_N}"
    echo -e "${C_W}  .env Configuration ($MODE mode)${C_N}"
    echo -e "${C_W}═══════════════════════════════════════${C_N}"

    if [ -f ".env" ]; then
        ask ".env already exists. Overwrite? [y/N]"
        read -r ans
        if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
            ok "Keeping existing .env"
            return
        fi
        cp .env ".env.bak.$(date +%s)"
        ok "Old .env backed up"
    fi

    [ -f ".env.example" ] && cp .env.example .env || touch .env

    # Admin Key
    ADMIN_KEY="sk-admin-$(rand_hex 16)"
    sed -i "s/^ST_ADMIN_API_KEY=.*/ST_ADMIN_API_KEY=$ADMIN_KEY/" .env
    ok "Admin Key generated (shown once): $ADMIN_KEY"

    if [ "$MODE" = "local" ]; then
        sed -i "s/^ST_DOMAIN_SUFFIX=.*/ST_DOMAIN_SUFFIX=127-0-0-1.sslip.io/" .env
        sed -i "s/^ST_PUBLIC_SCHEME=.*/ST_PUBLIC_SCHEME=http/" .env
        sed -i "s/^ST_TRAEFIK_ENTRYPOINT=.*/ST_TRAEFIK_ENTRYPOINT=web/" .env
        sed -i "s/^ST_TRAEFIK_TLS=.*/ST_TRAEFIK_TLS=false/" .env
        ok "Local mode: 127-0-0-1.sslip.io / HTTP / no TLS"
    else
        sed -i "s/^ST_PUBLIC_SCHEME=.*/ST_PUBLIC_SCHEME=https/" .env
        sed -i "s/^ST_TRAEFIK_ENTRYPOINT=.*/ST_TRAEFIK_ENTRYPOINT=websecure/" .env
        sed -i "s/^ST_TRAEFIK_TLS=.*/ST_TRAEFIK_TLS=true/" .env

        ask "Base domain (e.g. st.example.com):"
        read -r domain
        domain="${domain:-st.example.com}"
        sed -i "s/^ST_DOMAIN_SUFFIX=.*/ST_DOMAIN_SUFFIX=$domain/" .env
        ok "Domain: $domain"
    fi

    chmod 600 .env 2>/dev/null || true
    ok ".env permissions set to 600"
}

# ── Cloudflare ──
init_cloudflare() {
    echo ""
    info "Cloudflare DNS Setup"
    ask "Enable Cloudflare DNS? [y/N]"
    read -r ans
    if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
        ok "Cloudflare disabled"
        return
    fi

    ask "CF API Token (input hidden):"
    read -rs token
    echo ""
    token="$(echo "$token" | sed -E 's/^[Bb]earer[[:space:]]+//; s/^[[:space:]]+//; s/[[:space:]]+$//')"
    ask "Zone ID:"
    read -r zone_id
    ask "Zone Name (e.g. example.com):"
    read -r zone_name
    ask "Base domain (e.g. st.example.com):"
    read -r base_domain
    ask "Record type [CNAME]:"
    read -r rec_type; rec_type="${rec_type:-CNAME}"
    ask "Record target (e.g. your-server.com or IP):"
    read -r rec_target
    ask "Proxy through Cloudflare? [y/N]:"
    read -r proxied; proxied="${proxied:-n}"; [ "$proxied" = "y" ] && proxied="true" || proxied="false"
    ask "TTL [1]:"
    read -r ttl; ttl="${ttl:-1}"
    ask "Sync-delete on instance removal? [Y/n]:"
    read -r sync; sync="${sync:-y}"; [ "$sync" = "y" ] && sync="true" || sync="false"

    cat >> .env << EOF
ST_DOMAIN_MODE=cloudflare
ST_CF_API_TOKEN=${token}
ST_CF_ZONE_ID=${zone_id}
ST_CF_ZONE_NAME=${zone_name}
ST_CF_BASE_DOMAIN=${base_domain}
ST_CF_RECORD_TYPE=${rec_type}
ST_CF_RECORD_TARGET=${rec_target}
ST_CF_PROXIED=${proxied}
ST_CF_TTL=${ttl}
ST_CF_SYNC_DELETE=${sync}
EOF
    ok "Cloudflare DNS configured"
}

# ── API config ──
init_api_config() {
    echo ""
    info "API Configuration"
    ask "Configure API now? [y/N]"
    read -r ans
    if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
        ok "Skipping API config (set later in admin panel)"
        return
    fi

    ask "API Base URL (e.g. http://api.lordfa.top):"
    read -r api_url; api_url="${api_url:-http://api.lordfa.top}"
    ask "Model name [deepseek-v4-pro]:"
    read -r model; model="${model:-deepseek-v4-pro}"
    ask "Upstream API Key (input hidden):"
    read -rs api_key; echo ""
    ask "Enable streaming? [Y/n]:"
    read -r stream; stream="${stream:-y}"; [ "$stream" = "y" ] && stream="true" || stream="false"

    cat >> .env << EOF
ST_API_BASE_URL=${api_url}
ST_API_MODEL=${model}
ST_MASTER_API_KEY=${api_key}
ST_STREAMING_ENABLED=${stream}
EOF
    ok "API config saved"
}

# ── database ──
init_db() {
    echo ""
    echo -e "${C_W}═══════════════════════════════════════${C_N}"
    echo -e "${C_W}  Database${C_N}"
    echo -e "${C_W}═══════════════════════════════════════${C_N}"

    if [ -f "data.db" ]; then
        warn "data.db already exists"
        echo "  1) Keep and migrate"
        echo "  2) Backup then rebuild"
        echo "  3) Skip"
        ask "Choice [1]:"
        read -r choice; choice="${choice:-1}"
        case "$choice" in
            2) cp data.db "data.db.bak.$(date +%s)" && rm -f data.db && ok "Old DB backed up, rebuilding..." ;;
            3) ok "Skipping DB init"; return ;;
            *) ok "Keeping existing DB, migrating..." ;;
        esac
    fi

    $PY_CMD scripts/init_db.py 2>&1
    ok "Database ready"

    # Import API config from .env into DB
    if [ -f ".env" ]; then
        info "Importing .env settings into database..."
        $PY_CMD -c "
from manager.config import API_BASE_URL, API_MODEL, MASTER_API_KEY, MANAGER_PROXY_URL
from manager.settings_service import set_settings
try:
    s = {
        'api_base_url': API_BASE_URL,
        'api_model': API_MODEL,
        'upstream_api_key': MASTER_API_KEY,
        'streaming_enabled': 'true',
    }
    set_settings(s)
    print('  [OK] Settings imported')
except Exception as e:
    print(f'  [WARN] Settings import failed: {e}')
" 2>&1 || warn "Settings import skipped (start Manager first)"
    fi
}

# ── start services ──
start_services() {
    echo ""
    echo -e "${C_W}═══════════════════════════════════════${C_N}"
    echo -e "${C_W}  Starting Services${C_N}"
    echo -e "${C_W}═══════════════════════════════════════${C_N}"

    # Docker network
    docker network create st_proxy 2>/dev/null && ok "Network st_proxy created" || ok "Network st_proxy exists"

    # docker compose
    if [ -f "docker-compose.yml" ]; then
        info "docker compose pull..."
        docker compose pull 2>&1 | tail -3
        info "docker compose up -d --build..."
        docker compose up -d --build 2>&1 | tail -5
        ok "Containers started"
        docker compose ps 2>&1
    else
        warn "No docker-compose.yml — starting standalone"
        $PY_CMD -m uvicorn manager.app:app --host 0.0.0.0 --port 5000 &
        sleep 2
        ok "Manager started (port 5000)"
    fi
}

# ── stop services ──
stop_services() {
    info "Stopping services..."
    docker compose down 2>/dev/null && ok "Containers stopped" || true
    pkill -f "uvicorn manager.app" 2>/dev/null && ok "Manager stopped" || true
    ok "Services stopped"
}

# ── health check ──
health_check() {
    echo ""
    echo -e "${C_W}═══════════════════════════════════════${C_N}"
    echo -e "${C_W}  Health Check${C_N}"
    echo -e "${C_W}═══════════════════════════════════════${C_N}"

    test_docker()  { docker info &>/dev/null 2>&1 && ok "Docker" || fail "Docker"; }
    test_traefik(){ docker ps --filter name=st-traefik --format '{{.Status}}' 2>/dev/null | grep -q Up && ok "Traefik container" || fail "Traefik container"; }
    test_manager(){ curl -sf -o /dev/null http://127.0.0.1:5000/activate 2>/dev/null && ok "Manager (port 5000)" || fail "Manager (port 5000)"; }
    test_db()     { [ -f "data.db" ] && ok "Database" || fail "Database"; }
    test_tpl()    { [ -f "templates/sillytavern/config/config.yaml.tpl" ] && ok "Config template" || fail "Config template"; }
    test_tpl2()   {
        local d="templates/sillytavern/data/default-user"
        [ -d "$d" ] && ok "API template" || warn "API template (run export_api_template.py after configuring one ST instance)"
    }
    test_sec()    {
        if grep -q "test-admin-key" .env 2>/dev/null; then
            fail "Security: USING DEFAULT ADMIN KEY!"
        else
            ok "Security: Admin Key is custom"
        fi
    }

    test_docker; test_traefik; test_manager; test_db; test_tpl; test_tpl2; test_sec
    echo ""
}

# ── create test key ──
create_test_key() {
    echo ""
    ask "Generate a test activation key? [y/N]"
    read -r ans
    if [[ "$ans" != "y" && "$ans" != "Y" ]]; then return; fi
    $PY_CMD scripts/create_key.py --count 1 --days 30 2>&1
    ok "Key generated — copy the key above"
}

# ── main menu ──
show_menu() {
    echo ""
    echo -e "${C_W}╔══════════════════════════════════════════╗${C_N}"
    echo -e "${C_W}║   ST Cloud Manager v0.3 — Installer      ║${C_N}"
    echo -e "${C_W}╠══════════════════════════════════════════╣${C_N}"
    echo -e "${C_W}║  1) Local test install                   ║${C_N}"
    echo -e "${C_W}║  2) Production install                   ║${C_N}"
    echo -e "${C_W}║  3) Re-generate .env                     ║${C_N}"
    echo -e "${C_W}║  4) Init / migrate database              ║${C_N}"
    echo -e "${C_W}║  5) Start services                       ║${C_N}"
    echo -e "${C_W}║  6) Stop services                        ║${C_N}"
    echo -e "${C_W}║  7) Health check                         ║${C_N}"
    echo -e "${C_W}║  8) Create test Key                      ║${C_N}"
    echo -e "${C_W}║  9) Exit                                 ║${C_N}"
    echo -e "${C_W}╚══════════════════════════════════════════╝${C_N}"
    echo ""
    ask "Choice [1]:"
    read -r choice
    echo ""
    case "${choice:-1}" in
        1)
            check_system
            init_dirs
            init_env "local"
            init_api_config
            init_db
            start_services
            health_check
            create_test_key
            post_summary
            ;;
        2)
            check_system
            init_dirs
            init_env "production"
            init_cloudflare
            init_api_config
            init_db
            start_services
            health_check
            create_test_key
            post_summary
            ;;
        3) init_env "local" ;;
        4) init_db ;;
        5) start_services ;;
        6) stop_services ;;
        7) health_check ;;
        8) create_test_key ;;
        9) echo "Bye."; exit 0 ;;
        *) warn "Invalid choice"; show_menu ;;
    esac
}

post_summary() {
    local admin_key=""; local domain="127-0-0-1.sslip.io"; local scheme="http"
    [ -f ".env" ] && admin_key=$(grep ST_ADMIN_API_KEY .env | cut -d= -f2) || true
    [ -f ".env" ] && domain=$(grep ST_DOMAIN_SUFFIX .env | cut -d= -f2 | head -1) || true
    [ -f ".env" ] && scheme=$(grep ST_PUBLIC_SCHEME .env | cut -d= -f2 | head -1) || true

    echo ""
    echo -e "${C_Y}╔══════════════════════════════════════════╗${C_N}"
    echo -e "${C_Y}║  Installation Complete                    ║${C_N}"
    echo -e "${C_Y}╠══════════════════════════════════════════╣${C_N}"
    echo -e "${C_Y}║  Admin:   ${scheme}://manager.${domain}/admin${C_N}"
    echo -e "${C_Y}║  Activate:${scheme}://manager.${domain}/activate${C_N}"
    echo -e "${C_Y}║  API doc: ${scheme}://manager.${domain}/docs${C_N}"
    echo -e "${C_Y}╠══════════════════════════════════════════╣${C_N}"
    echo -e "${C_Y}║  Admin Key: ${admin_key}${C_N}"
    echo -e "${C_Y}╠══════════════════════════════════════════╣${C_N}"
    echo -e "${C_Y}║  Useful commands:${C_N}"
    echo -e "${C_Y}║    docker compose ps${C_N}"
    echo -e "${C_Y}║    docker compose logs -f manager${C_N}"
    echo -e "${C_Y}║    python scripts/create_key.py --count 5 --days 30${C_N}"
    echo -e "${C_Y}╚══════════════════════════════════════════╝${C_N}"
    echo ""

    if [ "$scheme" = "http" ]; then
        warn "Running on HTTP — only suitable for local testing"
    fi
}

# ── entry ──
[ ! -f "manager/requirements.txt" ] && { fail "Run from project root: cd st-cloud-manager && bash scripts/install.sh"; exit 1; }

# Install pip deps if needed
if ! $PY_CMD -c "import fastapi" 2>/dev/null; then
    info "Installing Python dependencies..."
    $PY_CMD -m pip install -q -r manager/requirements.txt 2>&1 | tail -3
    ok "Dependencies installed"
fi

show_menu
