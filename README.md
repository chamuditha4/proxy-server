# Dockerized Authenticated Proxy Server

Turn any data center server into a secure, authenticated proxy — then put an
entire fleet of them behind **one hostname** with no public ports on any node.

## Two ways to run this

| Mode | What you get |
| --- | --- |
| **Standalone node** | One server, one proxy on `IP:8899`. The original setup. |
| **Fleet + gateway** | Any number of nodes reachable through a single endpoint, `proxy.example.com:8899`. The proxy **username** picks the exit IP. Nodes have zero public ports. |

---

# Fleet mode

```
client ──► proxy.example.com:8899        (gateway; the only public listener,
              │                           its A record auto-synced to Cloudflare)
              │  username in Proxy-Authorization selects the node
              ▼  over the tailnet only
   100.64.0.1     100.64.0.2     100.64.0.3
    "de-1"         "de-2"         "us-1"        ← no public ports, no firewall holes
```

Usage from a client:

```bash
curl -x http://de-1:PASS@proxy.example.com:8899 https://ifconfig.me   # that exact node
curl -x http://de:PASS@proxy.example.com:8899   https://ifconfig.me   # any healthy de-* node
curl -x http://any:PASS@proxy.example.com:8899  https://ifconfig.me   # any healthy node
```

The password is the same everywhere (`GATEWAY_PASS`); only the username varies.

## How the IPs stay in sync

Nothing is hand-maintained. Two independent loops do it:

1. **Nodes → gateway.** The gateway polls the Tailscale API every 60s for
   devices tagged `tag:proxy-node`, and turns each one into a route:
   Tailscale hostname `proxy-de-1` → node `de-1` → `100.64.0.1:8899`.
   Provision a new server and it becomes routable within a minute — no gateway
   config change, no redeploy. Destroy one and it drops out the same way.
   A TCP health check every 30s takes unreachable nodes out of rotation.

2. **Gateway → Cloudflare.** The gateway detects its own public IP and keeps
   the `PROXY_HOSTNAME` A record pointed at it. Rebuild or move the gateway and
   the domain follows it.

## Setup

### 1. Tailscale

Create the tag in your [ACL policy](https://login.tailscale.com/admin/acls) so
nodes may self-assign it and only the gateway may reach them:

```jsonc
{
  "tagOwners": {
    "tag:proxy-node": ["autogroup:admin"],
    "tag:proxy-gateway": ["autogroup:admin"]
  },
  "acls": [
    // Only the gateway may talk to the nodes, and only on the proxy port.
    { "action": "accept", "src": ["tag:proxy-gateway"],
      "dst": ["tag:proxy-node:8899"] }
  ]
}
```

Then create:
- An **auth key** (Settings → Keys) — used by each node to join. Make it
  reusable and pre-approved, tagged `tag:proxy-node`.
- An **OAuth client** with `devices:core:read` scope — used by the gateway to
  list devices. Preferred over an API key, which expires every 90 days.

### 2. Cloudflare

Create an API token scoped to **Zone → DNS → Edit** on the one zone.

> The record must stay **DNS-only (grey cloud)**. A forward proxy speaks raw
> HTTP `CONNECT`, which the Cloudflare edge will not pass through. The gateway
> sets `proxied: false` on every sync, so it will un-orange the record for you.

### 3. Deploy the gateway

On the server that will hold the public endpoint:

```bash
git clone https://github.com/chamuditha4/proxy-server.git
cd proxy-server/gateway
cp .env.example .env
$EDITOR .env          # GATEWAY_PASS, Tailscale creds, Cloudflare token + hostname
docker compose up -d --build
```

Join the gateway to the tailnet too, tagged `tag:proxy-gateway`:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey=tskey-auth-xxxx --hostname=proxy-gw \
  --advertise-tags=tag:proxy-gateway
```

Open **only** port 8899 on this one machine:

```bash
sudo ufw allow 8899/tcp
```

Check what it sees:

```bash
curl -s localhost:9000 | jq
```
```json
{
  "nodes": [
    { "name": "de-1", "group": "de", "ip": "100.64.0.1", "port": 8899, "healthy": true },
    { "name": "us-1", "group": "us", "ip": "100.64.0.3", "port": 8899, "healthy": true }
  ],
  "healthy": 2, "total": 2
}
```

### 4. Deploy nodes

On each new server — one command, and it registers itself:

```bash
export TS_AUTHKEY=tskey-auth-xxxx
curl -fsSL https://raw.githubusercontent.com/chamuditha4/proxy-server/main/scripts/install-node.sh \
  | sudo -E bash -s -- de-1
```

The script installs Docker and Tailscale, joins the tailnet as `proxy-de-1`
with `tag:proxy-node`, and starts the proxy **bound to the Tailscale address
only**. Nothing listens on the public interface. The name you pass (`de-1`) is
the username clients will use.

Naming is just a convention: `<group>-<number>`. The part before the last dash
becomes the group, so `de-1` and `de-2` both answer to username `de`.

## Configuration

Gateway settings live in [gateway/.env.example](gateway/.env.example). The ones
you'll actually touch:

| Variable | Purpose |
| --- | --- |
| `GATEWAY_PASS` | The single password all clients use. Make it long. |
| `TAILSCALE_OAUTH_CLIENT_ID` / `_SECRET` | Device-list access. Or `TAILSCALE_API_KEY`. |
| `NODE_TAG` | Which tag marks a proxy node. Default `tag:proxy-node`. |
| `NODE_NAME_PREFIX` | Stripped from the Tailscale hostname. Default `proxy-`. |
| `CF_API_TOKEN`, `PROXY_HOSTNAME`, `CF_ZONE_NAME` | DNS sync. Omit any of them to disable it. |
| `SYNC_INTERVAL` | How fast new nodes appear, in seconds. Default 60. |

## Node auth

Nodes ship with **no basic auth** in fleet mode, and that is deliberate. The
security boundary is the tailnet: the node binds only to its `100.x` address,
the ACL admits only `tag:proxy-gateway`, and the gateway authenticates every
client. Nothing on the public internet can reach a node to attempt auth at all.

If you want defence in depth anyway, set `PROXY_USER`/`PROXY_PASS` on the nodes
and `NODE_PROXY_USER`/`NODE_PROXY_PASS` on the gateway. **Caveat:** the gateway
rewrites credentials on the first request of a connection only. HTTPS traffic
is unaffected (one `CONNECT`, then an opaque tunnel), but a keep-alive
connection issuing several plain-HTTP requests will have later requests
rejected by the node. Leave node auth off unless you need it.

---

# Standalone mode

The original single-server setup, with the proxy exposed directly.

```bash
git clone https://github.com/chamuditha4/proxy-server.git
cd proxy-server
cp .env.example .env
$EDITOR .env          # set PROXY_USER, PROXY_PASS, and NODE_BIND_IP=0.0.0.0
docker compose up -d
sudo ufw allow 8899/tcp
```

`NODE_BIND_IP` defaults to `127.0.0.1`, so a missing value exposes nothing
rather than everything. Set it to `0.0.0.0` to publish publicly, and set
`PROXY_USER`/`PROXY_PASS` — an open proxy on the public internet is found and
abused within hours.

Point your browser's HTTP **and** HTTPS proxy at the server IP, port 8899.

## Features

- **Instant deployment** — Docker and Docker Compose, one command per server.
- **Single endpoint** — one hostname and port for the whole fleet.
- **Username-based exit selection** — pick a node, a region, or any node.
- **Automatic discovery** — new servers register themselves via Tailscale.
- **Automatic DNS** — the gateway keeps its Cloudflare record current.
- **Health checked** — dead nodes leave rotation on their own.
- **No exposed ports on nodes** — the only public listener is the gateway.
- **HTTPS tunnelling** — full `CONNECT` support end to end.

## Troubleshooting

**`502 No healthy node matches 'x'`** — check `curl -s localhost:9000` on the
gateway. If the node is missing, it isn't tagged `tag:proxy-node`
(`tailscale status --json | jq .Self.Tags` on the node). If it's present but
unhealthy, the ACL is blocking port 8899 or the container isn't running.

**`407` on every request** — the password must equal `GATEWAY_PASS` exactly;
the username is routing only and never authenticates.

**Connections hang after DNS resolves** — the Cloudflare record is orange
(proxied). It must be DNS-only.

**Node reachable publicly** — `ss -ltnp | grep 8899` should show `100.x.y.z:8899`,
not `0.0.0.0:8899`. Fix `NODE_BIND_IP` in the node's `.env` and recreate.

## License

MIT
