"""Async Riot HTTP client with typed exceptions."""

from __future__ import annotations

import asyncio
import logging
import math
import time as _time
from typing import Any, cast
from urllib.parse import quote

import httpx
import redis.asyncio as aioredis

from lol_pipeline.rate_limiter_client import _get_client as _get_rl_client

_log = logging.getLogger("riot_api")

# Rate-limit window durations used for *informational* header parsing only.
# The rate-limiter service controls actual limiting; these constants let
# _parse_rate_limit_header extract short/long values for logging and the
# POST /headers payload.
_SHORT_WINDOW_S: int = 1
_LONG_WINDOW_S: int = 120

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


def _parse_rate_limit_header(
    header: str,
    field_name: str = "X-App-Rate-Limit",
    *,
    short_window_s: int | None = None,
    long_window_s: int | None = None,
) -> tuple[int, int] | None:
    """Parse a Riot rate-limit header into (short_value, long_value).

    Works for both ``X-App-Rate-Limit`` (limits) and ``X-App-Rate-Limit-Count``
    (current usage).  Expects the standard Riot format ``"20:1,100:120"`` where
    each entry is ``"value:window_seconds"``.

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
            if field_name == "X-App-Rate-Limit":
                _log.warning(
                    "%s missing expected windows — using defaults",
                    field_name,
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
        if field_name == "X-App-Rate-Limit":
            _log.warning(
                "failed to parse %s header — using defaults",
                field_name,
                extra={"header": header},
            )
        return None


# Public aliases that preserve the original call-site names.
def _parse_app_rate_limit(
    header: str,
    *,
    short_window_s: int | None = None,
    long_window_s: int | None = None,
) -> tuple[int, int] | None:
    """Parse X-App-Rate-Limit header. Thin wrapper around _parse_rate_limit_header."""
    return _parse_rate_limit_header(
        header,
        "X-App-Rate-Limit",
        short_window_s=short_window_s,
        long_window_s=long_window_s,
    )


def _parse_rate_limit_count(
    header: str,
    *,
    short_window_s: int | None = None,
    long_window_s: int | None = None,
) -> tuple[int, int] | None:
    """Parse X-App-Rate-Limit-Count header. Thin wrapper around _parse_rate_limit_header."""
    return _parse_rate_limit_header(
        header,
        "X-App-Rate-Limit-Count",
        short_window_s=short_window_s,
        long_window_s=long_window_s,
    )


def _check_rate_limit_count(
    count_header: str,
    limits: tuple[int, int] | None,
) -> bool:
    """Log a warning when remaining capacity drops to 10% or below for either window.

    Returns True when remaining capacity is at or below 5% in either window,
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
    if short_limit > 0 and (short_limit - short_count) <= short_limit * 0.1:
        _log.warning(
            "rate limit near capacity (short window): %d/%d used",
            short_count,
            short_limit,
            extra={"window": "short", "used": short_count, "limit": short_limit},
        )
    if long_limit > 0 and (long_limit - long_count) <= long_limit * 0.1:
        _log.warning(
            "rate limit near capacity (long window): %d/%d used",
            long_count,
            long_limit,
            extra={"window": "long", "used": long_count, "limit": long_limit},
        )
    # Throttle hint when <= 5% capacity remains in either window
    if short_limit > 0 and (short_limit - short_count) <= short_limit * 0.05:
        near_limit = True
    if long_limit > 0 and (long_limit - long_count) <= long_limit * 0.05:
        near_limit = True
    return near_limit


def _raise_for_status_raw(resp: httpx.Response) -> tuple[Any, bytes]:
    """Like _raise_for_status but also returns the raw response body on success.

    Returns (parsed_data, raw_bytes) for 200 responses so callers can avoid
    a redundant json.dumps() round-trip when they need both forms.
    """
    if resp.status_code == 200:
        return resp.json(), resp.content
    _raise_for_non_200(resp)
    # unreachable — _raise_for_non_200 always raises
    raise AssertionError  # pragma: no cover


def _raise_for_non_200(resp: httpx.Response) -> None:
    """Raise the appropriate exception for non-200 status codes."""
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
                    retry_ms = int(parsed) * 1000 + 1000
            except (ValueError, TypeError, OverflowError):
                retry_ms = 1000
        raise RateLimitError(retry_ms)
    raise ServerError(f"HTTP {resp.status_code}: {resp.text[:200]}", status_code=resp.status_code)


def _raise_for_status(resp: httpx.Response) -> Any:
    if resp.status_code == 200:
        return resp.json()
    _raise_for_non_200(resp)


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
        self._r = r  # kept for backward-compat; no longer used for rate-limit writes
        self._client = client or httpx.AsyncClient(timeout=30.0)
        # R4: Circuit breaker state
        self._consecutive_5xx: int = 0
        self._circuit_open_until: float = 0.0

    # -- PRIN-COM-3: circuit-breaker increment extracted ----------------------

    def _on_server_error(self) -> None:
        """Increment the consecutive 5xx counter and open the circuit if threshold hit."""
        self._consecutive_5xx += 1
        if self._consecutive_5xx >= self._CIRCUIT_THRESHOLD:
            self._circuit_open_until = _time.monotonic() + self._CIRCUIT_OPEN_S
            _log.warning(
                "circuit breaker opened after %d consecutive 5xx errors",
                self._consecutive_5xx,
            )

    # -- PRIN-COM-2: routing resolution extracted -----------------------------

    @staticmethod
    def _resolve_base(region: str) -> str:
        """Resolve a platform region to the Riot API base URL."""
        routing = PLATFORM_TO_REGION.get(region, "americas")
        return _API_BASE.format(routing=routing)

    # -- PRIN-COM-4: _get decomposed into sub-functions -----------------------

    def _check_circuit_breaker(self) -> None:
        """Reject requests while the circuit breaker is open."""
        if _time.monotonic() < self._circuit_open_until:
            raise ServerError("circuit breaker open — skipping API call", status_code=503)

    async def _send_request(self, url: str) -> httpx.Response:
        """Send the authenticated GET request; translate network errors."""
        try:
            return await self._client.get(
                url,
                headers={
                    "X-Riot-Token": self._api_key,
                    "User-Agent": "lol-pipeline/1.0",
                },
            )
        except httpx.RequestError as exc:
            self._on_server_error()
            raise ServerError(f"network error: {exc}") from exc

    def _track_5xx(self, resp: httpx.Response) -> None:
        """Update circuit-breaker state based on response status."""
        if resp.status_code >= 500:
            self._on_server_error()
        else:
            self._consecutive_5xx = 0

    async def _persist_rate_limits(self, resp: httpx.Response) -> None:
        """Forward rate-limit headers to the rate-limiter service via POST /headers.

        If the service reports ``throttle: true`` (>90% capacity used),
        applies a 200 ms proactive sleep.  Fails open: if the HTTP call
        fails for any reason, a warning is logged and execution continues.
        """
        rate_limit = resp.headers.get("X-App-Rate-Limit", "")
        rate_limit_count = resp.headers.get("X-App-Rate-Limit-Count", "")
        if not rate_limit:
            return
        try:
            rl_client = _get_rl_client()
            rl_resp = await rl_client.post(
                "/headers",
                json={
                    "source": "riot",
                    "rate_limit": rate_limit,
                    "rate_limit_count": rate_limit_count,
                },
            )
            data = rl_resp.json()
            if data.get("throttle"):
                await asyncio.sleep(0.2)
        except Exception as exc:
            _log.warning("rate-limiter: POST /headers failed (%s), continuing", exc)

    async def _get(self, url: str) -> Any:
        """Authenticated GET with circuit breaker, error mapping, and rate-limit persistence."""
        self._check_circuit_breaker()
        resp = await self._send_request(url)
        self._track_5xx(resp)
        data = _raise_for_status(resp)
        await self._persist_rate_limits(resp)
        return data

    async def _get_with_raw(self, url: str) -> tuple[Any, bytes]:
        """Like _get but also returns the raw response bytes.

        Returns (parsed_data, raw_bytes) to avoid a json.dumps() round-trip
        when the caller needs both forms (e.g. RiotSource storing raw_blob).
        """
        self._check_circuit_breaker()
        resp = await self._send_request(url)
        self._track_5xx(resp)
        data, raw = _raise_for_status_raw(resp)
        await self._persist_rate_limits(resp)
        return data, raw

    # -- Public API methods ---------------------------------------------------

    async def get_account_by_riot_id(
        self, game_name: str, tag_line: str, region: str
    ) -> dict[str, Any]:
        """Resolve a Riot ID to an account dict containing 'puuid'."""
        base = self._resolve_base(region)
        safe_name = quote(game_name, safe="")
        safe_tag = quote(tag_line, safe="")
        url = f"{base}/riot/account/v1/accounts/by-riot-id/{safe_name}/{safe_tag}"
        return cast(dict[str, Any], await self._get(url))

    async def get_account_by_puuid(self, puuid: str, region: str) -> dict[str, Any]:
        """Resolve a PUUID to an account dict containing 'gameName' and 'tagLine'."""
        base = self._resolve_base(region)
        url = f"{base}/riot/account/v1/accounts/by-puuid/{puuid}"
        return cast(dict[str, Any], await self._get(url))

    async def get_match_ids(
        self, puuid: str, region: str, start: int = 0, count: int = 100
    ) -> list[str]:
        """Return up to count match IDs for puuid, paginated by start."""
        base = self._resolve_base(region)
        url = f"{base}/lol/match/v5/matches/by-puuid/{puuid}/ids?start={start}&count={count}"
        return cast(list[str], await self._get(url))

    async def get_match(self, match_id: str, region: str) -> dict[str, Any]:
        """Fetch raw match JSON by match_id."""
        base = self._resolve_base(region)
        url = f"{base}/lol/match/v5/matches/{match_id}"
        return cast(dict[str, Any], await self._get(url))

    async def get_match_with_raw(self, match_id: str, region: str) -> tuple[dict[str, Any], bytes]:
        """Fetch match data and raw response bytes in a single request.

        Returns (parsed_dict, raw_bytes) so callers can persist the raw bytes
        without a redundant json.dumps() round-trip.
        """
        base = self._resolve_base(region)
        url = f"{base}/lol/match/v5/matches/{match_id}"
        data, raw = await self._get_with_raw(url)
        return cast(dict[str, Any], data), raw

    async def get_match_timeline(self, match_id: str, region: str) -> dict[str, Any]:
        """Fetch match timeline JSON by match_id."""
        base = self._resolve_base(region)
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
