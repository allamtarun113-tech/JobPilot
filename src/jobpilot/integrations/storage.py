"""Object store for PDFs and corpus files.

- LocalStore: a directory (development and tests).
- SupabaseStore: a private Supabase Storage bucket, so the Vercel dashboard can serve files the laptop made.
get_store() picks Supabase when SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from jobpilot.core.config import REPO_ROOT, get_config, get_settings


class NotFound(KeyError):
    """No object is stored under this key."""

    pass


class Store(Protocol):
    """An object store for PDFs, images and corpus files."""

    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        """Store ``data`` under ``key`` and return the key."""

    async def get(self, key: str) -> bytes:
        """Return the bytes stored under ``key``; raise NotFound if missing."""


def _check_key(key: str) -> str:
    """Return ``key`` if it is a safe relative path, else raise ValueError.

    Blocks absolute paths, ``..`` segments and backslashes, so a key
    can never escape the store.
    """
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise ValueError(f"bad storage key {key!r}")
    return key


class LocalStore:
    """Files in a local folder (development and tests)."""

    def __init__(self, root: Path | None = None) -> None:
        """Use ``root``, or the configured storage folder."""
        self.root = root or REPO_ROOT / get_config().resume.storage_dir

    def _path(self, key: str) -> Path:
        """Map a key to a file path inside the root (never outside it)."""
        path = (self.root / _check_key(key)).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"bad storage key {key!r}")
        return path

    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        """Write the bytes to the key's file, creating folders as needed."""
        path = self._path(key)

        def write() -> None:
            """Write the file (runs in a worker thread)."""
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(write)
        return key

    async def get(self, key: str) -> bytes:
        """Read the key's file; raise NotFound if missing."""
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise NotFound(key) from exc


class SupabaseStore:
    """Supabase Storage REST API with the service role key. The bucket must be private."""

    def __init__(
        self, url: str, service_key: str, bucket: str, transport: httpx.AsyncBaseTransport | None = None
    ):
        """Create the store.

        Args:
            url: The Supabase project URL.
            service_key: The service role key (server side only).
            bucket: The private bucket name.
            transport: An httpx transport for tests.
        """
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        """Return a new authenticated HTTP client."""
        return httpx.AsyncClient(headers=self.headers, timeout=30, transport=self.transport)

    def _object(self, key: str) -> str:
        """Return the REST URL of one object."""
        return f"{self.base}/object/{self.bucket}/{quote(_check_key(key))}"

    async def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> str:
        """Upload (or overwrite) one object and return its key."""
        async with self._client() as client:
            resp = await client.post(
                self._object(key), content=data, headers={"Content-Type": content_type, "x-upsert": "true"}
            )
        resp.raise_for_status()
        return key

    async def get(self, key: str) -> bytes:
        """Download one object; raise NotFound if it is missing."""
        async with self._client() as client:
            resp = await client.get(self._object(key))
        if resp.status_code in (400, 404):  # Supabase answers 400 "Object not found" for missing keys
            raise NotFound(key)
        resp.raise_for_status()
        return resp.content

    async def ensure_bucket(self) -> None:
        """Create the private bucket if it does not exist (one-time setup)."""
        async with self._client() as client:
            resp = await client.get(f"{self.base}/bucket/{self.bucket}")
            if resp.status_code == 200:
                if resp.json().get("public"):
                    raise RuntimeError(f"bucket {self.bucket!r} is public - make it private in Supabase")
                return
            created = await client.post(
                f"{self.base}/bucket", json={"id": self.bucket, "name": self.bucket, "public": False}
            )
            created.raise_for_status()


def get_store() -> Store:
    """Return Supabase Storage when configured, else the local folder.

    Raises:
        RuntimeError: On Vercel without Supabase settings (no local
            disk to fall back to).
    """
    s = get_settings()
    if s.supabase_url and s.supabase_service_role_key and s.supabase_service_role_key.get_secret_value():
        return SupabaseStore(
            s.supabase_url, s.supabase_service_role_key.get_secret_value(), s.supabase_storage_bucket
        )
    if s.serverless:
        raise RuntimeError(
            "on Vercel the object store must be Supabase (set SUPABASE_URL and the service key)"
        )
    return LocalStore()
