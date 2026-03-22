"""Async Riot HTTP client with typed exceptions."""

from __future__ import annotations

import logging
import math
import os
import time as _time
from typing import Any, cast
from urllib.parse import quote

import httpx
import redis.asyncio as aioredis

_log = logging.getLogger("riot_api")

# Configurable rate limit windows — production Riot API keys use 10s/600s,
# dev keys use 1s/120s.  Read once at module import.
_SHORT_WINDOW_S = int(os.environ.get("RATE_LIMIT_SHORT_WINDOW_S", "1"))
_LONG_WINDOW_S = int(os.environ.get("RATE_LIMIT_LONG_WINDOW_S", "120"))

_RATE_LIMIT_KEY_TTL = 3600  # 1 hour — stale limits expire after API key rotation
_RATE_LIMIT_WRITE_INTERVAL_S = 1800  # Re-write cached values every 30 min to refresh TTL

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

    def __init__(self, message: str = "", status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def _parse_app_rate_limit(
    header: str,
    *,
    short_window_s: int | None = None,
    long_window_s: int | None = None,
) -> tuple[int, int] | None:
    """Parse the X-App-Rate-Limit header value into (short_limit, long_limit).

    Expects the standard Riot format "20:1,100:120" where each entry is
    "count:window_seconds". Looks for windows matching the configured durations
    (defaulting to module-level ``_SHORT_WINDOW_S`` / ``_LONG_WINDOW_S`` which
    are themselves env-configurable via ``RATE_LIMIT_SHORT_WINDOW_S`` and
    ``RATE_LIMIT_LONG_WINDOW_S``).

    Returns None if the header is absent, malformed, or missing either window.
    """
    if not header:
        return None
    target_short = short_window_s if short_window_s is not None else _SHORT_WINDOW_S
    target_long = long_window_s if long_window_s is not None else _LONG_WINDOW_S
    try:
        by_window: dict[int, int] = {}
        for entry in header.split(","):
            count_str, window_str = entry.strip().split(":")
            by_window[int(window_str)] = int(count_str)
        short = by_window.get(target_short)
        long_ = by_window.get(target_long)
        if short is None or long_ is None:
            _log.warning(
                "X-App-Rate-Limit missing expected windows — using defaults",
                extra={
                    "header": header,
                    "windows_found": list(by_window.keys()),
                    "expected_short": target_short,
                    "expected_long": target_long,
                },
            )
            return None
        return short, long_
    except (ValueError, TypeError):
        _log.warning(
            "failed to parse X-App-Rate-Limit header — using defaults",
            extra={"header": header},
        )
        return None


def _parse_rate_limit_count(
    header: str,
    *,
    short_window_s: int | None = None,
    long_window_s: int | None = None,
) -> tuple[int, int] | None:
    """Parse X-App-Rate-Limit-Count header into (short_count, long_count).

    Format is "19:1,85:120" — requests used in each window.
    Returns None if header is absent, malformed, or missing expected windows.
    """
    if not header:
        return None
    target_short = short_window_s if short_window_s is not None else _SHORT_WINDOW_S
    target_long = long_window_s if long_window_s is not None else _LONG_WINDOW_S
    try:
        by_window: dict[int, int] = {}
        for entry in header.split(","):
            count_str, window_str = entry.strip().split(":")
            by_window[int(window_str)] = int(count_str)
        short = by_window.get(target_short)
        long_ = by_window.get(target_long)
        if short is None or long_ is None:
            return None
        return short, long_
    except (ValueError, TypeError):
        return None


def _check_rate_limit_count(
    count_header: str,
    limits: tuple[int, int] | None,
) -> bool:
    """Log a warning when remaining capacity drops below 10% for either window.

    Returns True when remaining capacity is below 5% in either window,
    signalling the caller to set a throttle hint in Redis.
    """
    if not limits:
        return False
    counts = _parse_rate_limit_count(count_header)
    if not counts:
        return False
    short_limit, long_limit = limits
    short_count, long_count = counts
    near_limit = False
    if short_limit > 0 and (short_limit - short_count) < short_limit * 0.1:
        _log.warning(
            "rate limit near capacity (short window): %d/%d used",
            short_count,
            short_limit,
            extra={"window": "short", "used": short_count, "limit": short_limit},
        )
    if long_limit > 0 and (long_limit - long_count) < long_limit * 0.1:
        _log.warning(
            "rate limit near capacity (long window): %d/%d used",
            long_count,
            long_limit,
            extra={"window": "long", "used": long_count, "limit": long_limit},
        )
    # Throttle hint when < 5% capacity remains in either window
    if short_limit > 0 and (short_limit - short_count) < short_limit * 0.05:
        near_limit = True
    if long_limit > 0 and (long_limit - long_count) < long_limit * 0.05:
        near_limit = True
    return near_limit


def _raise_for_status(resp: httpx.Response) -> Any:
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 404:
        raise NotFoundError(str(resp.url))
    if resp.status_code in (401, 403):
        raise AuthError(str(resp.url))
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        retry_ms: int | None = None
        if retry_after:
            try:
                parsed = float(retry_after)
                if not math.isfinite(parsed) or parsed < 0:
                    retry_ms = 1000
                else:
                    # +1000ms jitter to avoid thundering herd on rate-limit window reset
                    retry_ms = int(parsed) * 1000 + 1000
            except (ValueError, TypeError, OverflowError):
                # HTTP-date format or other non-numeric value — use 1s default
                retry_ms = 1000
        raise RateLimitError(retry_ms)
    raise ServerError(f"HTTP {resp.status_code}: {resp.text[:200]}", status_code=resp.status_code)


class RiotClient:
    """Async Riot Games API client. All requests are authenticated via X-Riot-Token."""

    # R4: Circuit breaker constants
    _CIRCUIT_THRESHOLD: int = 5
    _CIRCUIT_OPEN_S: float = 30.0

    def __init__(
        self,
        api_key: str,
        r: aioredis.Redis | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._r = r
        self._client = client or httpx.AsyncClient(timeout=30.0)
        # R4: Circuit breaker state
        self._consecutive_5xx: int = 0
        self._circuit_open_until: float = 0.0
        # Cache last-written rate limit values to avoid redundant Redis writes.
        # Initialized to None so the first successful call always writes.
        self._cached_short_limit: int | None = None
        self._cached_long_limit: int | None = None
        self._limits_last_written_at: float = 0.0

    async def _get(self, url: str) -> Any:
        # R4: Circuit breaker — reject requests while circuit is open
        if _time.monotonic() < self._circuit_open_until:
            raise ServerError("circuit breaker open — skipping API call", status_code=503)

        try:
            resp = await self._client.get(
                url,
                headers={
                    "X-Riot-Token": self._api_key,
                    "User-Agent": "lol-pipeline/1.0",
                },
            )
        except httpx.RequestError as exc:
            self._consecutive_5xx += 1
            if self._consecutive_5xx >= self._CIRCUIT_THRESHOLD:
                self._circuit_open_until = _time.monotonic() + self._CIRCUIT_OPEN_S
                _log.warning(
                    "circuit breaker opened after %d consecutive 5xx errors",
                    self._consecutive_5xx,
                )
            raise ServerError(f"network error: {exc}") from exc

        # Check for 5xx before _raise_for_status (which raises)
        if resp.status_code >= 500:
            self._consecutive_5xx += 1
            if self._consecutive_5xx >= self._CIRCUIT_THRESHOLD:
                self._circuit_open_until = _time.monotonic() + self._CIRCUIT_OPEN_S
                _log.warning(
                    "circuit breaker opened after %d consecutive 5xx errors",
                    self._consecutive_5xx,
                )
        else:
            self._consecutive_5xx = 0

        data = _raise_for_status(resp)
        # On success, persist actual rate limits so the shared rate limiter uses
        # the real values for this API key (dev vs production keys differ).
        if self._r:
            limits = _parse_app_rate_limit(resp.headers.get("X-App-Rate-Limit", ""))
            if limits:
                short, long_ = limits
                # Only write to Redis when the values change or the TTL needs
                # refreshing (every _RATE_LIMIT_WRITE_INTERVAL_S).  At 20 req/s
                # this avoids ~40 redundant SET commands per second.
                now = _time.monotonic()
                values_changed = (
                    short != self._cached_short_limit or long_ != self._cached_long_limit
                )
                ttl_stale = now - self._limits_last_written_at >= _RATE_LIMIT_WRITE_INTERVAL_S
                if values_changed or ttl_stale:
                    await self._r.set("ratelimit:limits:short", str(short), ex=_RATE_LIMIT_KEY_TTL)
                    await self._r.set("ratelimit:limits:long", str(long_), ex=_RATE_LIMIT_KEY_TTL)
                    self._cached_short_limit = short
                    self._cached_long_limit = long_
                    self._limits_last_written_at = now
            # R5: Parse X-App-Rate-Limit-Count for near-limit warnings + throttle hint
            should_throttle = _check_rate_limit_count(
                resp.headers.get("X-App-Rate-Limit-Count", ""),
                limits,
            )
            if should_throttle:
                await self._r.set("ratelimit:throttle", "1", ex=2)
        return data

    async def get_account_by_riot_id(
        self, game_name: str, tag_line: str, region: str
    ) -> dict[str, Any]:
        """Resolve a Riot ID to an account dict containing 'puuid'."""
        routing = PLATFORM_TO_REGION.get(region, "americas")
        base = _API_BASE.format(routing=routing)
        safe_name = quote(game_name, safe="")
        safe_tag = quote(tag_line, safe="")
        url = f"{base}/riot/account/v1/accounts/by-riot-id/{safe_name}/{safe_tag}"
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

    async def get_match_timeline(self, match_id: str, region: str) -> dict[str, Any]:
        """Fetch match timeline JSON by match_id."""
        routing = PLATFORM_TO_REGION.get(region, "americas")
        base = _API_BASE.format(routing=routing)
        url = f"{base}/lol/match/v5/matches/{match_id}/timeline"
        return cast(dict[str, Any], await self._get(url))

    async def get_summoner_by_puuid(self, puuid: str, region: str) -> dict[str, Any]:
        """Fetch summoner data by PUUID (summoner-v4)."""
        base = _API_BASE.format(routing=region)
        url = f"{base}/lol/summoner/v4/summoners/by-puuid/{puuid}"
        return cast(dict[str, Any], await self._get(url))

    async def get_league_entries(self, summoner_id: str, region: str) -> list[dict[str, Any]]:
        """Fetch ranked league entries for a summoner (league-v4)."""
        base = _API_BASE.format(routing=region)
        url = f"{base}/lol/league/v4/entries/by-summoner/{summoner_id}"
        return cast(list[dict[str, Any]], await self._get(url))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
