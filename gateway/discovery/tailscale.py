"""Discovery via the Tailscale API. Nodes are devices carrying NODE_TAG."""

import os
import time

import httpx

from .base import NodeSpec, Provider

API = os.environ.get("TAILSCALE_API_URL", "https://api.tailscale.com/api/v2")


class TailscaleProvider(Provider):
    name = "tailscale"

    def __init__(self):
        super().__init__()
        self.tailnet = os.environ.get("TAILNET", "-")
        self.api_key = os.environ.get("TAILSCALE_API_KEY")
        self.oauth_id = os.environ.get("TAILSCALE_OAUTH_CLIENT_ID")
        self.oauth_secret = os.environ.get("TAILSCALE_OAUTH_CLIENT_SECRET")
        self.node_tag = os.environ.get("NODE_TAG", "tag:proxy-node")
        self._token = None
        self._token_expires = 0.0

        if not self.api_key and not (self.oauth_id and self.oauth_secret):
            raise RuntimeError(
                "set TAILSCALE_API_KEY, or TAILSCALE_OAUTH_CLIENT_ID + "
                "TAILSCALE_OAUTH_CLIENT_SECRET"
            )

    async def _headers(self, client):
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}

        if self._token and time.time() < self._token_expires - 60:
            return {"Authorization": f"Bearer {self._token}"}

        resp = await client.post(
            f"{API}/oauth/token",
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

    @staticmethod
    def _ipv4(device):
        for addr in device.get("addresses", []):
            if ":" not in addr:
                return addr
        return None

    async def fetch(self):
        async with httpx.AsyncClient(timeout=15) as client:
            headers = await self._headers(client)
            resp = await client.get(
                f"{API}/tailnet/{self.tailnet}/devices", headers=headers
            )
            resp.raise_for_status()
            devices = resp.json().get("devices", [])

        specs = []
        for device in devices:
            if self.node_tag not in (device.get("tags") or []):
                continue
            ip = self._ipv4(device)
            if not ip:
                continue
            hostname = device.get("hostname") or device.get("name", "").split(".")[0]
            specs.append(
                NodeSpec(
                    name=self.strip_prefix(hostname),
                    ip=ip,
                    port=self.node_port,
                    source_name=device.get("name", hostname),
                )
            )
        return specs
