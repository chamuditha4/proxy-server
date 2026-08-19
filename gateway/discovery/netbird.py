"""Discovery via the NetBird API. Nodes are peers in NODE_GROUP.

Works against NetBird Cloud or a self-hosted management server — only
NETBIRD_API_URL changes.
"""

import os

import httpx

from .base import NodeSpec, Provider


class NetbirdProvider(Provider):
    name = "netbird"

    def __init__(self):
        super().__init__()
        self.require("NETBIRD_TOKEN")
        self.api_url = os.environ.get(
            "NETBIRD_API_URL", "https://api.netbird.io"
        ).rstrip("/")
        self.token = os.environ["NETBIRD_TOKEN"]
        self.group = os.environ.get("NODE_GROUP", "proxy-node")

    async def fetch(self):
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self.api_url}/api/peers",
                headers={
                    "Authorization": f"Token {self.token}",
                    "Accept": "application/json",
                },
            )
            resp.raise_for_status()
            peers = resp.json()

        specs = []
        for peer in peers:
            groups = {g.get("name") for g in (peer.get("groups") or [])}
            if self.group not in groups:
                continue
            ip = peer.get("ip")
            if not ip:
                continue
            # NetBird exposes two names: "name" (set by `netbird up --hostname`,
            # user-controlled, correct for single-container/all-in-one nodes) and
            # "hostname" (the OS hostname, which a container can't set without
            # SYS_ADMIN). Prefer whichever already carries our prefix; otherwise
            # fall back to "name". This keeps every deploy path routable.
            candidates = [c for c in (peer.get("name"), peer.get("hostname")) if c]
            chosen = next(
                (c for c in candidates if c.startswith(self.name_prefix)),
                candidates[0] if candidates else "",
            )
            specs.append(
                NodeSpec(
                    name=self.strip_prefix(chosen),
                    ip=ip,
                    port=self.node_port,
                    source_name=peer.get("name") or peer.get("hostname"),
                    # NetBird reports live connection state; trust a False.
                    online=peer.get("connected"),
                )
            )
        return specs
