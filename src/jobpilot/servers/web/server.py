"""Read-only web MCP: OpenAI web search plus SSRF-safe public-page fetch."""

from __future__ import annotations

import logging
import sys

from mcp.server import MCPServer
from pydantic import BaseModel, Field

from jobpilot.core.config import get_config, get_settings
from jobpilot.servers.web import auth
from jobpilot.servers.web.fetch import SafeFetcher
from jobpilot.servers.web.search import OpenAIWebSearch, SearchError
from jobpilot.servers.web.ssrf_guard import UnsafeUrl

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
server = MCPServer("jobpilot-web")


class SearchResult(BaseModel):
    """What the search tool returns."""

    ok: bool
    results: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None


class FetchResult(BaseModel):
    """What the fetch tool returns."""

    ok: bool
    page: dict[str, str | int] | None = None
    error: str | None = None


@server.tool()
async def search(query: str, count: int = 10) -> SearchResult:
    """Search the web (OpenAI web search) and return result leads."""
    try:
        auth.check_scope("search")
        key = get_settings().openai_api_key
        if key is None or not key.get_secret_value():
            raise auth.Refused("OPENAI_API_KEY is not set (web search uses OpenAI)")
        cfg = get_config()
        model = cfg.model_for("web_search")
        searcher = OpenAIWebSearch(
            key.get_secret_value(), model, effort=cfg.llm.effort_for("web_search", model)
        )
        return SearchResult(ok=True, results=await searcher.search(query, count))
    except (auth.Refused, SearchError, ValueError) as exc:
        return SearchResult(ok=False, error=f"refused: {exc}" if isinstance(exc, auth.Refused) else str(exc))


@server.tool()
async def fetch(url: str) -> FetchResult:
    """Fetch one public page through the SSRF guard and the deny-list."""
    try:
        auth.check_scope("fetch")
        return FetchResult(ok=True, page=await SafeFetcher().fetch(url))
    except (auth.Refused, UnsafeUrl, ValueError) as exc:
        return FetchResult(ok=False, error=f"refused: {exc}")
    # Report only the error type: messages may echo page content.
    except Exception as exc:
        return FetchResult(ok=False, error=f"fetch failed: {type(exc).__name__}")


if __name__ == "__main__":
    server.run("stdio")
