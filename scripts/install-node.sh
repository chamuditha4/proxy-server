#!/usr/bin/env bash
#
# One-shot provisioning for a proxy node.
#
#   MESH=tailscale  TS_AUTHKEY=tskey-auth-xxx  ./install-node.sh de-1
#   MESH=netbird    NB_SETUP_KEY=xxxxxxxx      ./install-node.sh de-1
#   MESH=cloudflare CF_TOKEN=xxx NODE_IP=10.99.0.1 ./install-node.sh de-1
#
# Joins the chosen mesh as "proxy-<name>", starts the proxy bound to the mesh
# address only, and leaves nothing listening on the public interface. The
# gateway picks the node up on its next sync -- no gateway changes needed.

set -euo pipefail

NODE_NAME="${1:-}"
MESH="${MESH:-tailscale}"
NODE_PREFIX="${NODE_PREFIX:-proxy-}"
REPO_URL="${REPO_URL:-https://github.com/chamuditha4/proxy-server.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/proxy-server}"
MESH_HOSTNAME="${NODE_PREFIX}${NODE_NAME}"

die() { echo "error: $*" >&2; exit 1; }

[[ -n "$NODE_NAME" ]] || die "usage: MESH=<mesh> ... $0 <node-name>   e.g. de-1"
[[ $EUID -eq 0 ]] || die "run as root (sudo -E $0 $NODE_NAME)"

echo "==> Installing Docker"
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh

# --- join the mesh, and report the address the proxy should bind to ----------

join_tailscale() {
  [[ -n "${TS_AUTHKEY:-}" ]] || die "TS_AUTHKEY is required for MESH=tailscale"
  command -v tailscale >/dev/null 2>&1 || curl -fsSL https://tailscale.com/install.sh | sh
  tailscale up \
    --authkey="${TS_AUTHKEY}" \
    --hostname="${MESH_HOSTNAME}" \
    --advertise-tags="${NODE_TAG:-tag:proxy-node}" \
    --accept-dns=false \
    --ssh=false
  tailscale ip -4 | head -n1
}

join_netbird() {
  [[ -n "${NB_SETUP_KEY:-}" ]] || die "NB_SETUP_KEY is required for MESH=netbird"
  if ! command -v netbird >/dev/null 2>&1; then
    curl -fsSL https://pkgs.netbird.io/install.sh | sh
  fi
  # The setup key must be configured in NetBird to auto-assign the group the
  # gateway looks for (NODE_GROUP, default "proxy-node").
  local args=(--setup-key "${NB_SETUP_KEY}" --hostname "${MESH_HOSTNAME}")
  [[ -n "${NB_MANAGEMENT_URL:-}" ]] && args+=(--management-url "${NB_MANAGEMENT_URL}")
  netbird up "${args[@]}"

  # Wait for the overlay address to be assigned. Preferred source is the
  # documented "netbirdIp" JSON field; the wt0 interface is the fallback.
  local ip=""
  for _ in $(seq 1 30); do
    ip="$(netbird status --json 2>/dev/null | grep -o '"netbirdIp": *"[^"]*"' \
          | head -n1 | cut -d'"' -f4 | cut -d/ -f1)"
    [[ -z "$ip" ]] && ip="$(ip -4 -o addr show wt0 2>/dev/null \
          | awk '{print $4}' | cut -d/ -f1)"
    [[ -n "$ip" ]] && break
    sleep 2
  done
  echo "$ip"
}

join_cloudflare() {
  [[ -n "${CF_TOKEN:-}" ]] || die "CF_TOKEN is required for MESH=cloudflare"
  [[ -n "${NODE_IP:-}"  ]] || die "NODE_IP is required for MESH=cloudflare (e.g. 10.99.0.1)"
  if ! command -v cloudflared >/dev/null 2>&1; then
    curl -fsSL -o /tmp/cloudflared.deb \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    dpkg -i /tmp/cloudflared.deb
  fi

  # Give the node a private address of its own for the tunnel to advertise.
  # /32 on loopback: routable through the tunnel, invisible everywhere else.
  ip addr add "${NODE_IP}/32" dev lo 2>/dev/null || true

  cloudflared service install "${CF_TOKEN}"
  systemctl enable --now cloudflared

  echo "${NODE_IP}"
  echo "NOTE: add the private route once, from your workstation:" >&2
  echo "  cloudflared tunnel route ip add ${NODE_IP}/32 ${MESH_HOSTNAME}" >&2
}

join_static() {
  [[ -n "${NODE_IP:-}" ]] || die "NODE_IP is required for MESH=static"
  echo "${NODE_IP}"
}

echo "==> Joining mesh: ${MESH} as ${MESH_HOSTNAME}"
# MESH_IFACE is the interface the gateway's traffic arrives on. Set it here,
# not inside the join functions -- those run in $( ) subshells.
case "$MESH" in
  tailscale)  BIND_IP="$(join_tailscale)";  MESH_IFACE=tailscale0 ;;
  netbird)    BIND_IP="$(join_netbird)";    MESH_IFACE=wt0        ;;
  cloudflare) BIND_IP="$(join_cloudflare)"; MESH_IFACE=lo         ;;
  static)     BIND_IP="$(join_static)";     MESH_IFACE="${MESH_IFACE:-wg0}" ;;
  *) die "unknown MESH '${MESH}' (tailscale, netbird, cloudflare, static)" ;;
esac

[[ -n "$BIND_IP" ]] || die "could not determine the mesh address for this node"
echo "    Mesh address: ${BIND_IP}"

echo "==> Fetching the proxy"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

echo "==> Writing configuration"
cat > "${INSTALL_DIR}/.env" <<ENVEOF
PROXY_PORT=8899
NODE_BIND_IP=${BIND_IP}
# Auth happens at the gateway; this node is mesh-only.
PROXY_USER=
PROXY_PASS=
ENVEOF

echo "==> Starting the proxy"
cd "${INSTALL_DIR}"
if docker compose version >/dev/null 2>&1; then
  docker compose up -d --build
else
  docker-compose up -d --build
fi

echo "==> Allowing the gateway in over the mesh interface"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "^Status: active"; then
  # With ufw's default-deny inbound, traffic arriving on the overlay interface
  # is dropped too -- including the gateway's. The public interface is
  # unaffected: the proxy never binds to it.
  ufw allow in on "${MESH_IFACE}" to any port 8899 proto tcp
fi

echo
echo "Node '${NODE_NAME}' is up on ${BIND_IP}:8899 (mesh only)."
echo "Verify nothing is public:  ss -ltn | grep 8899"
echo "Route traffic through it with username '${NODE_NAME}' at your gateway."
