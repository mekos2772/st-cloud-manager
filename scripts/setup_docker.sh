#!/usr/bin/env bash
set -e
echo "Setting up Docker mode..."

# Install Python deps
pip3 install -r manager/requirements.txt 2>/dev/null || pip install -r manager/requirements.txt

# Init DB and dirs
python3 scripts/init_db.py
docker network create st_proxy 2>/dev/null || true
mkdir -p users archive backups templates/proxy

# Set runtime_mode
python3 -c "
from manager.db import init_db, get_db
from manager.settings_service import set_settings
init_db()
set_settings({'runtime_mode': 'docker'})
print('runtime_mode set to docker')
"

echo "Docker mode ready. Run: docker compose up -d"
