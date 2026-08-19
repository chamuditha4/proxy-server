"""Node registry: discovers proxy nodes from the Tailscale API and health-checks them."""

import asyncio
import os
import random
import time

import httpx

TAILSCALE_API = "https://api.tailscale.com/api/v2"


class Node:
    def __init__(self, name, ip, port, tailscale_name):
        self.name = name
        self.ip = ip
        self.port = port
        self.tailscale_name = tailscale_name
        self.healthy = False
        self.last_check = 0.0

    @property
    def group(self):
        # "de-1" -> "de", used so a client can ask for any node in a region
        return self.name.rsplit("-", 1)[0] if "-" in self.name else self.name

    def as_dict(self):
        return {
            "name": self.name,
            "group": self.group,
            "ip": self.ip,
            "port": self.port,
            "healthy": self.healthy,
            "tailscale_name": self.tailscale_name,
            "last_check": self.last_check,
        }


class Registry:
    """Keeps an up-to-date map of node name -> backend address."""

    def __init__(self):
        self.tailnet = os.environ.get("TAILNET", "-")
        self.api_key = os.environ.get("TAILSCALE_API_KEY")
        self.oauth_id = os.environ.get("TAILSCALE_OAUTH_CLIENT_ID")
        self.oauth_secret = os.environ.get("TAILSCALE_OAUTH_CLIENT_SECRET")
        self.node_tag = os.environ.get("NODE_TAG", "tag:proxy-node")
        self.name_prefix = os.environ.get("NODE_NAME_PREFIX", "proxy-")
        self.node_port = int(os.environ.get("NODE_PORT", 8899))
        self.sync_interval = int(os.environ.get("SYNC_INTERVAL", 60))
        self.health_interval = int(os.environ.get("HEALTH_INTERVAL", 30))
        self.health_timeout = float(os.environ.get("HEALTH_TIMEOUT", 3))
        self.any_keyword = os.environ.get("ANY_KEYWORD", "any")

        self.nodes = {}
        self._token = None
        self._token_expires = 0.0

    # --- Tailscale API ---------------------------------------------------

    async def _auth_header(self, client):
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}

        if not self.oauth_id or not self.oauth_secret:
            raise RuntimeError(
                "Set TAILSCALE_API_KEY, or TAILSCALE_OAUTH_CLIENT_ID + "
                "TAILSCALE_OAUTH_CLIENT_SECRET"
            )

        if self._token and time.time() < self._token_expires - 60:
            return {"Authorization": f"Bearer {self._token}"}

        resp = await client.post(
            f"{TAILSCALE_API}/oauth/token",
            data={
                "client_id": self.oauth_id,
                "client_secret": self.oauth_secret,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires = time.time() + payload.get("expires_in", 3600)
        return {"Authorization": f"Bearer {self._token}"}

    def _node_name(self, device):
        # Tailscale hostname "proxy-de-1" -> node name "de-1"
        hostname = device.get("hostname") or device.get("name", "").split(".")[0]
        if self.name_prefix and hostname.startswith(self.name_prefix):
            hostname = hostname[len(self.name_prefix):]
        return hostname

    @staticmethod
    def _ipv4(device):
        for addr in device.get("addresses", []):
            if ":" not in addr:
                return addr
        return None

    async def sync(self):
        """Pull the device list and rebuild the routing table."""
        async with httpx.AsyncClient(timeout=15) as client:
            headers = await self._auth_header(client)
            resp = await client.get(
                f"{TAILSCALE_API}/tailnet/{self.tailnet}/devices", headers=headers
            )
            resp.raise_for_status()
            devices = resp.json().get("devices", [])

        discovered = {}
        for device in devices:
            if self.node_tag not in (device.get("tags") or []):
                continue
            ip = self._ipv4(device)
            if not ip:
                continue
            name = self._node_name(device)
            existing = self.nodes.get(name)
            node = Node(name, ip, self.node_port, device.get("name", name))
            if existing and existing.ip == ip:
                # Preserve health state across syncs so traffic isn't paused.
                node.healthy = existing.healthy
                node.last_check = existing.last_check
            discovered[name] = node

        added = set(discovered) - set(self.nodes)
        removed = set(self.nodes) - set(discovered)
        if added:
            print(f"[registry] nodes added: {sorted(added)}", flush=True)
        if removed:
            print(f"[registry] nodes removed: {sorted(removed)}", flush=True)

        self.nodes = discovered
        return self.nodes

    # --- health ----------------------------------------------------------

    async def _check(self, node):
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(node.ip, node.port), self.health_timeout
            )
            writer.close()
            await writer.wait_closed()
            healthy = True
        except Exception:
            healthy = False

        if healthy != node.healthy:
            state = "healthy" if healthy else "unhealthy"
            print(f"[registry] {node.name} ({node.ip}) is now {state}", flush=True)
        node.healthy = healthy
        node.last_check = time.time()

    async def health_check(self):
        await asyncio.gather(*(self._check(n) for n in list(self.nodes.values())))

    # --- selection -------------------------------------------------------

    def select(self, key):
        """Resolve a proxy username to a backend node.

        "de-1" -> that exact node
        "de"   -> a random healthy node in the de-* group
        "any"  -> a random healthy node anywhere
        """
        key = key.strip().lower()

        node = self.nodes.get(key)
        if node:
            return node if node.healthy else None

        if key == self.any_keyword:
            pool = [n for n in self.nodes.values() if n.healthy]
        else:
            pool = [n for n in self.nodes.values() if n.healthy and n.group == key]

        return random.choice(pool) if pool else None

    # --- background loops ------------------------------------------------

    async def run(self):
        async def sync_loop():
            while True:
                try:
                    await self.sync()
                    await self.health_check()
                except Exception as exc:
                    print(f"[registry] sync failed: {exc}", flush=True)
                await asyncio.sleep(self.sync_interval)

        async def health_loop():
            while True:
                await asyncio.sleep(self.health_interval)
                try:
                    await self.health_check()
                except Exception as exc:
                    print(f"[registry] health check failed: {exc}", flush=True)

        await asyncio.gather(sync_loop(), health_loop())
