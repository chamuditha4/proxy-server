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
              ▼  over the private mesh only
   100.92.0.1     100.92.0.2     100.92.0.3
    "de-1"         "de-2"         "us-1"        ← no public ports, no firewall holes
```

```bash
curl -x http://de-1:PASS@proxy.example.com:8899 https://ifconfig.me   # that exact node
curl -x http://de:PASS@proxy.example.com:8899   https://ifconfig.me   # any healthy de-* node
curl -x http://any:PASS@proxy.example.com:8899  https://ifconfig.me   # any healthy node
```

The password is the same everywhere (`GATEWAY_PASS`); only the username varies.

## How the IPs stay in sync

Nothing is hand-maintained. Two independent loops:

1. **Nodes → gateway.** Every 60s the gateway asks the mesh's API which nodes
   exist, and turns each into a route: mesh hostname `proxy-de-1` → node `de-1`
   → `100.92.0.1:8899`. Provision a server and it becomes routable within a
   minute — no gateway config change, no redeploy. Destroy one and it drops out
   the same way. A TCP health check every 30s takes unreachable nodes out of
   rotation.

2. **Gateway → Cloudflare.** The gateway detects its own public IP and keeps
   the `PROXY_HOSTNAME` A record pointed at it. Rebuild or move the gateway and
   the domain follows.

## Choosing a mesh

Only step 1 depends on the mesh, so the backend is pluggable — set
`DISCOVERY_BACKEND`. Cloudflare DNS sync works with all of them.

| `DISCOVERY_BACKEND` | Cost | Data path | Notes |
| --- | --- | --- | --- |
| **`netbird`** ⭐ | Free: 100 peers / 5 users hosted, **unlimited self-hosted** | Direct WireGuard, peer-to-peer | Fully open source (BSD-3), control plane included. Self-host and no vendor is involved at all. |
| `tailscale` | Free: 100 devices / 3 users | Direct WireGuard, peer-to-peer | Most polished. Control plane is proprietary and hosted — can't self-host it. |
| `cloudflare` | Free tier of Zero Trust | **Hairpins through Cloudflare's edge** | One vendor for DNS and mesh. Read the caveats below before choosing it. |
| `static` | Free | Whatever you built | Plain WireGuard, Nebula, or a cloud VPC. You maintain the node list; health checks still run. |

**Recommendation: `netbird`.** It's the only option that is both free at any
scale and keeps proxy traffic on a direct node-to-node path. Since you're
self-hosting proxies anyway, self-hosting the coordination server removes the
last third party from the design.

One exception: NetBird has no userspace WireGuard mode, so it can't run on a
platform that won't grant `NET_ADMIN` and `/dev/net/tun`. If your nodes live on
Railway, Render, Fly or similar, use `tailscale` for those — see
[Nodes on env-only platforms](#nodes-on-env-only-platforms-railway-render-fly-koyeb-cloud-run).

### About the Cloudflare backend

It works, and it's implemented — nodes run `cloudflared` advertising a private
`/32`, the gateway joins the same Zero Trust org and dials those addresses
directly. But two things to weigh first:

- **Every byte detours through Cloudflare's edge.** Client → gateway → Cloudflare
  → node → destination. A WireGuard mesh goes gateway → node directly. For a
  proxy, where latency and throughput are the product, this is the wrong shape.
- **Licensing risk.** Backhauling arbitrary forward-proxy traffic over the free
  Zero Trust tier is not what it's sold for, and an enforcement action would
  take your DNS down with it — the same account serves both.

Use Cloudflare for what it's genuinely good at here, which is the DNS record.

## Setup

### 1. Bring up the mesh

<details open>
<summary><b>NetBird (recommended)</b></summary>

Either sign up at [netbird.io](https://netbird.io) or
[self-host](https://docs.netbird.io/selfhosted/selfhosted-guide) the management
server. Then:

1. Create a group named `proxy-node`.
2. Create a **setup key** (Settings → Setup Keys), reusable, with `proxy-node`
   set as an auto-assigned group. Nodes using this key land in the group
   automatically — that's what makes them discoverable.
3. Create an **access token** (Settings → Tokens) for the gateway.
4. Add a NetBird access-control policy allowing the gateway's group to reach
   `proxy-node` on TCP 8899, and nothing else.

Set in `gateway/.env`: `DISCOVERY_BACKEND=netbird`, `NETBIRD_TOKEN`, and
`NETBIRD_API_URL` if self-hosted.
</details>

<details>
<summary><b>Tailscale</b></summary>

In your [ACL policy](https://login.tailscale.com/admin/acls):

```jsonc
{
  "tagOwners": {
    "tag:proxy-node": ["autogroup:admin"],
    "tag:proxy-gateway": ["autogroup:admin"]
  },
  "acls": [
    { "action": "accept", "src": ["tag:proxy-gateway"],
      "dst": ["tag:proxy-node:8899"] }
  ]
}
```

Create a reusable auth key tagged `tag:proxy-node` for nodes, and an OAuth
client with `devices:core:read` for the gateway (API keys expire every 90 days).

Set: `DISCOVERY_BACKEND=tailscale`, `TAILSCALE_OAUTH_CLIENT_ID`/`_SECRET`.
</details>

<details>
<summary><b>Cloudflare Tunnel</b></summary>

Pick a private range for nodes, e.g. `10.99.0.0/24`, one `/32` each. Per node:
create a tunnel named `proxy-<name>`, then
`cloudflared tunnel route ip add 10.99.0.1/32 proxy-de-1`. Enroll the gateway
in the same Zero Trust org so it can reach those routes.

Set: `DISCOVERY_BACKEND=cloudflare`, `CF_ACCOUNT_ID`, `CF_DISCOVERY_TOKEN`
(Account → Cloudflare Tunnel:Read + Zero Trust:Read — the zone-scoped DNS token
below cannot read tunnels).
</details>

<details>
<summary><b>Static / plain WireGuard</b></summary>

Set `DISCOVERY_BACKEND=static` and list nodes yourself:

```
STATIC_NODES=de-1=10.8.0.1,us-1=10.8.0.2:8899
```

or point `STATIC_NODES_FILE` at a JSON file, re-read on every sync.
</details>

### 2. Cloudflare DNS

Create an API token scoped to **Zone → DNS → Edit** on the one zone. Set
`CF_API_TOKEN`, `PROXY_HOSTNAME`, and `CF_ZONE_NAME` in `gateway/.env`.

> The record must stay **DNS-only (grey cloud)**. A forward proxy speaks raw
> HTTP `CONNECT`, which the Cloudflare edge will not pass through. The gateway
> sets `proxied: false` on every sync, so it un-oranges the record for you.

### 3. Deploy the gateway

On the server that holds the public endpoint:

```bash
git clone https://github.com/chamuditha4/proxy-server.git
cd proxy-server/gateway
cp .env.example .env
$EDITOR .env          # GATEWAY_PASS, DISCOVERY_BACKEND + its creds, CF_API_TOKEN
docker compose up -d --build
```

Join it to your mesh too, then open **only** port 8899 on this one machine:

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
    { "name": "de-1", "group": "de", "ip": "100.92.0.1", "port": 8899, "healthy": true },
    { "name": "us-1", "group": "us", "ip": "100.92.0.3", "port": 8899, "healthy": true }
  ],
  "healthy": 2, "total": 2
}
```

### 4. Deploy nodes

One command per server, and it registers itself:

```bash
# NetBird
export MESH=netbird NB_SETUP_KEY=xxxxxxxx
curl -fsSL https://raw.githubusercontent.com/chamuditha4/proxy-server/main/scripts/install-node.sh \
  | sudo -E bash -s -- de-1

# Tailscale
export MESH=tailscale TS_AUTHKEY=tskey-auth-xxxx
# Cloudflare
export MESH=cloudflare CF_TOKEN=xxxxx NODE_IP=10.99.0.1
```

The script installs Docker and the mesh client, joins as `proxy-de-1`, and
starts the proxy **bound to the mesh address only**. Nothing listens on the
public interface. The name you pass (`de-1`) is the username clients will use.

Naming is a convention: `<group>-<number>`. The part before the last dash is the
group, so `de-1` and `de-2` both answer to username `de`.

### Nodes on env-only platforms (Railway, Render, Fly, Koyeb, Cloud Run)

Those platforms run one image and give you nothing but environment variables —
no `--cap-add`, no `--device`, no volumes. The all-in-one node image
[docker/Dockerfile.aio](docker/Dockerfile.aio) is built for exactly that: mesh
client and proxy in a single container, every knob an env var.

```bash
# Anywhere that only accepts env vars. Note: no flags at all.
docker run -d \
  -e MESH=tailscale \
  -e NODE_NAME=de-1 \
  -e TS_AUTHKEY=tskey-auth-xxxxxxxx \
  twozerotwo/proxy-node-aio:1.1.0
```

On Railway: point the service at this repo, set `RAILWAY_DOCKERFILE_PATH` to
`docker/Dockerfile.aio`, add the three variables above, deploy. Nothing else.
Leave the service **private** — the node is reached over the tailnet, not over
Railway's public networking.

**It has to be Tailscale here, not NetBird.** Kernel WireGuard needs
`NET_ADMIN` and `/dev/net/tun`, which these platforms don't hand out. Tailscale
is the only one of the two with a userspace mode (`--tun=userspace-networking`),
where its netstack accepts inbound tailnet connections and forwards them to the
proxy on localhost. The proxy's own egress still leaves through the platform's
normal network — which is the whole point of an exit node. Use an **ephemeral,
pre-authorized, tagged** auth key: state is kept in memory (`TS_STATE=mem:`)
since there's no volume, so the peer re-registers on every deploy and removes
itself when the container stops.

The same image also runs the other two modes:

| `MESH` | Needs | Use when |
| --- | --- | --- |
| `tailscale` | nothing | PaaS, or any host where you can't grant capabilities |
| `netbird` | `--cap-add=NET_ADMIN --device=/dev/net/tun` | your own VPS |
| `none` | a public port + `PROXY_USER`/`PROXY_PASS` | platform gives you a public TCP endpoint and you'd rather skip the mesh |
| `auto` (default) | — | picks `tailscale` if `TS_AUTHKEY` is set, `netbird` if `NB_SETUP_KEY` is |

`MESH=none` publishes the proxy directly, so the image refuses to start unless
both `PROXY_USER` and `PROXY_PASS` are set. Register such a node with the
gateway by hand — `DISCOVERY_BACKEND=static`,
`STATIC_NODES=de-1=node.up.railway.app:12345`, plus `NODE_PROXY_USER` /
`NODE_PROXY_PASS` (read the caveat under [Node auth](#node-auth) first).

Every variable the all-in-one image reads:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MESH` | `auto` | `auto`, `tailscale`, `netbird`, `none` |
| `NODE_NAME` | container id | Mesh hostname `proxy-<NODE_NAME>` **and** the client username |
| `PROXY_PORT` | `$PORT`, else `8899` | Platform-injected `$PORT` is picked up automatically |
| `TS_AUTHKEY` | — | Tailscale auth key. Required for `MESH=tailscale` |
| `NODE_TAG` | `tag:proxy-node` | Advertised tag; the gateway discovers by it. Empty string to skip |
| `TS_STATE` | `mem:` | Set to a path only if you actually have a volume |
| `TS_LOGIN_SERVER` | — | Headscale or another control server |
| `TS_EXTRA_ARGS` | — | Appended verbatim to `tailscale up` |
| `NB_SETUP_KEY` | — | NetBird setup key. Required for `MESH=netbird` |
| `NB_MANAGEMENT_URL` | `https://api.netbird.io` | Self-hosted NetBird |
| `PROXY_USER` / `PROXY_PASS` | empty | Leave empty behind a gateway; required for `MESH=none` |

If the mesh client dies the container exits rather than sitting there as an
unreachable-but-healthy proxy, so the platform's restart policy takes over.

## Configuration

Full reference in [gateway/.env.example](gateway/.env.example). The ones you'll
actually touch:

| Variable | Purpose |
| --- | --- |
| `GATEWAY_PASS` | The single password all clients use. Make it long. |
| `DISCOVERY_BACKEND` | `netbird`, `tailscale`, `cloudflare`, or `static`. |
| `NODE_NAME_PREFIX` | Stripped from the mesh hostname. Default `proxy-`. |
| `NODE_GROUP` / `NODE_TAG` | Which NetBird group / Tailscale tag marks a node. |
| `CF_API_TOKEN`, `PROXY_HOSTNAME`, `CF_ZONE_NAME` | DNS sync. Omit any to disable. |
| `SYNC_INTERVAL` | How fast new nodes appear, in seconds. Default 60. |

Adding another mesh means one file in
[gateway/discovery/](gateway/discovery/) returning `NodeSpec(name, ip)`.
Routing, health checks and failover are provider-agnostic.

## Node auth

Nodes ship with **no basic auth** in fleet mode, deliberately. The security
boundary is the mesh: the node binds only to its overlay address, the mesh ACL
admits only the gateway, and the gateway authenticates every client. Nothing on
the public internet can reach a node to attempt auth at all.

For defence in depth anyway, set `PROXY_USER`/`PROXY_PASS` on nodes and
`NODE_PROXY_USER`/`NODE_PROXY_PASS` on the gateway. **Caveat:** the gateway
rewrites credentials on the first request of a connection only. HTTPS is
unaffected (one `CONNECT`, then an opaque tunnel), but a keep-alive connection
issuing several plain-HTTP requests will have later requests rejected. Leave
node auth off unless you need it.

---

# Standalone mode

The original single-server setup, proxy exposed directly.

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

- **Instant deployment** — Docker Compose, one command per server.
- **Single endpoint** — one hostname and port for the whole fleet.
- **Username-based exit selection** — pick a node, a region, or any node.
- **Automatic discovery** — new servers register themselves via the mesh.
- **Pluggable mesh** — NetBird, Tailscale, Cloudflare Tunnel, or a static list.
- **Automatic DNS** — the gateway keeps its Cloudflare record current.
- **Health checked** — dead nodes leave rotation on their own.
- **No exposed ports on nodes** — the only public listener is the gateway.
- **HTTPS tunnelling** — full `CONNECT` support end to end.

## Troubleshooting

**`502 No healthy node matches 'x'`** — check `curl -s localhost:9000` on the
gateway. Missing entirely: the node isn't in the group/tag the gateway looks
for. Present but `"healthy": false`: the mesh ACL is blocking TCP 8899, or the
container isn't running.

**`407` on every request** — the password must equal `GATEWAY_PASS` exactly;
the username is routing only and never authenticates.

**Connections hang after DNS resolves** — the Cloudflare record is orange
(proxied). It must be DNS-only.

**Node reachable publicly** — `ss -ltn | grep 8899` should show the overlay
address, not `0.0.0.0:8899`. Fix `NODE_BIND_IP` in the node's `.env` and recreate.

## License

MIT
