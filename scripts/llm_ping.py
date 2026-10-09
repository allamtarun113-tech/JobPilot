"""One trivial structured call through the LLM client (needs OPENAI_API_KEY).

Usage: uv run python scripts/llm_ping.py
"""

from __future__ import annotations

import asyncio

from jobpilot.db.session import dispose_engine
from jobpilot.llm import prompts
from jobpilot.llm.client import LLMClient
from jobpilot.models.health import PingResult
from jobpilot.monitoring.tracing import setup_tracing, shutdown_tracing


async def main() -> None:
    """Make one structured model call and print the result, tokens and cost."""
    setup_tracing("script")
    result = await LLMClient().structured(
        "health_ping", prompts.render("system/ping", word="pilot"), PingResult
    )
    print(
        result.output,
        f"model={result.model} tokens={result.input_tokens}+{result.output_tokens} "
        f"cost=${result.cost_usd:.6f}",
    )
    await dispose_engine()
    shutdown_tracing()


if __name__ == "__main__":
    asyncio.run(main())
