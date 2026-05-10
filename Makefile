.PHONY: install init dev start stop clean backup test logs \
        setup setup-docker setup-nodocker start-nodocker stop-nodocker nginx-reload

PY    := python3
PIP   := pip3
PORT  := 5000

# ─── Setup ───

setup:          ## Interactive setup (choose Docker or No-Docker)
	bash scripts/setup.sh

setup-docker:   ## Setup for Docker mode
	bash scripts/setup_docker.sh

setup-nodocker: ## Setup for No-Docker mode
	bash scripts/setup_nodocker.sh

# ─── Install & Init ───

install:        ## Install Python dependencies
	$(PIP) install -r manager/requirements.txt

init:           ## Initialize database and directories
	$(PY) scripts/init_db.py
	docker network create st_proxy 2>/dev/null || true
	mkdir -p users archive backups nginx-sites

# ─── Run ───

dev:            ## Start dev server (auto-reload)
	$(PY) -m uvicorn manager.app:app --host 0.0.0.0 --port $(PORT) --reload

start:          ## Start production server
	$(PY) -m uvicorn manager.app:app --host 0.0.0.0 --port $(PORT)

start-nodocker: ## Start in No-Docker mode (manager + nginx reload)
	@echo "Starting manager..."
	$(PY) -m uvicorn manager.app:app --host 0.0.0.0 --port $(PORT) &
	@sleep 2
	@echo "Reloading nginx..."
	@nginx -s reload 2>/dev/null || echo "WARNING: nginx reload failed (is nginx installed?)"
	@echo "Manager: http://localhost:$(PORT)"

# ─── Stop & Clean ───

stop:           ## Stop all ST containers (Docker mode)
	docker ps -aq --filter "name=st-" | xargs -r docker rm -f

stop-nodocker:  ## Stop all ST processes (No-Docker mode)
	@for pidfile in users/*/.st_pid; do \
		[ -f "$$pidfile" ] && kill $$(cat "$$pidfile") 2>/dev/null && rm -f "$$pidfile" && echo "Killed $$pidfile" || true; \
	done
	@pkill -f "uvicorn manager.app:app" 2>/dev/null || true
	@echo "All ST processes stopped"

clean:          ## Clean test data (WARNING: deletes all instance data)
	rm -f data.db
	rm -rf users/* archive/*

# ─── Maintenance ───

backup:         ## Create a backup
	bash scripts/backup.sh

key:            ## Generate a test activation key
	$(PY) scripts/create_key.py --count 1 --days 30

nginx-reload:   ## Reload nginx config (No-Docker mode)
	nginx -t && nginx -s reload

update-st:      ## Update shared ST release (git pull + npm install)
	@ST_DIR="$${ST_RELEASE_DIR:-$(pwd)/st-release}"; \
	if [ -d "$$ST_DIR/.git" ]; then \
		echo "Updating ST at $$ST_DIR..."; \
		cd "$$ST_DIR" && git pull && npm install --omit=dev; \
		echo "ST updated. Restart instances to apply."; \
	else \
		echo "ST release not found at $$ST_DIR. Run 'make setup-nodocker' first."; \
	fi

pull-images:    ## Pull latest Docker images
	docker pull ghcr.io/sillytavern/sillytavern:latest
	docker pull traefik:v3

logs:           ## Show Manager logs (Docker mode)
	docker compose logs -f --tail=50

test:           ## Run API health check
	curl -s -H "x-api-key: ${ADMIN_KEY}" http://localhost:$(PORT)/api/admin/health/manager | python -m json.tool

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
