"""Web search through OpenAI's built-in `web_search` tool (Responses API).

Only URLs the search tool itself returned are kept (its citations first, then its other sources); words the
model writes are used as snippets, never as links. Search results are leads: this module never fetches their
pages (fetch.py does, with the SSRF guard). `site:` in a query is honoured by the search itself.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

ENDPOINT = "https://api.openai.com/v1/responses"
# A fixed instruction, not an app prompt: it only asks the search tool for a cited result list.
INSTRUCTION = (
    "Search the web for: {query}\n"
    "List up to {count} distinct results, one line each: the page title, then one sentence on what the page "
    "says. Cite each line with its source. Do not add anything else."
)


class SearchError(RuntimeError):
    """The OpenAI web search call failed."""

    pass


def _check_query(query: str) -> str:
    """Collapse whitespace and require 1-400 characters."""
    query = " ".join(query.split())
    if not query or len(query) > 400:
        raise ValueError("search query must contain 1-400 characters")
    return query


def clean_url(url: str) -> str:
    """Drop the `utm_*` tracking OpenAI appends (`?utm_source=openai`) so seen URLs and dedup match."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _line_around(text: str, start: int, end: int) -> str:
    """Return the whole line of ``text`` around a citation span."""
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    return text[left : right if right != -1 else len(text)].strip(" -*\t")


def parse(body: dict[str, Any], count: int) -> list[dict[str, str]]:
    """Results from a Responses API body: cited URLs (with their title and line), then other sources."""
    results: dict[str, dict[str, str]] = {}
    sources: list[str] = []
    for item in body.get("output") or []:
        if item.get("type") == "web_search_call":
            # Sources the search tool consulted (kept after the cited
            # ones).
            sources += [str(s.get("url") or "") for s in (item.get("action") or {}).get("sources") or []]
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            text = str(part.get("text") or "")
            for ann in part.get("annotations") or []:
                if ann.get("type") != "url_citation" or not ann.get("url"):
                    continue
                url = clean_url(str(ann["url"]))
                snippet = _line_around(text, int(ann.get("start_index") or 0), int(ann.get("end_index") or 0))
                results.setdefault(
                    url,
                    {
                        "title": str(ann.get("title") or "")[:500],
                        "url": url[:4000],
                        "snippet": snippet[:4000],
                    },
                )
    for raw in sources:
        if raw and (url := clean_url(raw)) not in results:
            results[url] = {"title": "", "url": url[:4000], "snippet": ""}
    return list(results.values())[:count]


class OpenAIWebSearch:
    """Web search through OpenAI's ``web_search`` tool."""

    def __init__(
        self,
        api_key: str,
        model: str,
        transport: httpx.AsyncBaseTransport | None = None,
        effort: str | None = None,
    ) -> None:
        """Create the searcher.

        Args:
            api_key: The OpenAI API key.
            model: The model that runs the search tool.
            transport: An httpx transport for tests.
            effort: The reasoning effort (gpt-5 models). Web search refuses "minimal", so it becomes "low".
        """
        self.api_key = api_key
        self.model = model
        self.transport = transport
        self.effort = "low" if effort == "minimal" else effort

    async def search(self, query: str, count: int = 10) -> list[dict[str, str]]:
        """Search the web and return up to ``count`` results (max 20).

        Args:
            query: The search query (``site:`` filters work).
            count: How many results to return.

        Returns:
            Results with title, url and snippet.

        Raises:
            SearchError: The API call failed.
            ValueError: The query is empty or too long.
        """
        count = max(1, min(count, 20))
        body = {
            "model": self.model,
            "tools": [{"type": "web_search", "user_location": {"type": "approximate", "country": "IN"}}],
            # Force a real search instead of an answer from memory.
            "tool_choice": "required",
            "include": ["web_search_call.action.sources"],
            "store": False,
            "input": INSTRUCTION.format(query=_check_query(query), count=count),
        }
        if self.effort is not None:
            body["reasoning"] = {"effort": self.effort}
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
            response = await client.post(
                ENDPOINT, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        if response.status_code != 200:
            raise SearchError(f"OpenAI web search failed ({response.status_code})")
        return parse(response.json(), count)
