"""OpggSource -- wraps the existing OpggClient for the source waterfall.

Op.gg cannot look up matches by Riot match_id directly. Its primary value
in the waterfall comes from the BlobStore cache path: blobs saved when
op.gg is used at the crawler level are found by the coordinator before
hitting any remote source.

For direct fetches, this source always returns UNAVAILABLE.
"""

from __future__ import annotations

from lol_pipeline.opgg_client import OpggClient
from lol_pipeline.sources.base import (
    MATCH,
    DataType,
    FetchContext,
    FetchResponse,
    FetchResult,
)


class OpggSource:
    """Op.gg data source for the waterfall pipeline.

    Returns UNAVAILABLE for all fetch requests because op.gg has no
    match-by-Riot-ID endpoint. Value comes exclusively from cached blobs
    in the BlobStore.
    """

    name = "opgg"
    supported_data_types: frozenset[DataType] = frozenset({MATCH})
    required_context_keys: frozenset[str] = frozenset()

    def __init__(self, opgg_client: OpggClient) -> None:
        self._opgg = opgg_client

    async def fetch(self, context: FetchContext, data_type: DataType) -> FetchResponse:
        """Always returns UNAVAILABLE -- op.gg cannot fetch by match_id."""
        return FetchResponse(result=FetchResult.UNAVAILABLE)

    async def close(self) -> None:
        """Delegate to the underlying OpggClient."""
        await self._opgg.close()
