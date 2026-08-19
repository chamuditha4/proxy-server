#!/bin/sh
# All-in-one node: NetBird + proxy in ONE container. Starts the NetBird daemon,
# joins the mesh, then runs the proxy in the foreground as the main process.
set -eu

: "${NB_SETUP_KEY:?NB_SETUP_KEY is required (your proxy-nodes setup key)}"
MGMT="${NB_MANAGEMENT_URL:-https://api.netbird.io}"
SOCK="/var/run/netbird.sock"

echo "[aio] starting NetBird daemon"
# The daemon backgrounds itself; we track liveness via the socket + status,
# not via the launching PID (which returns immediately).
netbird service run >/var/log/netbird-daemon.log 2>&1 &

echo "[aio] waiting for the daemon socket"
i=0; while [ ! -S "$SOCK" ]; do
  i=$((i+1)); [ "$i" -gt 30 ] && { echo "[aio] daemon socket never appeared"; cat /var/log/netbird-daemon.log; exit 1; }
  sleep 1
done

echo "[aio] joining the mesh${NODE_NAME:+ as proxy-$NODE_NAME}"
if [ -n "${NODE_NAME:-}" ]; then
  netbird up --setup-key "$NB_SETUP_KEY" --hostname "proxy-${NODE_NAME}" --management-url "$MGMT"
else
  # No NODE_NAME -> the peer name defaults to the container id. Set NODE_NAME to
  # get a clean, routable node name.
  netbird up --setup-key "$NB_SETUP_KEY" --management-url "$MGMT"
fi

netbird status 2>/dev/null | grep -i "NetBird IP" || true

echo "[aio] starting proxy on :${PROXY_PORT:-8899}"
exec python3 /app/proxy_server.py
