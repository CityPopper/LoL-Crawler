"""IMP-062: WaterfallCoordinator passes redis_only=True on the hot-path exists() check."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from lol_pipeline.sources.base import MATCH, FetchContext, WaterfallResult
from lol_pipeline.sources.coordinator import WaterfallCoordinator


@pytest.mark.asyncio
async def test_coordinator_exists_passes_redis_only_true():
    """The coordinator's fetch_match calls raw_store.exists(match_id, redis_only=True)."""
    raw_store = AsyncMock()
    raw_store.exists = AsyncMock(return_value=True)

    registry = MagicMock()
    blob_store = None
    extractors: list = []

    coordinator = WaterfallCoordinator(registry, blob_store, raw_store, extractors)
    context = FetchContext(match_id="NA1_12345", puuid="test-puuid", region="na1")
    result = await coordinator.fetch_match(context, MATCH)

    # Verify redis_only=True is passed
    raw_store.exists.assert_called_once_with("NA1_12345", redis_only=True)
    assert result.status == "cached"


@pytest.mark.asyncio
async def test_coordinator_exists_false_continues_to_sources():
    """When redis_only exists() returns False, coordinator proceeds to blob/source checks."""
    raw_store = AsyncMock()
    raw_store.exists = AsyncMock(return_value=False)
    raw_store.set = AsyncMock()

    registry = MagicMock()
    registry.source_names = ["riot"]
    registry.sources_for = MagicMock(return_value=[])

    blob_store = None
    extractors: list = []

    coordinator = WaterfallCoordinator(registry, blob_store, raw_store, extractors)
    context = FetchContext(match_id="NA1_99999", puuid="test-puuid", region="na1")
    result = await coordinator.fetch_match(context, MATCH)

    raw_store.exists.assert_called_once_with("NA1_99999", redis_only=True)
    # All sources exhausted (none registered), so status is "all_exhausted"
    assert result.status == "all_exhausted"
