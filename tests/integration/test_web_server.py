"""Web MCP subprocess authenticates the worker and applies fetch policy server-side."""

from jobpilot.integrations.mcp_client import MCPConnection, open_client


async def test_web_mcp_refuses_missing_search_key_and_blocked_fetch() -> None:
    """Search needs an API key and deny-listed pages are never fetched."""
    async with open_client("web", "worker", extra_env={"OPENAI_API_KEY": ""}) as client:
        conn = MCPConnection("web", "worker", client)
        search = await conn.call("search", {"query": "data analyst jobs"})
        assert not search["ok"] and "OPENAI_API_KEY" in search["error"]
        fetched = await conn.call("fetch", {"url": "https://www.linkedin.com/jobs/view/123"})
        assert not fetched["ok"] and "denied" in fetched["error"]
