"""Telegram Bot API calls behind the MCP tools. Dry-run (no bot token) appends to a JSONL file instead."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from jobpilot.core.config import REPO_ROOT, get_config, get_settings

API = "https://api.telegram.org"
# Dry-run writes come from several threads; keep lines whole.
_lock = threading.Lock()


def _dry_run_path() -> Path:
    """Return the JSONL file dry-run calls are written to."""
    return REPO_ROOT / get_config().telegram.dry_run_log


def _dry_run(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Log a call to the dry-run file instead of sending it.

    Returns:
        A success result with a made-up, increasing message id.
    """
    path = _dry_run_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        n = sum(1 for _ in path.open()) if path.exists() else 0
        message_id = params.get("message_id") or 1000 + n
        with path.open("a") as f:
            f.write(
                json.dumps({"ts": time.time(), "method": method, "message_id": message_id, **params}) + "\n"
            )
    return {"ok": True, "message_id": message_id, "dry_run": True}


async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Returns {"ok": bool, "message_id"?, "error"?, "retryable"?, "retry_after"?}."""
    settings = get_settings()
    if settings.telegram_dry_run:
        return _dry_run(method, params)
    token = settings.telegram_bot_token.get_secret_value()  # type: ignore[union-attr]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{API}/bot{token}/{method}", json=params)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"network: {type(exc).__name__}", "retryable": True}
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if body.get("ok"):
        result = body.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        return {"ok": True, "message_id": message_id}
    description = str(body.get("description", resp.text))[:300]
    # Editing to the same text is a no-op, not an error.
    if method == "editMessageText" and "message is not modified" in description:
        return {"ok": True, "message_id": params.get("message_id")}
    if resp.status_code == 429:
        retry_after = (body.get("parameters") or {}).get("retry_after", 5)
        return {"ok": False, "error": description, "retryable": True, "retry_after": retry_after}
    return {"ok": False, "error": description, "retryable": resp.status_code >= 500}


async def call_document(
    chat_id: int,
    data: bytes,
    filename: str,
    caption: str | None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upload a PDF to the chat with sendDocument."""
    return await call_upload(
        "sendDocument", "document", chat_id, data, filename, "application/pdf", caption, reply_markup
    )


async def call_upload(
    method: str,
    field: str,
    chat_id: int,
    data: bytes,
    filename: str,
    content_type: str,
    caption: str | None,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Upload a file (a resume PDF with sendDocument, its preview with sendPhoto)."""
    settings = get_settings()
    if settings.telegram_dry_run:
        return _dry_run(
            method,
            {
                "chat_id": chat_id,
                field: filename,
                "caption": caption,
                "reply_markup": reply_markup,
                "bytes": len(data),
            },
        )
    token = settings.telegram_bot_token.get_secret_value()  # type: ignore[union-attr]
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{API}/bot{token}/{method}",
                data={
                    "chat_id": str(chat_id),
                    "caption": caption or "",
                    "reply_markup": json.dumps(reply_markup) if reply_markup else "",
                },
                files={field: (filename, data, content_type)},
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"network: {type(exc).__name__}", "retryable": True}
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if body.get("ok"):
        return {"ok": True, "message_id": body["result"]["message_id"]}
    return {
        "ok": False,
        "error": str(body.get("description", resp.text))[:300],
        "retryable": resp.status_code >= 500,
    }
