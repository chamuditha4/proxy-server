"""Discovery via Cloudflare Tunnel private network routes.

Each node runs `cloudflared` advertising its own /32 as a private route. The
gateway joins the same Zero Trust org (WARP in mode "Gateway with WARP", or its
own WARP Connector) and so can dial those addresses directly — no per-node
`cloudflared access` process to supervise.

Tunnels must be named "<NODE_NAME_PREFIX><node>", e.g. proxy-de-1.

Read the README before choosing this backend: it puts all proxy payload
traffic through Cloudflare's edge, which costs latency and has licensing
implications.
"""

import os

import httpx

from .base import NodeSpec, Provider

CF_API = "https://api.cloudflare.com/client/v4"


class CloudflareTunnelProvider(Provider):
    name = "cloudflare"

    def __init__(self):
        super().__init__()
        self.account_id = os.environ.get("CF_ACCOUNT_ID")
        # A DNS-scoped token cannot read tunnels, so this is usually separate.
        self.token = os.environ.get("CF_DISCOVERY_TOKEN") or os.environ.get(
            "CF_API_TOKEN"
        )
        self.vnet_id = os.environ.get("CF_VIRTUAL_NETWORK_ID")

        if not self.account_id or not self.token:
            raise RuntimeError(
                "set CF_ACCOUNT_ID and CF_DISCOVERY_TOKEN "
                "(Account > Cloudflare Tunnel:Read + Zero Trust:Read)"
            )

    async def _get(self, client, path, params=None):
        resp = await client.get(
            f"{CF_API}/accounts/{self.account_id}/{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            params=params,
        )
        resp.raise_for_status()
        return resp.json().get("result") or []

    async def fetch(self):
        params = {"is_deleted": "false", "per_page": 1000}
        if self.vnet_id:
            params["virtual_network_id"] = self.vnet_id

        async with httpx.AsyncClient(timeout=15) as client:
            routes = await self._get(client, "teamnet/routes", params)
            tunnels = await self._get(
                client, "cfd_tunnel", {"is_deleted": "false", "per_page": 1000}
            )

        # "healthy" means cloudflared is connected to the edge. Anything else
        # (degraded, down, inactive) means the node cannot serve traffic.
        status = {t.get("id"): t.get("status") for t in tunnels}

        specs = []
        for route in routes:
            tunnel_name = route.get("tunnel_name") or ""
            if self.name_prefix and not tunnel_name.startswith(self.name_prefix):
                continue
            network = route.get("network") or ""
            ip = network.split("/")[0]
            if not ip:
                continue
            tunnel_status = status.get(route.get("tunnel_id"))
            specs.append(
                NodeSpec(
                    name=self.strip_prefix(tunnel_name),
                    ip=ip,
                    port=self.node_port,
                    source_name=tunnel_name,
                    online=(tunnel_status == "healthy") if tunnel_status else None,
                )
            )
        return specs
