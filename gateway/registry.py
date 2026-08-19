"""Node registry: keeps a live map of node name -> reachable backend address.

Discovery is pluggable (see the discovery package); health checking, username
routing and failover are the same whichever mesh you run.
"""

import asyncio
import os
import random
import time

from discovery import build_provider


class Node:
    def __init__(self, name, ip, port, source_name):
        self.name = name
        self.ip = ip
        self.port = port
        self.source_name = source_name
        self.healthy = False
        self.last_check = 0.0

    @property
    def group(self):
        # "de-1" -> "de", so a client can ask for any node in a region
        return self.name.rsplit("-", 1)[0] if "-" in self.name else self.name

    def as_dict(self):
        return {
            "name": self.name,
            "group": self.group,
            "ip": self.ip,
            "port": self.port,
            "healthy": self.healthy,
            "source_name": self.source_name,
            "last_check": self.last_check,
        }


class Registry:
    def __init__(self, provider=None):
        self.provider = provider or build_provider()
        self.sync_interval = int(os.environ.get("SYNC_INTERVAL", 60))
        self.health_interval = int(os.environ.get("HEALTH_INTERVAL", 30))
        self.health_timeout = float(os.environ.get("HEALTH_TIMEOUT", 3))
        self.any_keyword = os.environ.get("ANY_KEYWORD", "any")
        self.nodes = {}
        # Names the mesh reports as disconnected; skipped by the TCP check.
        self.offline = set()

    # --- discovery -------------------------------------------------------

    async def sync(self):
        """Rebuild the routing table from the discovery provider."""
        specs = await self.provider.fetch()

        discovered = {}
        offline = []
        for spec in specs:
            if not spec.name or not spec.ip:
                continue
            existing = self.nodes.get(spec.name)
            node = Node(spec.name, spec.ip, spec.port, spec.source_name)
            if existing and existing.ip == node.ip and existing.port == node.port:
                # Keep health state so traffic isn't paused on every sync.
                node.healthy = existing.healthy
                node.last_check = existing.last_check
            if spec.online is False:
                # The mesh says this peer is disconnected. Believe a False
                # immediately; a True still has to pass the TCP check.
                node.healthy = False
                offline.append(node.name)
            discovered[spec.name] = node

        added = set(discovered) - set(self.nodes)
        removed = set(self.nodes) - set(discovered)
        if added:
            print(f"[registry] nodes added: {sorted(added)}", flush=True)
        if removed:
            print(f"[registry] nodes removed: {sorted(removed)}", flush=True)

        self.nodes = discovered
        self.offline = set(offline)
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
        targets = [n for n in self.nodes.values() if n.name not in self.offline]
        await asyncio.gather(*(self._check(n) for n in targets))

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
