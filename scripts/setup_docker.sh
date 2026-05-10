#!/usr/bin/env bash
set -e
echo "Setting up Docker mode..."

# Install Python deps
pip3 install -r manager/requirements.txt 2>/dev/null || pip install -r manager/requirements.txt

# Init DB and dirs
python3 scripts/init_db.py
docker network create st_proxy 2>/dev/null || true
mkdir -p users archive backups templates/proxy

# Pull Docker images (SillyTavern + Traefik)
echo "Pulling Docker images..."
docker pull ghcr.io/sillytavern/sillytavern:latest 2>/dev/null || echo "  WARNING: ST image pull failed (will pull on first docker compose up)"
docker pull traefik:v3 2>/dev/null || echo "  WARNING: Traefik pull failed"

# Set runtime_mode
python3 -c "
from manager.db import init_db, get_db
from manager.settings_service import set_settings
init_db()
set_settings({'runtime_mode': 'docker'})
print('runtime_mode set to docker')
"

echo ""
echo "Docker mode ready. Run: docker compose up -d"
