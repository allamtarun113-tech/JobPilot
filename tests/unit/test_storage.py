"""Object stores: local directory and Supabase Storage (REST, mocked)."""

from pathlib import Path

import httpx
import pytest

from jobpilot.integrations.storage import LocalStore, NotFound, SupabaseStore


async def test_local_store_round_trip_and_key_safety(tmp_path: Path) -> None:
    """Local files round-trip and unsafe keys are refused."""
    store = LocalStore(tmp_path)
    await store.put("resumes/base/1.pdf", b"%PDF")
    assert await store.get("resumes/base/1.pdf") == b"%PDF"
    with pytest.raises(NotFound):
        await store.get("resumes/base/missing.pdf")
    for bad in ("../x.pdf", "/etc/passwd", "a/../../b", "a\\b"):
        with pytest.raises(ValueError):
            await store.put(bad, b"x")


async def test_supabase_store_uses_private_bucket_api() -> None:
    """Supabase uploads, downloads and creates a private bucket."""
    seen: list[httpx.Request] = []
    objects: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """Act like Supabase Storage's REST API."""
        seen.append(request)
        path = request.url.path
        if request.method == "POST" and path.startswith("/storage/v1/object/"):
            objects[path] = request.content
            return httpx.Response(200, json={"Key": path})
        if request.method == "GET" and path.startswith("/storage/v1/object/"):
            if path in objects:
                return httpx.Response(200, content=objects[path])
            return httpx.Response(400, json={"error": "Object not found"})
        if path == "/storage/v1/bucket/jobpilot":
            return httpx.Response(404, json={"error": "not found"})
        if path == "/storage/v1/bucket":
            return httpx.Response(200, json={"name": "jobpilot"})
        return httpx.Response(500)

    store = SupabaseStore(
        "https://abc.supabase.co", "service-key", "jobpilot", transport=httpx.MockTransport(handler)
    )
    await store.put("resumes/base/1-v1.pdf", b"%PDF-1.7")
    assert await store.get("resumes/base/1-v1.pdf") == b"%PDF-1.7"
    with pytest.raises(NotFound):
        await store.get("resumes/base/none.pdf")
    upload = seen[0]
    assert upload.url.path == "/storage/v1/object/jobpilot/resumes/base/1-v1.pdf"
    assert upload.headers["authorization"] == "Bearer service-key" and upload.headers["x-upsert"] == "true"
    await store.ensure_bucket()
    assert seen[-1].method == "POST" and b'"public":false' in seen[-1].content.replace(b" ", b"")
