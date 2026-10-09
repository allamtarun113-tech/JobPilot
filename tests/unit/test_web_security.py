"""Web fetch safety: SSRF checks, redirects, content and byte caps, and the search client."""

import httpx
import pytest

from jobpilot.servers.web.fetch import SafeFetcher
from jobpilot.servers.web.search import OpenAIWebSearch
from jobpilot.servers.web.ssrf_guard import FetchRules, UnsafeUrl, public_ip, validate_url

RULES = FetchRules(("linkedin.com", "naukri.com", "indeed.com"), 1000, 2)


async def public_resolver(host: str, port: int) -> list[str]:
    """Resolve every host to one public address."""
    return ["93.184.216.34"]


async def private_resolver(host: str, port: int) -> list[str]:
    """Resolve every host to a private address."""
    return ["10.0.0.4"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
        "https://example.com:8443/",
        "https://linkedin.com/jobs/view/1",
        "https://jobs.naukri.com/example",
    ],
)
async def test_ssrf_and_deny_list_urls_are_refused(url: str) -> None:
    """Private, odd-port, credential and deny-listed URLs are refused."""
    with pytest.raises(UnsafeUrl):
        await validate_url(url, RULES, public_resolver)


async def test_dns_with_any_private_answer_is_refused() -> None:
    """A host resolving to a private address is refused."""
    assert public_ip("8.8.8.8") and not public_ip("100.64.0.1")
    with pytest.raises(UnsafeUrl):
        await validate_url("https://careers.example.com/job/1", RULES, private_resolver)


async def test_fetch_revalidates_redirect_and_extracts_text() -> None:
    """Redirects are re-checked; pages yield title and visible text."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve a page, or a redirect to a private address."""
        if request.url.path == "/redirect-private":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=(
                "<title>Data Analyst - Acme</title><script>ignore()</script>"
                "<h1>Data Analyst</h1><p>SQL role</p>"
            ),
        )

    fetcher = SafeFetcher(rules=RULES, transport=httpx.MockTransport(handler), resolver=public_resolver)
    page = await fetcher.fetch("https://careers.example.com/job/1#fragment")
    assert page["title"] == "Data Analyst - Acme"
    assert "SQL role" in str(page["text"]) and "ignore" not in str(page["text"])
    with pytest.raises(UnsafeUrl):
        await fetcher.fetch("https://careers.example.com/redirect-private")


async def test_fetch_rejects_binary_and_oversized_pages() -> None:
    """Binary and oversized pages are refused."""
    binary = SafeFetcher(
        rules=RULES,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"pdf")
        ),
        resolver=public_resolver,
    )
    with pytest.raises(ValueError, match="content type"):
        await binary.fetch("https://careers.example.com/file")
    large = SafeFetcher(
        rules=RULES,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "text/plain"}, content=b"x" * 1001)
        ),
        resolver=public_resolver,
    )
    with pytest.raises(ValueError, match="byte limit"):
        await large.fetch("https://careers.example.com/large")


async def test_openai_web_search_contract_keeps_only_cited_or_sourced_urls() -> None:
    """Only URLs the search tool returned are kept, without tracking."""
    import json

    seen: list[httpx.Request] = []
    body = {
        "output": [
            {
                "type": "web_search_call",
                "action": {
                    "sources": [
                        {"url": "https://jobs.lever.co/acme/1?utm_source=openai"},
                        {"url": "https://acme.example/careers"},
                    ]
                },
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Acme - AI Engineer: build LLM features.\nMade-up line without a citation.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://jobs.lever.co/acme/1?utm_source=openai",
                                "title": "Acme - AI Engineer",
                                "start_index": 0,
                                "end_index": 37,
                            }
                        ],
                    }
                ],
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the request and answer with the recorded body."""
        seen.append(request)
        return httpx.Response(200, json=body)

    searcher = OpenAIWebSearch("sk-x", "gpt-5-nano", httpx.MockTransport(handler), effort="minimal")
    results = await searcher.search("site:jobs.lever.co AI Engineer India", 99)
    assert results == [
        {
            "title": "Acme - AI Engineer",
            "url": "https://jobs.lever.co/acme/1",
            "snippet": "Acme - AI Engineer: build LLM features.",
        },
        {"title": "", "url": "https://acme.example/careers", "snippet": ""},
    ]
    sent = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == "Bearer sk-x"
    assert sent["tools"][0]["type"] == "web_search" and sent["model"] == "gpt-5-nano"
    assert sent["reasoning"] == {"effort": "low"}  # web search refuses "minimal"
    assert "site:jobs.lever.co AI Engineer India" in sent["input"] and "up to 20" in sent["input"]


async def test_openai_web_search_failure_is_an_error() -> None:
    """A failed search call raises SearchError."""
    from jobpilot.servers.web.search import SearchError

    search = OpenAIWebSearch("sk-x", "m", httpx.MockTransport(lambda r: httpx.Response(429, json={})))
    with pytest.raises(SearchError):
        await search.search("q")
