"""op.gg internal API client.

Uses the op.gg SPA internal endpoints to fetch match history and normalize
to match-v5-shaped dicts via the ETL layer in _opgg_etl.py.

op.gg ``game['id']`` is the Riot numeric game ID (e.g.,
``game['id'] = 7234567890`` corresponds to Riot match ``NA1_7234567890``).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import redis.asyncio as aioredis

from lol_pipeline._opgg_etl import normalize_game
from lol_pipeline.rate_limiter_client import try_token, wait_for_token

_log = logging.getLogger("opgg_client")

_BASE_URL = "https://lol-api-summoner.op.gg/api"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.op.gg",
    "Referer": "https://www.op.gg/",
}


class OpggParseError(Exception):
    """Raised when the op.gg response cannot be parsed."""


class OpggRateLimitError(Exception):
    """Raised when op.gg returns HTTP 429 or rate-limit tokens are unavailable."""

    def __init__(self, message: str = "", *, retry_ms: int = 5000) -> None:
        self.retry_ms = retry_ms
        super().__init__(message or f"op.gg rate limit — retry after {retry_ms}ms")


class OpggClient:
    """Thin HTTP client for op.gg internal API.

    Rate limiting:
        Pass ``r`` (a Redis client) to enable rate limiting via the
        centralized rate-limiter HTTP service.  Each endpoint (summoner lookup,
        match history) uses its own endpoint name so bursts on one
        endpoint do not starve the other.  When ``r`` is None, rate limiting is
        skipped (useful in unit tests with respx mocks).

    Inject a custom ``httpx.AsyncClient`` for testing.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        r: aioredis.Redis | None = None,
        rate_limit_per_second: int = 2,
        rate_limit_long: int = 30,
        summoner_cache_ttl_seconds: int = 3600,
        rate_limit_source: str = "opgg",
    ) -> None:
        self._client = client or httpx.AsyncClient(
            headers=_DEFAULT_HEADERS,
            timeout=httpx.Timeout(10.0),
            follow_redirects=True,
        )
        self._r = r
        self._rate_limit_per_second = rate_limit_per_second
        self._rate_limit_long = rate_limit_long
        self._summoner_cache_ttl = summoner_cache_ttl_seconds
        self._rate_limit_source = rate_limit_source

    @staticmethod
    def _check_status(resp: httpx.Response) -> None:
        """Raise typed errors before delegating to raise_for_status."""
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "")
            try:
                retry_ms = int(float(retry_after)) * 1000
            except (ValueError, TypeError):
                retry_ms = 5000
            raise OpggRateLimitError("opgg 429", retry_ms=retry_ms)
        resp.raise_for_status()

    async def _acquire(self, endpoint: str) -> None:
        """Acquire a rate limit token for the given endpoint via HTTP service.

        No-op when no Redis client is configured (backward-compat signal
        that rate limiting should be skipped, e.g. in unit tests).
        """
        if self._r is None:
            return
        await wait_for_token(self._rate_limit_source, endpoint)

    async def get_summoner_id(self, game_name: str, tag_line: str, region: str) -> str:
        """Resolve a Riot ID (game_name#tag_line) to an op.gg summoner_id.

        Raises ``OpggParseError`` if the summoner is not found or on parse failure.
        """
        await self._acquire("summoner")
        riot_id = f"{game_name}#{tag_line}"
        url = f"{_BASE_URL}/v3/{region}/summoners"
        params = {"riot_id": riot_id, "hl": "en_US"}
        resp = await self._client.get(url, params=params)
        if resp.status_code == 404:
            raise OpggParseError(f"summoner not found: {riot_id}")
        self._check_status(resp)
        try:
            data = resp.json()
            return str(data["data"]["summoner_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OpggParseError(f"unexpected response from summoner lookup: {exc}") from exc

    async def get_match_history(
        self,
        summoner_id: str,
        region: str,
        limit: int = 20,
        game_type: str = "total",
        ended_at: str = "",
    ) -> list[dict[str, Any]]:
        """Fetch match history for a summoner_id.

        Returns a list of match-v5-shaped dicts (normalized via ETL layer).
        Raises ``OpggParseError`` on unexpected response structure.
        """
        await self._acquire("games")
        url = f"{_BASE_URL}/{region}/summoners/{summoner_id}/games"
        params: dict[str, str | int] = {
            "limit": limit,
            "game_type": game_type,
            "hl": "en_US",
        }
        if ended_at:
            params["ended_at"] = ended_at
        resp = await self._client.get(url, params=params)
        self._check_status(resp)
        try:
            body = resp.json()
            games: list[dict[str, Any]] | None = body.get("data")
            if games is None:
                raise OpggParseError(f"unexpected response: missing 'data' key in {list(body)}")
        except (TypeError, ValueError) as exc:
            raise OpggParseError(f"unexpected response: {exc}") from exc

        results: list[dict[str, Any]] = []
        for raw_game in games:
            try:
                results.append(normalize_game(raw_game, region))
            except (KeyError, TypeError) as exc:
                _log.warning(
                    "skipping malformed op.gg game",
                    extra={"game_id": raw_game.get("id", "?"), "error": str(exc)},
                )
        return results

    async def get_summoner_id_by_puuid(
        self, puuid: str, region: str, *, blocking: bool = True
    ) -> str:
        """Return op.gg summoner ID for a PUUID, using Redis cache when available."""
        import re

        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", puuid):
            raise OpggParseError(f"Invalid PUUID format: {puuid!r}")

        cache_key = f"opgg:summoner:{puuid}:{region}"
        if self._r is not None:
            cached = await self._r.get(cache_key)
            if cached:
                return cached.decode() if isinstance(cached, bytes) else cached

        if blocking:
            await self._acquire("summoner")
        else:
            granted = await try_token(self._rate_limit_source, "summoner")
            if not granted:
                raise OpggRateLimitError(
                    "opgg summoner token unavailable", retry_ms=0
                )

        url = f"{_BASE_URL}/v3/{region}/summoners"
        resp = await self._client.get(url, params={"puuid": puuid, "hl": "en_US"})
        if resp.status_code == 404:
            raise OpggParseError(
                f"Summoner not found for PUUID {puuid!r} in {region!r}"
            )
        try:
            self._check_status(resp)
        except OpggRateLimitError:
            _log.warning(
                "opgg API 429",
                extra={
                    "url": str(resp.url),
                    "retry_after": resp.headers.get("Retry-After", ""),
                },
            )
            raise

        data = resp.json()
        summoner_id = (
            data.get("data", {}).get("summoner_id")
            or data.get("summoner_id")
            or data.get("id")
        )
        if not summoner_id:
            raise OpggParseError(f"No summoner_id in response: {data!r}")
        summoner_id = str(summoner_id)

        if self._r is not None:
            await self._r.set(cache_key, summoner_id, ex=self._summoner_cache_ttl)

        return summoner_id

    async def get_raw_games(
        self,
        summoner_id: str,
        region: str,
        *,
        limit: int = 5,
        blocking: bool = True,
    ) -> list[dict[str, Any]]:
        """Return raw op.gg game dicts (un-normalized) for the given summoner."""
        import re

        if not re.fullmatch(r"[0-9a-zA-Z_-]+", summoner_id):
            raise OpggParseError(f"Invalid summoner_id format: {summoner_id!r}")

        if blocking:
            await self._acquire("games")
        else:
            granted = await try_token(self._rate_limit_source, "games")
            if not granted:
                raise OpggRateLimitError(
                    "opgg games token unavailable", retry_ms=0
                )

        url = (
            f"{_BASE_URL}/{region}/summoners/{summoner_id}/games"
            f"?limit={limit}&game_type=total&hl=en_US"
        )
        resp = await self._client.get(url)
        try:
            self._check_status(resp)
        except OpggRateLimitError:
            _log.warning(
                "opgg API 429",
                extra={
                    "url": str(resp.url),
                    "retry_after": resp.headers.get("Retry-After", ""),
                },
            )
            raise

        payload = resp.json()
        raw_games: list[dict[str, Any]] | None = payload.get("data")
        if raw_games is None:
            raise OpggParseError(f"No 'data' key in games response: {payload!r}")
        return raw_games

    async def prefetch_player_games(
        self,
        puuid: str,
        opgg_region: str,
        blob_store: object,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Pre-fetch player's recent games to BlobStore. Returns raw game dicts.

        Non-fatal on errors -- logs warnings and returns empty list.
        """
        import json

        from lol_pipeline._opgg_etl import OPGG_REGION_MAP, RIOT_PLATFORM_TO_OPGG_REGION

        # Build reverse map: opgg_region -> Riot platform (e.g., "na" -> "NA1")
        opgg_to_platform = {v: k for k, v in RIOT_PLATFORM_TO_OPGG_REGION.items()}

        try:
            summoner_id = await self.get_summoner_id_by_puuid(
                puuid, opgg_region, blocking=True
            )
            raw_games = await self.get_raw_games(
                summoner_id, opgg_region, limit=limit, blocking=True
            )
        except Exception as exc:
            _log.warning(
                "opgg prefetch_player_games failed (puuid=%s, region=%s): %s",
                puuid,
                opgg_region,
                exc,
            )
            return []

        platform = opgg_to_platform.get(opgg_region, opgg_region.upper())
        for game in raw_games:
            game_id = game.get("id")
            if game_id is None:
                continue
            match_id = f"{platform}_{game_id}"
            raw_blob = json.dumps(game).encode()
            try:
                await blob_store.write("opgg", match_id, raw_blob)  # type: ignore[attr-defined]
            except Exception as exc:
                _log.warning(
                    "opgg prefetch: blob_store.write failed (match_id=%s): %s",
                    match_id,
                    exc,
                )

        return raw_games

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
