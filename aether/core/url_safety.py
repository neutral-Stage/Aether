"""Outbound URL safety checks — SSRF mitigation for MCP SSE and similar clients."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud metadata and link-local endpoints commonly targeted in SSRF.
_BLOCKED_LITERAL_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})


class URLSafetyError(ValueError):
    """Raised when an outbound URL targets a disallowed host or network."""


def validate_outbound_url(url: str, *, allow_private: bool = False) -> None:
    """Reject URLs that may reach private, link-local, or metadata networks.

    When ``allow_private`` is False (default), blocks loopback, RFC1918,
    link-local, and unique-local addresses after DNS resolution. Set
    ``mcp.allow_private_urls: true`` in config for trusted local MCP servers.
    """
    if not url or not str(url).strip():
        raise URLSafetyError("URL is required")

    parsed = urlparse(str(url).strip())
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise URLSafetyError(f"URL scheme not allowed: {scheme or '(none)'}")

    host = parsed.hostname
    if not host:
        raise URLSafetyError("URL host is required")

    host_lower = host.lower().rstrip(".")
    if host_lower in _BLOCKED_LITERAL_HOSTS:
        raise URLSafetyError(f"Blocked host: {host}")

    if _is_blocked_ip(host, allow_private=allow_private):
        raise URLSafetyError(f"Blocked IP address: {host}")

    if allow_private:
        return

    try:
        addrinfos = socket.getaddrinfo(
            host,
            parsed.port or (443 if scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # Hostname did not resolve; defer to the HTTP client for connection errors.
        return

    seen: set[str] = set()
    for info in addrinfos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_str = sockaddr[0]
        if ip_str in seen:
            continue
        seen.add(ip_str)
        if _is_blocked_ip(ip_str, allow_private=False):
            raise URLSafetyError(
                f"Host {host!r} resolves to blocked address {ip_str}"
            )


def _is_blocked_ip(host_or_ip: str, *, allow_private: bool) -> bool:
    if allow_private:
        return False
    try:
        addr = ipaddress.ip_address(host_or_ip)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if addr.is_private:
        return True
    if addr.is_link_local:
        return True
    if addr.is_reserved:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.is_site_local:
        return True
    return False
