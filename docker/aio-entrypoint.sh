#!/bin/sh
# All-in-one node. Brings up the mesh (if any) and then runs the proxy in the
# foreground as the main process. Everything is driven by env vars so the image
# runs unchanged on platforms that only let you set env vars.
set -eu

log() { echo "[aio] $*"; }

# Railway/Render/Heroku hand you the port in $PORT. Honour it, then fall back.
PROXY_PORT="${PROXY_PORT:-${PORT:-8899}}"
export PROXY_PORT
NODE_NAME="${NODE_NAME:-}"

# ---------------------------------------------------------------------------
# Which mesh?
# ---------------------------------------------------------------------------
MESH="${MESH:-auto}"
if [ "$MESH" = "auto" ]; then
  if [ -n "${TS_AUTHKEY:-}" ]; then
    MESH=tailscale
  elif [ -n "${NB_SETUP_KEY:-}" ]; then
    MESH=netbird
  else
    MESH=none
  fi
  log "MESH=auto resolved to '$MESH'"
fi

# ---------------------------------------------------------------------------
# Mesh: Tailscale, userspace WireGuard.
# No NET_ADMIN, no /dev/net/tun, no volume. tailscaled's netstack forwards
# inbound tailnet connections to 127.0.0.1:<port>, which is where the proxy
# listens; the proxy's own egress still leaves via the normal container
# network, which is the whole point of an exit node.
# ---------------------------------------------------------------------------
start_tailscale() {
  : "${TS_AUTHKEY:?TS_AUTHKEY is required for MESH=tailscale}"
  SOCK="/var/run/tailscale/tailscaled.sock"
  mkdir -p /var/run/tailscale

  # A filesystem state path needs its directory to exist; 'mem:' does not.
  case "${TS_STATE:=mem:}" in
    mem:) : ;;
    *) mkdir -p "$(dirname "$TS_STATE")" ;;
  esac
  [ "$TS_STATE" = "mem:" ] && log "state is in memory - use an EPHEMERAL auth key"

  log "starting tailscaled (userspace networking)"
  tailscaled \
    --tun="${TS_TUN:-userspace-networking}" \
    --state="$TS_STATE" \
    --socket="$SOCK" \
    ${TS_SOCKS5_SERVER:+--socks5-server="$TS_SOCKS5_SERVER"} \
    >/var/log/tailscaled.log 2>&1 &
  MESH_PID=$!

  i=0; while [ ! -S "$SOCK" ]; do
    i=$((i+1))
    if [ "$i" -gt 30 ]; then
      log "tailscaled socket never appeared"; cat /var/log/tailscaled.log; exit 1
    fi
    sleep 1
  done

  # An untagged auth key rejects --advertise-tags, so NODE_TAG="" opts out.
  # The gateway's tailscale discovery selects nodes by exactly this tag.
  set -- --authkey="$TS_AUTHKEY" --accept-dns=false --accept-routes=false
  [ -n "$NODE_NAME" ] && set -- "$@" --hostname="proxy-${NODE_NAME}"
  [ -n "${NODE_TAG:-}" ] && set -- "$@" --advertise-tags="$NODE_TAG"
  [ -n "${TS_LOGIN_SERVER:-}" ] && set -- "$@" --login-server="$TS_LOGIN_SERVER"
  # shellcheck disable=SC2086 -- TS_EXTRA_ARGS is deliberately word-split.
  [ -n "${TS_EXTRA_ARGS:-}" ] && set -- "$@" $TS_EXTRA_ARGS

  log "joining the tailnet${NODE_NAME:+ as proxy-$NODE_NAME}"
  tailscale --socket="$SOCK" up "$@"
  log "tailnet IP: $(tailscale --socket="$SOCK" ip -4 | head -1)"
}

# ---------------------------------------------------------------------------
# Mesh: NetBird, kernel WireGuard.
# NetBird has no userspace mode, so this path genuinely cannot run without
# NET_ADMIN and a tun device - fail loudly instead of hanging.
# ---------------------------------------------------------------------------
start_netbird() {
  : "${NB_SETUP_KEY:?NB_SETUP_KEY is required for MESH=netbird}"
  if [ ! -c /dev/net/tun ]; then
    log "/dev/net/tun is missing. NetBird needs --cap-add=NET_ADMIN --device=/dev/net/tun."
    log "On a platform that only accepts env vars, use MESH=tailscale instead."
    exit 1
  fi
  SOCK="/var/run/netbird.sock"

  log "starting NetBird daemon"
  # The daemon backgrounds itself; we track liveness via the socket + status,
  # not via the launching PID (which returns immediately).
  netbird service run >/var/log/netbird-daemon.log 2>&1 &
  MESH_PID=$!

  log "waiting for the daemon socket"
  i=0; while [ ! -S "$SOCK" ]; do
    i=$((i+1))
    if [ "$i" -gt 30 ]; then
      log "daemon socket never appeared"; cat /var/log/netbird-daemon.log; exit 1
    fi
    sleep 1
  done

  set -- --setup-key "$NB_SETUP_KEY" --management-url "${NB_MANAGEMENT_URL:-https://api.netbird.io}"
  # No NODE_NAME -> the peer name defaults to the container id. Set NODE_NAME to
  # get a clean, routable node name.
  [ -n "$NODE_NAME" ] && set -- "$@" --hostname "proxy-${NODE_NAME}"

  log "joining the mesh${NODE_NAME:+ as proxy-$NODE_NAME}"
  netbird up "$@"
  netbird status 2>/dev/null | grep -i "NetBird IP" || true
}

MESH_PID=""
case "$MESH" in
  tailscale) start_tailscale ;;
  netbird)   start_netbird ;;
  none)
    # No mesh means the port is reachable by anyone who finds it.
    if [ -z "${PROXY_USER:-}" ] || [ -z "${PROXY_PASS:-}" ]; then
      log "MESH=none without PROXY_USER/PROXY_PASS would publish an OPEN PROXY."
      log "Set both, then point the gateway at it with STATIC_NODES + NODE_PROXY_USER/PASS."
      exit 1
    fi
    log "no mesh; serving authenticated proxy directly"
    ;;
  *) log "unknown MESH='$MESH' (expected auto|tailscale|netbird|none)"; exit 1 ;;
esac

# If the mesh client dies the node is unreachable but the proxy would happily
# keep running, so the platform would never restart it. Take the container down
# instead. SIGTERM to PID 1 is the only signal the kernel delivers here (a
# same-namespace SIGKILL to PID 1 is refused), so the proxy shuts down cleanly
# and the container exits 0 -- run it under `restart: always`, not `on-failure`.
if [ -n "$MESH_PID" ]; then
  ( while kill -0 "$MESH_PID" 2>/dev/null; do sleep 10; done
    log "mesh client exited; stopping the container"
    kill 1 ) &
fi

log "starting proxy on :${PROXY_PORT}"
exec python3 /app/proxy_server.py
