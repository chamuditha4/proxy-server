"""Pluggable node discovery. Select with DISCOVERY_BACKEND."""

import os

from .base import NodeSpec, Provider  # noqa: F401


def build_provider():
    backend = os.environ.get("DISCOVERY_BACKEND", "tailscale").strip().lower()

    if backend == "tailscale":
        from .tailscale import TailscaleProvider
        return TailscaleProvider()
    if backend == "netbird":
        from .netbird import NetbirdProvider
        return NetbirdProvider()
    if backend in ("cloudflare", "cloudflare_tunnel"):
        from .cloudflare import CloudflareTunnelProvider
        return CloudflareTunnelProvider()
    if backend == "static":
        from .static import StaticProvider
        return StaticProvider()

    raise RuntimeError(
        f"unknown DISCOVERY_BACKEND '{backend}' "
        "(expected: tailscale, netbird, cloudflare, static)"
    )
