"""Bounded public-page fetch with redirect-by-redirect SSRF validation.

Each hop: validate the URL and resolve it to public IPs, then connect to exactly that checked IP (Host header
and TLS SNI carry the hostname) - so a DNS answer can't change between the check and the connection. The
site's robots.txt is respected and requests to one host are spaced out.
"""

from __future__ import annotations

import asyncio
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from jobpilot.servers.web.ssrf_guard import FetchRules, Resolver, UnsafeUrl, pinned, resolve, resolve_safe

USER_AGENT = "JobPilot/1.0 (+personal job search; contact: local-user)"
_robots: dict[str, tuple[float, RobotFileParser | None]] = {}
_last_hit: dict[str, float] = {}
ROBOTS_TTL = 3600

_TEXT_TYPES = ("text/", "application/json", "application/xhtml+xml")


class _TextExtractor(HTMLParser):
    """Collects a page's title and visible text from its HTML."""

    def __init__(self) -> None:
        """Start with no text and no open tags."""
        super().__init__()
        self.title_depth = 0
        self.title: list[str] = []
        self.parts: list[str] = []
        self.ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track entering script/style blocks and the title."""
        if tag in ("script", "style", "noscript", "svg"):
            self.ignored += 1
        if tag == "title":
            self.title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """Track leaving blocks; block-level tags end a line."""
        if tag in ("script", "style", "noscript", "svg") and self.ignored:
            self.ignored -= 1
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        if tag in ("p", "li", "div", "section", "h1", "h2", "h3", "br"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Keep visible text (and the title text)."""
        if self.ignored:
            return
        if self.title_depth:
            self.title.append(data)
        self.parts.append(data)


def html_text(value: str) -> tuple[str, str]:
    """Return a page's title and its visible text, one line per block."""
    parser = _TextExtractor()
    parser.feed(value)
    title = " ".join("".join(parser.title).split())
    text = "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    return title, text


class SafeFetcher:
    """Fetches public pages with SSRF checks on every redirect hop."""

    def __init__(
        self,
        *,
        rules: FetchRules | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve,
    ) -> None:
        """Create the fetcher.

        Args:
            rules: Fetch limits and the deny-list; loaded from config by
                default.
            transport: An httpx transport for tests.
            resolver: The DNS resolver (tests pass a fake).
        """
        self.rules = rules or FetchRules.load()
        self.transport = transport
        self.resolver = resolver

    def _request_args(self, url: str, ip: str) -> tuple[str, dict, dict]:
        """Return the IP-pinned URL, the Host header and the TLS SNI for a request."""
        host = urlsplit(url).hostname or ""
        extensions = {"sni_hostname": host} if url.startswith("https://") else {}
        return pinned(url, ip), {"Host": host}, extensions

    async def _polite(self, host: str) -> None:
        """Wait if this host was hit less than the minimum interval ago."""
        wait = self.rules.min_interval_seconds - (time.monotonic() - _last_hit.get(host, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        _last_hit[host] = time.monotonic()

    async def _robots_allowed(self, client: httpx.AsyncClient, url: str, ip: str) -> bool:
        """Return True if the site's robots.txt allows fetching ``url``.

        robots.txt is read through the same pinned IP and cached for an
        hour per origin. A missing or unreadable file allows everything.
        """
        if not self.rules.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = _robots.get(origin)
        if cached is None or time.monotonic() - cached[0] > ROBOTS_TTL:
            parser: RobotFileParser | None = None
            target, headers, extensions = self._request_args(f"{origin}/robots.txt", ip)
            try:
                resp = await client.get(target, headers=headers, extensions=extensions)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("text/plain"):
                    parser = RobotFileParser()
                    parser.parse(resp.text[:100_000].splitlines())
            except httpx.HTTPError:
                parser = None  # no readable robots.txt: allowed
            cached = (time.monotonic(), parser)
            _robots[origin] = cached
        parser = cached[1]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    async def fetch(self, url: str) -> dict[str, str | int]:
        """Fetch one public page, following at most three redirects.

        Args:
            url: The page to fetch.

        Returns:
            The final URL, the page title, its text and the HTTP status.

        Raises:
            UnsafeUrl: A hop is blocked by the guard or robots.txt.
            ValueError: Too many redirects, too large, or not text.
        """
        current = url
        async with httpx.AsyncClient(
            timeout=self.rules.timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for _ in range(4):
                # Re-check every hop: a redirect may point at a private
                # address.
                safe, ip = await resolve_safe(current, self.rules, self.resolver)
                if not await self._robots_allowed(client, safe, ip):
                    raise UnsafeUrl("the site's robots.txt disallows this page")
                await self._polite(urlsplit(safe).hostname or "")
                target, headers, extensions = self._request_args(safe, ip)
                async with client.stream("GET", target, headers=headers, extensions=extensions) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise httpx.HTTPStatusError(
                                "redirect without location", request=response.request, response=response
                            )
                        current = urljoin(safe, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith(_TEXT_TYPES):
                        raise ValueError(f"unsupported content type: {content_type or 'unknown'}")
                    body = bytearray()
                    # Stream and stop early, so a huge page can't fill
                    # memory.
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.rules.max_response_bytes:
                            raise ValueError("response exceeds configured byte limit")
                    text = body.decode(response.encoding or "utf-8", errors="replace")
                    title, clean = html_text(text) if "html" in content_type else ("", text)
                    return {
                        "url": safe,
                        "title": title[:500],
                        "text": clean[: self.rules.max_response_bytes],
                        "status": response.status_code,
                    }
        raise ValueError("too many redirects")
