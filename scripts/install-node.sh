#!/usr/bin/env bash
#
# One-shot provisioning for a proxy node.
#
#   TS_AUTHKEY=tskey-auth-xxxx ./scripts/install-node.sh de-1
#
# Joins the tailnet as "proxy-<name>" with tag:proxy-node, starts the proxy
# bound to the Tailscale address only, and locks down the public interface.
# The gateway picks the node up on its next sync — no gateway changes needed.

set -euo pipefail

NODE_NAME="${1:-}"
NODE_PREFIX="${NODE_PREFIX:-proxy-}"
NODE_TAG="${NODE_TAG:-tag:proxy-node}"
REPO_URL="${REPO_URL:-https://github.com/chamuditha4/proxy-server.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/proxy-server}"

if [[ -z "$NODE_NAME" ]]; then
  echo "usage: TS_AUTHKEY=tskey-auth-xxxx $0 <node-name>   e.g. de-1" >&2
  exit 1
fi

if [[ -z "${TS_AUTHKEY:-}" ]]; then
  echo "TS_AUTHKEY is required (create one at https://login.tailscale.com/admin/settings/keys)" >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "run as root (sudo -E $0 $NODE_NAME)" >&2
  exit 1
fi

echo "==> Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

echo "==> Installing Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "==> Joining tailnet as ${NODE_PREFIX}${NODE_NAME}"
tailscale up \
  --authkey="${TS_AUTHKEY}" \
  --hostname="${NODE_PREFIX}${NODE_NAME}" \
  --advertise-tags="${NODE_TAG}" \
  --accept-dns=false \
  --ssh=false

TS_IP="$(tailscale ip -4 | head -n1)"
if [[ -z "$TS_IP" ]]; then
  echo "could not read the Tailscale IPv4 address" >&2
  exit 1
fi
echo "    Tailscale IP: ${TS_IP}"

echo "==> Fetching the proxy"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

echo "==> Writing configuration"
cat > "${INSTALL_DIR}/.env" <<ENVEOF
PROXY_PORT=8899
NODE_BIND_IP=${TS_IP}
# Auth happens at the gateway; this node is tailnet-only.
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

echo "==> Locking down the public interface"
if command -v ufw >/dev/null 2>&1; then
  # Docker publishes ports below ufw's rules, so binding to the Tailscale
  # address (above) is what actually keeps 8899 off the public interface.
  # ufw here just protects everything else.
  ufw allow in on tailscale0 >/dev/null 2>&1 || true
  ufw deny 8899/tcp >/dev/null 2>&1 || true
fi

echo
echo "Node '${NODE_NAME}' is up on ${TS_IP}:8899 (tailnet only)."
echo "Route traffic through it with username '${NODE_NAME}' at your gateway."
