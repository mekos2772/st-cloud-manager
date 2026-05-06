.PHONY: install init dev start stop clean backup test logs

PY    := python3
PIP   := pip3
PORT  := 5000

install:        ## Install Python dependencies
	$(PIP) install -r manager/requirements.txt

init:           ## Initialize database and directories
	$(PY) scripts/init_db.py
	docker network create st_proxy 2>/dev/null || true
	mkdir -p users archive backups

dev:            ## Start development server
	$(PY) -m uvicorn manager.app:app --host 0.0.0.0 --port $(PORT) --reload

start:          ## Start production server
	$(PY) -m uvicorn manager.app:app --host 0.0.0.0 --port $(PORT)

stop:           ## Stop all ST containers
	docker ps -aq --filter "name=st-" | xargs -r docker rm -f

clean:          ## Clean test data (WARNING: deletes all instance data)
	rm -f data.db
	rm -rf users/* archive/*

backup:         ## Create a backup
	bash scripts/backup.sh

key:            ## Generate a test activation key
	$(PY) scripts/create_key.py --count 1 --days 30

test:           ## Run API tests
	curl -s -H "x-api-key: ${ADMIN_KEY}" http://localhost:$(PORT)/api/admin/health/manager | python -m json.tool

logs:           ## Show Manager logs (if running via docker-compose)
	docker compose logs -f --tail=50

help:           ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
