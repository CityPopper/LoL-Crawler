"""OpggSource -- wraps the existing OpggClient for the source waterfall.

Op.gg cannot look up matches by Riot match_id directly. When the waterfall
reaches OpggSource, it resolves the player's op.gg summoner ID via PUUID,
fetches their recent game history, and searches for the target game by its
numeric Riot game ID.
"""

from __future__ import annotations

import json
import logging

import httpx

from lol_pipeline._opgg_etl import OPGG_REGION_MAP, RIOT_PLATFORM_TO_OPGG_REGION
from lol_pipeline.opgg_client import OpggClient, OpggParseError, OpggRateLimitError
from lol_pipeline.rate_limiter_client import notify_cooling_off
from lol_pipeline.sources.base import (
    MATCH,
    DataType,
    FetchContext,
    FetchResponse,
    FetchResult,
)
from lol_pipeline.sources.blob_store import MAX_BLOB_SIZE_BYTES

_log = logging.getLogger(__name__)


class OpggSource:
    """Op.gg data source for the waterfall pipeline.

    Uses non-blocking rate-limit checks (``blocking=False``) so the
    coordinator can fall through to the next source immediately when
    op.gg's rate limit is saturated.
    """

    name = "opgg"
    supported_data_types: frozenset[DataType] = frozenset({MATCH})
    required_context_keys: frozenset[str] = frozenset()

    def __init__(self, opgg_client: OpggClient, game_limit: int = 5) -> None:
        self._opgg = opgg_client
        self._game_limit = game_limit

    async def fetch(self, context: FetchContext, data_type: DataType) -> FetchResponse:
        """Fetch match data from op.gg by looking up the player's recent games."""
        if data_type != MATCH:
            return FetchResponse(result=FetchResult.UNAVAILABLE)

        if not context.puuid:
            _log.warning(
                "opgg source: empty puuid — op.gg fallback unavailable (match_id=%s)",
                context.match_id,
            )
            return FetchResponse(result=FetchResult.UNAVAILABLE)

        opgg_region = (
            RIOT_PLATFORM_TO_OPGG_REGION.get(context.region.upper())
            or OPGG_REGION_MAP.get(context.region.lower())
        )
        if not opgg_region:
            return FetchResponse(result=FetchResult.UNAVAILABLE)

        game_id_str = context.match_id.split("_", 1)[-1]
        try:
            target_id = int(game_id_str)
        except ValueError:
            return FetchResponse(result=FetchResult.UNAVAILABLE)

        try:
            summoner_id = await self._opgg.get_summoner_id_by_puuid(
                context.puuid, opgg_region, blocking=False
            )
            raw_games = await self._opgg.get_raw_games(
                summoner_id, opgg_region, limit=self._game_limit, blocking=False
            )
        except OpggRateLimitError as exc:
            if exc.retry_ms:
                await notify_cooling_off("opgg", exc.retry_ms)
            return FetchResponse(
                result=FetchResult.THROTTLED, retry_after_ms=exc.retry_ms
            )
        except OpggParseError:
            return FetchResponse(result=FetchResult.UNAVAILABLE)
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError):
            return FetchResponse(result=FetchResult.UNAVAILABLE)

        for game in raw_games:
            if game.get("id") == target_id:
                raw_blob = json.dumps(game).encode()
                if len(raw_blob) > MAX_BLOB_SIZE_BYTES:
                    return FetchResponse(result=FetchResult.UNAVAILABLE)
                return FetchResponse(
                    result=FetchResult.SUCCESS, raw_blob=raw_blob, data=game
                )

        _log.info(
            "opgg source: game not in recent history (limit=%d, match_id=%s)",
            len(raw_games),
            context.match_id,
        )
        return FetchResponse(result=FetchResult.UNAVAILABLE)

    async def close(self) -> None:
        """Delegate to the underlying OpggClient."""
        await self._opgg.close()
