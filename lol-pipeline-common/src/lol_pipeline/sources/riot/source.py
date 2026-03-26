"""RiotSource -- wraps the existing RiotClient for the source waterfall.

Maps Riot API exceptions to FetchResult values so the WaterfallCoordinator
can fall through to the next source without any Riot-specific knowledge.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from lol_pipeline.rate_limiter_client import try_token
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
from lol_pipeline.sources.base import (
    MATCH,
    DataType,
    FetchContext,
    FetchResponse,
    FetchResult,
)

_log = logging.getLogger(__name__)

_ERROR_MAP: dict[type[Exception], FetchResult] = {
    NotFoundError: FetchResult.NOT_FOUND,
    AuthError: FetchResult.AUTH_ERROR,
    ServerError: FetchResult.SERVER_ERROR,
    TimeoutError: FetchResult.THROTTLED,
}


def _map_riot_error(exc: Exception) -> FetchResponse:
    """Convert a Riot API exception to a FetchResponse."""
    if isinstance(exc, RateLimitError):
        return FetchResponse(
            result=FetchResult.THROTTLED,
            retry_after_ms=exc.retry_after_ms,
        )
    result = _ERROR_MAP.get(type(exc))
    if result is not None:
        return FetchResponse(result=result)
    raise exc  # pragma: no cover — re-raise unexpected exceptions


class RiotSource:
    """Riot Games API source for the waterfall pipeline.

    Uses ``try_token()`` for non-blocking rate-limit checks so the
    coordinator can fall through to the next source immediately when
    Riot's rate limit is saturated.
    """

    name = "riot"
    supported_data_types: frozenset[DataType] = frozenset({MATCH})
    required_context_keys: frozenset[str] = frozenset()

    def __init__(self, riot_client: RiotClient) -> None:
        self._riot = riot_client

    async def fetch(
        self, context: FetchContext, data_type: DataType
    ) -> FetchResponse:
        """Fetch match data from the Riot API.

        1. Non-blocking rate-limit check via ``try_token()``.
        2. Call ``RiotClient.get_match()``.
        3. Map exceptions to ``FetchResult`` values.
        """
        try:
            granted = await try_token(source="riot", endpoint="match")
            if not granted:
                return FetchResponse(result=FetchResult.THROTTLED)

            data: dict[str, Any] = await self._riot.get_match(
                context.match_id, context.region
            )
            return FetchResponse(
                result=FetchResult.SUCCESS,
                raw_blob=json.dumps(data).encode(),
                data=data,
                available_data_types=frozenset({MATCH}),
            )
        except (RateLimitError, NotFoundError, AuthError, ServerError, TimeoutError) as exc:
            return _map_riot_error(exc)

    async def close(self) -> None:
        """Delegate to the underlying RiotClient."""
        await self._riot.close()


class RiotExtractor:
    """Extractor for Riot API blobs.

    Riot match blobs are already in the canonical pipeline shape
    (containing ``info`` and ``metadata`` top-level keys), so extraction
    is a no-op pass-through.
    """

    source_name = "riot"
    data_types: frozenset[DataType] = frozenset({MATCH})

    def can_extract(self, blob: dict[str, str]) -> bool:
        """Check that the blob has the minimum required Riot match structure."""
        return "info" in blob and "metadata" in blob

    def extract(
        self, blob: dict[str, str], match_id: str, region: str
    ) -> dict[str, str]:
        """Riot blobs are canonical -- return as-is."""
        return blob
