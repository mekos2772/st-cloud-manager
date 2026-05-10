#!/bin/sh
set -e

echo "[entrypoint] starting ST instance with path prefix: ${ST_PATH_PREFIX}"

# Modify ST config to use internal port 8001
CONFIG="/home/node/app/config/config.yaml"
if [ -f "$CONFIG" ]; then
    sed -i 's/port: 8000/port: 8001/' "$CONFIG"
    echo "[entrypoint] set ST port to 8001"
fi

# Start SillyTavern in background
cd /home/node/app
node server.js &
ST_PID=$!
echo "[entrypoint] ST started with PID $ST_PID"

# Start proxy on port 8000 (foreground)
exec node /proxy/proxy.js
