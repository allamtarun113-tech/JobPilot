"""Qdrant Cloud needs payload indexes on the fields we filter by."""

from unittest.mock import AsyncMock

from qdrant_client import models

from jobpilot.integrations.vectors import PROFILE_ITEMS, VectorStore


async def test_existing_profile_collection_gets_filter_index() -> None:
    """An existing collection gets its index once and isn't recreated."""
    client = AsyncMock()
    client.collection_exists.return_value = True
    store = VectorStore(client, 384)
    await store.ensure(PROFILE_ITEMS)
    await store.ensure(PROFILE_ITEMS)
    client.create_collection.assert_not_called()
    client.create_payload_index.assert_awaited_once_with(
        PROFILE_ITEMS, field_name="kind", field_schema=models.PayloadSchemaType.KEYWORD, wait=True
    )
