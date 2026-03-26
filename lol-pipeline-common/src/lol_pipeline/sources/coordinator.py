"""WaterfallCoordinator -- tries sources in priority order until one succeeds.

The coordinator is fully source-agnostic: no source-specific conditionals,
no isinstance checks, no source name comparisons. All source-specific
behavior is encapsulated in Source and Extractor implementations.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from lol_pipeline.sources.base import (
    MATCH,
    ExtractionError,
    FetchResult,
    WaterfallResult,
)
from lol_pipeline.sources.blob_store import MAX_BLOB_SIZE_BYTES

if TYPE_CHECKING:
    from lol_pipeline.raw_store import RawStore
    from lol_pipeline.sources.base import (
        DataType,
        Extractor,
        FetchContext,
        FetchResponse,
    )
    from lol_pipeline.sources.blob_store import BlobStore
    from lol_pipeline.sources.registry import SourceRegistry

log = logging.getLogger(__name__)


class WaterfallCoordinator:
    """Try sources in priority order. Return result with data or failure info."""

    def __init__(
        self,
        registry: SourceRegistry,
        blob_store: BlobStore | None,
        raw_store: RawStore,
        extractors: list[Extractor],
    ) -> None:
        self._registry = registry
        self._blob_store = blob_store
        self._raw_store = raw_store
        self._extractors = extractors
        # Index extractors by (source_name, data_type) for O(1) lookup.
        self._extractor_index: dict[tuple[str, DataType], Extractor] = {
            (ext.source_name, dt): ext for ext in extractors for dt in ext.data_types
        }

    def _get_extractor(self, source_name: str, data_type: DataType) -> Extractor | None:
        return self._extractor_index.get((source_name, data_type))

    async def fetch_match(
        self,
        context: FetchContext,
        data_type: DataType = MATCH,
    ) -> WaterfallResult:
        """Try sources in priority order. Returns result with data or failure info."""
        # Step 1: Check raw_store -- if blob exists, skip fetch entirely.
        # redis_only=True avoids an expensive disk scan on every message;
        # a false-negative just means we re-fetch and re-store (safe).
        if await self._raw_store.exists(context.match_id, redis_only=True):
            return WaterfallResult(status="cached")

        # Step 2: Check blob_store across all sources.
        result = await self._try_blob_cache(context, data_type)
        if result is not None:
            return result

        # Step 3: Try each source in priority order.
        return await self._try_sources(context, data_type)

    async def _try_blob_cache(
        self,
        context: FetchContext,
        data_type: DataType,
    ) -> WaterfallResult | None:
        """Check blob_store for a cached blob. Returns result or None to continue."""
        if self._blob_store is None:
            return None

        found = await self._blob_store.find_any(context.match_id, self._registry.source_names)
        if found is None:
            return None

        source_name, blob_dict = found
        extractor = self._get_extractor(source_name, data_type)
        if extractor is None:
            log.warning(
                "no extractor for cached blob (%s, %s), skipping cache hit",
                source_name,
                data_type,
            )
            return None

        if not extractor.can_extract(blob_dict):
            log.warning(
                "cached blob from %s failed can_extract for %s, skipping cache hit",
                source_name,
                data_type,
            )
            await self._blob_store.delete(source_name, context.match_id)
            return None

        try:
            extracted = extractor.extract(blob_dict, context.match_id, context.region)
        except ExtractionError:
            log.warning(
                "cached blob from %s raised ExtractionError for %s, skipping cache hit",
                source_name,
                data_type,
                exc_info=True,
            )
            return None

        await self._raw_store.set(context.match_id, json.dumps(extracted))
        return WaterfallResult(status="cached", data=extracted, source=source_name)

    async def _try_sources(  # noqa: C901
        self,
        context: FetchContext,
        data_type: DataType,
    ) -> WaterfallResult:
        """Iterate sources for data_type. Returns final WaterfallResult."""
        retry_hints: list[int] = []
        any_blob_validation_failed = False
        tried: list[tuple[str, FetchResult]] = []

        for entry in self._registry.sources_for(data_type):
            # Check required_context_keys before calling fetch().
            missing_keys = entry.source.required_context_keys - set(context.extra.keys())
            if missing_keys:
                log.warning(
                    "source %s requires context keys %s, skipping",
                    entry.name,
                    missing_keys,
                )
                continue

            response = await entry.source.fetch(context, data_type)
            tried.append((entry.name, response.result))

            if response.result == FetchResult.SUCCESS:
                result, validation_failed = await self._handle_success(
                    entry.name, context, data_type, response
                )
                if result is not None:
                    result.tried_sources = tried
                    return result
                if validation_failed:
                    any_blob_validation_failed = True
                continue

            if response.retry_after_ms is not None:
                retry_hints.append(response.retry_after_ms)

            if response.result in (
                FetchResult.THROTTLED,
                FetchResult.UNAVAILABLE,
                FetchResult.SERVER_ERROR,
            ):
                log.info(
                    "source %s returned %s, trying next",
                    entry.name,
                    response.result.value,
                )
                continue

            if response.result == FetchResult.NOT_FOUND:
                if data_type in entry.primary_for:
                    return WaterfallResult(status="not_found", tried_sources=tried)
                continue

            if response.result == FetchResult.AUTH_ERROR:
                if data_type in entry.primary_for:
                    return WaterfallResult(status="auth_error", tried_sources=tried)
                continue

        # All sources exhausted.
        return WaterfallResult(
            status="all_exhausted",
            retry_after_ms=max(retry_hints) if retry_hints else None,
            blob_validation_failed=any_blob_validation_failed,
            tried_sources=tried,
        )

    async def _handle_success(
        self,
        source_name: str,
        context: FetchContext,
        data_type: DataType,
        response: FetchResponse,
    ) -> tuple[WaterfallResult | None, bool]:
        """Process a SUCCESS response.

        Returns (result, blob_validation_failed). Result is None when the
        caller should continue to the next source. blob_validation_failed
        is True only when can_extract() returned False (not for oversized
        blobs or missing extractors).
        """
        # Guard: raw_blob is None -- treat as validation failure.
        if response.raw_blob is None:
            log.warning(
                "source %s returned SUCCESS with raw_blob=None, skipping",
                source_name,
            )
            return None, True

        # Blob size guard -- treated as UNAVAILABLE, not blob_validation_failed.
        if len(response.raw_blob) > MAX_BLOB_SIZE_BYTES:
            log.warning(
                "source %s returned oversized blob (%d bytes), skipping",
                source_name,
                len(response.raw_blob),
            )
            return None, False

        # Prefer pre-parsed data to avoid redundant json.loads(raw_blob).
        blob_dict: dict[str, str] = (
            response.data
            if response.data is not None
            else json.loads(response.raw_blob)
        )
        extractor = self._get_extractor(source_name, data_type)
        if extractor is None:
            log.warning("no extractor for (%s, %s), skipping", source_name, data_type)
            return None, False

        if not extractor.can_extract(blob_dict):
            return None, True

        # Persist blob to blob_store before extraction.
        if self._blob_store is not None and response.raw_blob is not None:
            await self._blob_store.write(source_name, context.match_id, response.raw_blob)

        try:
            extracted = extractor.extract(blob_dict, context.match_id, context.region)
        except ExtractionError:
            log.warning(
                "source %s raised ExtractionError during extraction, skipping",
                source_name,
                exc_info=True,
            )
            return None, True

        await self._raw_store.set(context.match_id, json.dumps(extracted))
        return WaterfallResult(
            status="success",
            data=extracted,
            source=source_name,
            available_data_types=response.available_data_types,
        ), False
