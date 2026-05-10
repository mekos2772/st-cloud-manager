#!/usr/bin/env bash
set -e

echo "========================================"
echo " ST Cloud Manager - Setup"
echo "========================================"
echo ""
echo "Choose deployment mode:"
echo "  1) Docker mode      (docker compose up)"
echo "  2) No-Docker mode   (bare metal, nginx + processes)"
echo ""
read -p "Enter 1 or 2 [1]: " choice
choice=${choice:-1}

case "$choice" in
  1)
    echo ""
    echo ">>> Docker mode setup"
    bash scripts/setup_docker.sh
    ;;
  2)
    echo ""
    echo ">>> No-Docker mode setup"
    bash scripts/setup_nodocker.sh
    ;;
  *)
    echo "Invalid choice. Exiting."
    exit 1
    ;;
esac

echo ""
echo "Setup complete."
echo "Run: make start   (or docker compose up -d for Docker mode)"
