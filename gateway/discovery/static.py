"""Discovery from a hand-maintained list.

For plain WireGuard, Nebula, a cloud VPC, or anything else without a discovery
API. Nodes are still health checked, so a dead node still leaves rotation --
you just add and remove entries yourself.

    STATIC_NODES=de-1=10.8.0.1,us-1=10.8.0.2:8899

or point STATIC_NODES_FILE at a JSON file, re-read on every sync:

    { "de-1": "10.8.0.1", "us-1": "10.8.0.2:8899" }
"""

import json
import os

from .base import NodeSpec, Provider


class StaticProvider(Provider):
    name = "static"

    def __init__(self):
        super().__init__()
        self.inline = os.environ.get("STATIC_NODES", "")
        self.path = os.environ.get("STATIC_NODES_FILE")
        if not self.inline and not self.path:
            raise RuntimeError("set STATIC_NODES or STATIC_NODES_FILE")

    def _spec(self, name, address):
        host, _, port = str(address).partition(":")
        return NodeSpec(
            name=self.strip_prefix(name),
            ip=host.strip(),
            port=int(port) if port else self.node_port,
            source_name=name,
        )

    async def fetch(self):
        entries = {}

        if self.path:
            with open(self.path) as handle:
                loaded = json.load(handle)
            if isinstance(loaded, list):
                loaded = {n["name"]: n["address"] for n in loaded}
            entries.update(loaded)

        for item in self.inline.split(","):
            item = item.strip()
            if not item:
                continue
            name, sep, address = item.partition("=")
            if not sep:
                raise RuntimeError(f"malformed STATIC_NODES entry: {item!r}")
            entries[name.strip()] = address.strip()

        return [self._spec(n, a) for n, a in entries.items()]
