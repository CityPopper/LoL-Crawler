"""Async Riot HTTP client with typed exceptions."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx
import redis.asyncio as aioredis

_log = logging.getLogger("riot_api")

PLATFORM_TO_REGION: dict[str, str] = {
    "na1": "americas",
    "br1": "americas",
    "la1": "americas",
    "la2": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "sea",
    "ph2": "sea",
    "sg2": "sea",
    "th2": "sea",
    "tw2": "sea",
    "vn2": "sea",
}

_API_BASE = "https://{routing}.api.riotgames.com"


class RiotAPIError(Exception):
    """Base exception for Riot API errors."""


class NotFoundError(RiotAPIError):
    """HTTP 404 — resource does not exist."""


class AuthError(RiotAPIError):
    """HTTP 403 — API key invalid or revoked."""


class RateLimitError(RiotAPIError):
    """HTTP 429 — rate limit exceeded."""

    def __init__(self, retry_after_ms: int | None = None) -> None:
        super().__init__(f"rate limited; retry_after_ms={retry_after_ms}")
        self.retry_after_ms = retry_after_ms


class ServerError(RiotAPIError):
    """HTTP 5xx — upstream server error."""


def _parse_app_rate_limit(header: str) -> tuple[int, int] | None:
    """Parse the X-App-Rate-Limit header value into (short_limit, long_limit).

    Expects the standard Riot format "20:1,100:120" where each entry is
    "count:window_seconds". Looks specifically for the 1-second and 120-second
    windows. Returns None if the header is absent, malformed, or missing either
    window.
    """
    if not header:
        return None
    try:
        by_window: dict[int, int] = {}
        for entry in header.split(","):
            count_str, window_str = entry.strip().split(":")
            by_window[int(window_str)] = int(count_str)
        short = by_window.get(1)
        long_ = by_window.get(120)
        if short is None or long_ is None:
            _log.warning(
                "X-App-Rate-Limit missing expected windows — using defaults",
                extra={"header": header, "windows_found": list(by_window.keys())},
            )
            return None
        return short, long_
    except (ValueError, TypeError):
        _log.warning(
            "failed to parse X-App-Rate-Limit header — using defaults",
            extra={"header": header},
        )
        return None


def _raise_for_status(resp: httpx.Response) -> Any:
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 404:
        raise NotFoundError(str(resp.url))
    if resp.status_code in (401, 403):
        raise AuthError(str(resp.url))
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        # +1000ms jitter to avoid thundering herd on rate-limit window reset
        retry_ms = int(retry_after) * 1000 + 1000 if retry_after else None
        raise RateLimitError(retry_ms)
    raise ServerError(f"HTTP {resp.status_code}: {resp.text[:200]}")


class RiotClient:
    """Async Riot Games API client. All requests are authenticated via X-Riot-Token."""

    def __init__(
        self,
        api_key: str,
        r: aioredis.Redis | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._r = r
        self._client = client or httpx.AsyncClient(timeout=30.0)

    async def _get(self, url: str) -> Any:
        try:
            resp = await self._client.get(
                url,
                headers={
                    "X-Riot-Token": self._api_key,
                    "User-Agent": "lol-pipeline/1.0",
                },
            )
        except httpx.RequestError as exc:
            raise ServerError(f"network error: {exc}") from exc
        data = _raise_for_status(resp)
        # On success, persist actual rate limits so the shared rate limiter uses
        # the real values for this API key (dev vs production keys differ).
        if self._r:
            limits = _parse_app_rate_limit(resp.headers.get("X-App-Rate-Limit", ""))
            if limits:
                short, long_ = limits
                await self._r.mset(
                    {
                        "ratelimit:limits:short": str(short),
                        "ratelimit:limits:long": str(long_),
                    }
                )
        return data

    async def get_account_by_riot_id(
        self, game_name: str, tag_line: str, region: str
    ) -> dict[str, Any]:
        """Resolve a Riot ID to an account dict containing 'puuid'."""
        routing = PLATFORM_TO_REGION.get(region, "americas")
        base = _API_BASE.format(routing=routing)
        url = f"{base}/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        return cast(dict[str, Any], await self._get(url))

    async def get_account_by_puuid(self, puuid: str, region: str) -> dict[str, Any]:
        """Resolve a PUUID to an account dict containing 'gameName' and 'tagLine'."""
        routing = PLATFORM_TO_REGION.get(region, "americas")
        base = _API_BASE.format(routing=routing)
        url = f"{base}/riot/account/v1/accounts/by-puuid/{puuid}"
        return cast(dict[str, Any], await self._get(url))

    async def get_match_ids(
        self, puuid: str, region: str, start: int = 0, count: int = 100
    ) -> list[str]:
        """Return up to count match IDs for puuid, paginated by start."""
        routing = PLATFORM_TO_REGION.get(region, "americas")
        base = _API_BASE.format(routing=routing)
        url = f"{base}/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start}&count={count}"
        return cast(list[str], await self._get(url))

    async def get_match(self, match_id: str, region: str) -> dict[str, Any]:
        """Fetch raw match JSON by match_id."""
        routing = PLATFORM_TO_REGION.get(region, "americas")
        base = _API_BASE.format(routing=routing)
        url = f"{base}/lol/match/v5/matches/{match_id}"
        return cast(dict[str, Any], await self._get(url))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
