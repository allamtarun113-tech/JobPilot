"""Re-check every learning-resource link (HEAD, then GET) and write var/resource_link_status.json.

Resources whose link stops answering 200/401/403 are then hidden from cards and the dashboard. Data/ is not
modified. Run: make check-links
"""

from __future__ import annotations

import asyncio
import json

import httpx

from jobpilot.knowledge.learning import LINK_STATUS_OVERRIDES, all_resources


async def status(client: httpx.AsyncClient, url: str) -> int:
    """Return a link's HTTP status (HEAD, falling back to GET); 0 when unreachable."""
    try:
        resp = await client.head(url)
        if resp.status_code in (405, 501):
            resp = await client.get(url)
        return resp.status_code
    except httpx.HTTPError:
        return 0


async def main() -> None:
    """Check every resource link (8 at a time) and save the statuses."""
    resources = all_resources()
    sem = asyncio.Semaphore(8)
    results: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:

        async def one(rid: str, url: str) -> None:
            """Check one resource's link within the concurrency limit."""
            async with sem:
                results[rid] = await status(client, url)

        await asyncio.gather(*(one(rid, r["url"]) for rid, r in resources.items()))
    LINK_STATUS_OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    LINK_STATUS_OVERRIDES.write_text(json.dumps(results, indent=1, sort_keys=True))
    bad = sum(1 for v in results.values() if v not in (200, 401, 403))
    print(f"checked {len(results)} links; {bad} will be hidden")


if __name__ == "__main__":
    asyncio.run(main())
