"""Discovery provider interface.

A provider's only job is to answer "which proxy nodes exist right now, and at
what address can I reach them?". Everything downstream — health checking,
username routing, failover — is provider-agnostic.
"""

import os


class NodeSpec:
    """One discovered node, before the registry attaches health state."""

    def __init__(self, name, ip, port=None, source_name=None, online=None):
        self.name = name
        self.ip = ip
        self.port = port
        self.source_name = source_name or name
        # True/False if the mesh reports reachability, None if it doesn't.
        # The TCP health check is still authoritative for True.
        self.online = online


class Provider:
    name = "base"

    def __init__(self):
        self.name_prefix = os.environ.get("NODE_NAME_PREFIX", "proxy-")
        self.node_port = int(os.environ.get("NODE_PORT", 8899))

    def strip_prefix(self, hostname):
        """Mesh hostname "proxy-de-1" -> node name "de-1"."""
        if self.name_prefix and hostname.startswith(self.name_prefix):
            hostname = hostname[len(self.name_prefix):]
        return hostname.strip().lower()

    @staticmethod
    def require(*names):
        missing = [n for n in names if not os.environ.get(n)]
        if missing:
            raise RuntimeError(f"missing required env vars: {', '.join(missing)}")

    async def fetch(self):
        """Return a list of NodeSpec. Raise to signal a transient failure."""
        raise NotImplementedError
