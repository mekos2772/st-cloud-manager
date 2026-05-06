#!/bin/bash
# Backup user data and database
# Usage: ./scripts/backup.sh [backup_dir]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="${1:-$PROJECT_DIR/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="st-backup-${TIMESTAMP}"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_NAME}"

mkdir -p "$BACKUP_PATH"

# Backup database
if [ -f "$PROJECT_DIR/data.db" ]; then
    cp "$PROJECT_DIR/data.db" "$BACKUP_PATH/data.db"
    echo "[OK] Database backed up"
fi

# Backup user data
if [ -d "$PROJECT_DIR/users" ] && [ "$(ls -A "$PROJECT_DIR/users" 2>/dev/null)" ]; then
    cp -r "$PROJECT_DIR/users" "$BACKUP_PATH/users"
    echo "[OK] User data backed up"
fi

# Compress
cd "$BACKUP_DIR"
tar -czf "${BACKUP_NAME}.tar.gz" "$BACKUP_NAME"
rm -rf "$BACKUP_NAME"
echo "[OK] Backup saved: ${BACKUP_PATH}.tar.gz"

# Keep only last 30 backups
ls -1t "${BACKUP_DIR}"/*.tar.gz 2>/dev/null | tail -n +31 | xargs -r rm
