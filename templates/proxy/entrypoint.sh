#!/bin/sh
set -e

INSTANCE_ID="${ST_INSTANCE_ID:-unknown}"
PROXY_PORT="${ST_PROXY_PORT:-8000}"
ST_PORT="${ST_ST_PORT:-8001}"
PREFIX="${ST_PATH_PREFIX:-/st-${INSTANCE_ID}}"

echo "[entrypoint] ${INSTANCE_ID} proxy:${PROXY_PORT} st:${ST_PORT} prefix:${PREFIX}"

# Modify ST config to use internal port
CONFIG="/home/node/app/config/config.yaml"
if [ -f "$CONFIG" ]; then
    sed -i "s/port: 8000/port: ${ST_PORT}/" "$CONFIG"
    echo "[entrypoint] set ST port to ${ST_PORT}"
fi

# Start SillyTavern in background
cd /home/node/app
node server.js &
echo "[entrypoint] ST started PID $!"

# Start proxy on proxy_port (foreground) — proxy handles ALL path logic
exec node /proxy/proxy.js "$INSTANCE_ID" "$PROXY_PORT" "$ST_PORT" "$PREFIX"
