"""URL validation for public web fetches: deny-listed hosts and all non-public destinations."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from jobpilot.core.config import load_yaml

Resolver = Callable[[str, int], Awaitable[list[str]]]


class UnsafeUrl(ValueError):
    """The URL is not allowed: bad form, denied host or non-public address."""

    pass


@dataclass(frozen=True)
class FetchRules:
    """Fetch limits and the deny-list from ``fetch_rules.yaml``."""

    deny_domains: tuple[str, ...]
    max_response_bytes: int
    timeout_seconds: float
    min_interval_seconds: float = 0.0  # politeness: at most one request per host per interval
    respect_robots: bool = True

    @classmethod
    def load(cls) -> FetchRules:
        """Load the rules from ``config/fetch_rules.yaml``."""
        data = load_yaml("fetch_rules.yaml")
        return cls(
            tuple(str(x).lower().strip(".") for x in data["deny_domains"]),
            int(data["max_response_bytes"]),
            float(data["timeout_seconds"]),
            float(data.get("min_interval_seconds", 1.0)),
            bool(data.get("respect_robots", True)),
        )


def denied_host(host: str, rules: FetchRules) -> bool:
    """Return True if the host is a deny-listed domain or one of its subdomains."""
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in rules.deny_domains)


def public_ip(value: str) -> bool:
    """Return True if the address is public (not private, loopback, link-local...)."""
    ip = ipaddress.ip_address(value)
    # ::ffff:10.0.0.1 is really 10.0.0.1: judge the IPv4 address.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


async def resolve(host: str, port: int) -> list[str]:
    """Resolve a host name to its distinct IP addresses."""
    loop = asyncio.get_running_loop()
    rows = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(row[4][0]) for row in rows))


async def validate_url(url: str, rules: FetchRules | None = None, resolver: Resolver = resolve) -> str:
    """Return the normalized URL if it is safe to fetch, else raise UnsafeUrl."""
    return (await resolve_safe(url, rules, resolver))[0]


async def resolve_safe(
    url: str, rules: FetchRules | None = None, resolver: Resolver = resolve
) -> tuple[str, str]:
    """(normalized URL, the checked public IP to connect to). Connecting to exactly this IP - not resolving
    the name again - is what closes the DNS-rebinding gap between the check and the connection."""
    rules = rules or FetchRules.load()
    try:
        parts = urlsplit(url)
        # IDNA-encode so look-alike Unicode host names are compared as
        # ASCII.
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except (UnicodeError, ValueError) as exc:
        raise UnsafeUrl("invalid URL") from exc
    if parts.scheme not in ("http", "https") or not host:
        raise UnsafeUrl("only absolute http/https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeUrl("URL credentials are not allowed")
    expected_port = 443 if parts.scheme == "https" else 80
    if port != expected_port:
        raise UnsafeUrl("only the scheme's standard port is allowed")
    if denied_host(host, rules):
        raise UnsafeUrl(f"fetch denied for {host}")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addresses = [str(literal)]
    except ValueError:
        addresses = await resolver(host, port)
    # Every address must be public: one private answer blocks the URL.
    if not addresses or any(not public_ip(ip) for ip in addresses):
        raise UnsafeUrl("destination is not a public Internet address")
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, "")), addresses[0]


def pinned(url: str, ip: str) -> str:
    """The same URL addressed to the checked IP (the Host header and TLS SNI keep the real hostname)."""
    parts = urlsplit(url)
    netloc = f"[{ip}]" if ":" in ip else ip
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
