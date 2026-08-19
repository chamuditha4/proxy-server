"""Single-endpoint proxy gateway.

Clients always connect to one host:port. The proxy username selects which
backend node the traffic exits from:

    curl -x http://de-1:PASS@proxy.example.com:8899 https://ifconfig.me
    curl -x http://de:PASS@proxy.example.com:8899   https://ifconfig.me   # any de-* node
    curl -x http://any:PASS@proxy.example.com:8899  https://ifconfig.me   # any healthy node

Backend nodes are discovered from the Tailscale API and are only reachable
over the tailnet, so they expose no public ports.
"""

import asyncio
import base64
import hmac
import json
import os
import signal
import sys

from dotenv import load_dotenv

from dns_sync import CloudflareDNS
from registry import Registry

load_dotenv()

MAX_HEAD = 64 * 1024
BUFFER = 64 * 1024
REALM = os.environ.get("PROXY_REALM", "proxy")


def _response(status, reason, extra_headers=b"", body=b""):
    headers = (
        f"HTTP/1.1 {status} {reason}\r\n".encode()
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n"
        + extra_headers
        + b"\r\n"
    )
    return headers + body


AUTH_REQUIRED = _response(
    407,
    "Proxy Authentication Required",
    f'Proxy-Authenticate: Basic realm="{REALM}"\r\n'.encode(),
    b"Proxy credentials required. Username selects the exit node.\n",
)


class Gateway:
    def __init__(self, registry):
        self.registry = registry
        self.host = os.environ.get("GATEWAY_HOST", "0.0.0.0")
        self.port = int(os.environ.get("GATEWAY_PORT", 8899))
        self.password = os.environ.get("GATEWAY_PASS")
        # Optional credentials for nodes that run with their own basic auth.
        # See README: this only rewrites the first request on a connection.
        self.node_user = os.environ.get("NODE_PROXY_USER")
        self.node_pass = os.environ.get("NODE_PROXY_PASS")

        if not self.password:
            sys.exit("GATEWAY_PASS must be set")

    # --- request parsing -------------------------------------------------

    @staticmethod
    async def _read_head(reader):
        head = b""
        while b"\r\n\r\n" not in head:
            if len(head) > MAX_HEAD:
                raise ValueError("request head too large")
            chunk = await reader.read(BUFFER)
            if not chunk:
                raise ConnectionError("client closed before sending a full request")
            head += chunk
        return head

    @staticmethod
    def _parse(head):
        raw_head, _, remainder = head.partition(b"\r\n\r\n")
        lines = raw_head.split(b"\r\n")
        request_line = lines[0].decode("latin-1")
        headers = []
        for line in lines[1:]:
            name, _, value = line.partition(b":")
            headers.append((name.decode("latin-1").strip(), value.decode("latin-1").strip()))
        return request_line, headers, remainder

    def _credentials(self, headers):
        for name, value in headers:
            if name.lower() != "proxy-authorization":
                continue
            scheme, _, token = value.partition(" ")
            if scheme.lower() != "basic":
                return None
            try:
                decoded = base64.b64decode(token).decode("utf-8")
            except Exception:
                return None
            user, sep, password = decoded.partition(":")
            return (user, password) if sep else None
        return None

    def _rebuild(self, request_line, headers, remainder):
        """Forward the request upstream with our own proxy credentials."""
        out = [request_line.encode("latin-1")]
        for name, value in headers:
            if name.lower() == "proxy-authorization":
                continue
            out.append(f"{name}: {value}".encode("latin-1"))
        if self.node_user and self.node_pass:
            token = base64.b64encode(
                f"{self.node_user}:{self.node_pass}".encode()
            ).decode()
            out.append(f"Proxy-Authorization: Basic {token}".encode())
        return b"\r\n".join(out) + b"\r\n\r\n" + remainder

    # --- relay -----------------------------------------------------------

    @staticmethod
    async def _pipe(reader, writer):
        try:
            while True:
                chunk = await reader.read(BUFFER)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if not writer.is_closing():
                try:
                    writer.write_eof()
                except (OSError, RuntimeError):
                    pass

    async def handle(self, reader, writer):
        upstream_writer = None
        try:
            head = await self._read_head(reader)
            request_line, headers, remainder = self._parse(head)

            creds = self._credentials(headers)
            if not creds:
                writer.write(AUTH_REQUIRED)
                await writer.drain()
                return

            user, password = creds
            # Compare as bytes: compare_digest rejects non-ASCII str.
            if not hmac.compare_digest(password.encode(), self.password.encode()):
                writer.write(AUTH_REQUIRED)
                await writer.drain()
                return

            node = self.registry.select(user)
            if not node:
                writer.write(
                    _response(
                        502,
                        "Bad Gateway",
                        body=f"No healthy node matches '{user}'.\n".encode(),
                    )
                )
                await writer.drain()
                return

            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(node.ip, node.port), 10
                )
            except (OSError, asyncio.TimeoutError) as exc:
                node.healthy = False
                print(f"[gateway] {node.name} unreachable: {exc}", flush=True)
                writer.write(
                    _response(
                        502,
                        "Bad Gateway",
                        body=f"Node '{node.name}' is unreachable.\n".encode(),
                    )
                )
                await writer.drain()
                return

            upstream_writer.write(self._rebuild(request_line, headers, remainder))
            await upstream_writer.drain()

            await asyncio.gather(
                self._pipe(reader, upstream_writer),
                self._pipe(upstream_reader, writer),
            )
        except (ConnectionError, ValueError, asyncio.TimeoutError):
            pass
        except Exception as exc:
            print(f"[gateway] error: {exc}", flush=True)
        finally:
            for stream in (upstream_writer, writer):
                if stream is not None and not stream.is_closing():
                    stream.close()

    async def serve(self):
        server = await asyncio.start_server(self.handle, self.host, self.port)
        print(f"[gateway] listening on {self.host}:{self.port}", flush=True)
        async with server:
            await server.serve_forever()


class StatusServer:
    """Tiny JSON endpoint so you can see which nodes are live."""

    def __init__(self, registry):
        self.registry = registry
        self.host = os.environ.get("STATUS_HOST", "127.0.0.1")
        self.port = int(os.environ.get("STATUS_PORT", 9000))

    async def handle(self, reader, writer):
        try:
            await reader.read(BUFFER)
            body = json.dumps(
                {
                    "nodes": [n.as_dict() for n in self.registry.nodes.values()],
                    "healthy": sum(1 for n in self.registry.nodes.values() if n.healthy),
                    "total": len(self.registry.nodes),
                },
                indent=2,
            ).encode()
            writer.write(
                _response(200, "OK", b"Content-Type: application/json\r\n", body)
            )
            await writer.drain()
        except Exception:
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    async def serve(self):
        server = await asyncio.start_server(self.handle, self.host, self.port)
        print(f"[status] listening on {self.host}:{self.port}", flush=True)
        async with server:
            await server.serve_forever()


async def main():
    registry = Registry()
    gateway = Gateway(registry)
    status = StatusServer(registry)
    dns = CloudflareDNS()

    print(f"[registry] discovery backend: {registry.provider.name}", flush=True)

    # Populate the routing table before accepting traffic.
    try:
        await registry.sync()
        await registry.health_check()
    except Exception as exc:
        print(f"[registry] initial sync failed: {exc}", flush=True)

    await asyncio.gather(
        gateway.serve(), status.serve(), registry.run(), dns.run()
    )


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)
    try:
        loop.run_until_complete(main())
    except RuntimeError:
        pass
    finally:
        print("\n[gateway] shutting down", flush=True)
