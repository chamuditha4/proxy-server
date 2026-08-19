"""Keeps the gateway's Cloudflare A record pointed at its current public IP."""

import asyncio
import os

import httpx

CF_API = "https://api.cloudflare.com/client/v4"


class CloudflareDNS:
    def __init__(self):
        self.token = os.environ.get("CF_API_TOKEN")
        self.zone_id = os.environ.get("CF_ZONE_ID")
        self.zone_name = os.environ.get("CF_ZONE_NAME")
        self.hostname = os.environ.get("PROXY_HOSTNAME")
        self.ttl = int(os.environ.get("DNS_TTL", 60))
        self.interval = int(os.environ.get("DNS_SYNC_INTERVAL", 300))
        self.static_ip = os.environ.get("PUBLIC_IP")
        self._last_ip = None

    @property
    def enabled(self):
        return bool(self.token and self.hostname and (self.zone_id or self.zone_name))

    async def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    async def _public_ip(self, client):
        if self.static_ip:
            return self.static_ip
        resp = await client.get("https://cloudflare.com/cdn-cgi/trace")
        resp.raise_for_status()
        for line in resp.text.splitlines():
            if line.startswith("ip="):
                return line[3:]
        raise RuntimeError("could not determine public IP")

    async def _resolve_zone(self, client, headers):
        if self.zone_id:
            return self.zone_id
        resp = await client.get(
            f"{CF_API}/zones", headers=headers, params={"name": self.zone_name}
        )
        resp.raise_for_status()
        results = resp.json().get("result", [])
        if not results:
            raise RuntimeError(f"zone not found: {self.zone_name}")
        self.zone_id = results[0]["id"]
        return self.zone_id

    async def sync_once(self):
        async with httpx.AsyncClient(timeout=15) as client:
            headers = await self._headers()
            ip = await self._public_ip(client)
            if ip == self._last_ip:
                return ip

            zone_id = await self._resolve_zone(client, headers)
            resp = await client.get(
                f"{CF_API}/zones/{zone_id}/dns_records",
                headers=headers,
                params={"type": "A", "name": self.hostname},
            )
            resp.raise_for_status()
            records = resp.json().get("result", [])

            body = {
                "type": "A",
                "name": self.hostname,
                "content": ip,
                "ttl": self.ttl,
                # A forward proxy speaks raw HTTP CONNECT, which the Cloudflare
                # edge will not pass through. This record must stay DNS-only.
                "proxied": False,
            }

            if records:
                record = records[0]
                if record["content"] == ip and record.get("proxied") is False:
                    self._last_ip = ip
                    return ip
                resp = await client.put(
                    f"{CF_API}/zones/{zone_id}/dns_records/{record['id']}",
                    headers=headers,
                    json=body,
                )
            else:
                resp = await client.post(
                    f"{CF_API}/zones/{zone_id}/dns_records", headers=headers, json=body
                )
            resp.raise_for_status()

            print(f"[dns] {self.hostname} -> {ip}", flush=True)
            self._last_ip = ip
            return ip

    async def run(self):
        if not self.enabled:
            print(
                "[dns] Cloudflare sync disabled "
                "(set CF_API_TOKEN, PROXY_HOSTNAME, CF_ZONE_ID or CF_ZONE_NAME)",
                flush=True,
            )
            return
        while True:
            try:
                await self.sync_once()
            except Exception as exc:
                print(f"[dns] sync failed: {exc}", flush=True)
            await asyncio.sleep(self.interval)
