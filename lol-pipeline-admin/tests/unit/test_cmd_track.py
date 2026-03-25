"""Unit tests for lol_admin.cmd_track — STRUCT-1: track sub-command (seed merged into admin).

The ``track`` command resolves a Riot ID to a PUUID, checks cooldown,
writes player data to Redis, publishes to ``stream:puuid``, and sets
``PRIORITY_MANUAL_20`` priority.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.riot_api import RiotClient

from lol_admin.cmd_track import cmd_track

_PUUID = "test-puuid-track-0001"
_STREAM_PUUID = "stream:puuid"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    return Config(_env_file=None)  # type: ignore[call-arg]


def _riot_account_response(
    puuid: str = _PUUID,
    game_name: str = "Faker",
    tag_line: str = "KR1",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={"puuid": puuid, "gameName": game_name, "tagLine": tag_line},
    )


def _make_args(
    riot_id: str = "Faker#KR1",
    region: str = "na1",
) -> argparse.Namespace:
    return argparse.Namespace(riot_id=riot_id, region=region)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCmdTrackHappyPath:
    """STRUCT-1: valid GameName#TagLine resolves, writes Redis, publishes, sets priority."""

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__returns_0(self, r, cfg):
        """track with valid Riot ID returns exit code 0."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            args = _make_args()
            result = await cmd_track(r, riot, cfg, args)
            await riot.close()

        assert result == 0

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__publishes_to_stream_puuid(self, r, cfg):
        """track publishes exactly 1 message to stream:puuid."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            result = await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        assert result == 0
        assert await r.xlen(_STREAM_PUUID) == 1

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__envelope_has_puuid_payload(self, r, cfg):
        """Published envelope payload contains puuid, game_name, tag_line, region."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        entries = await r.xrange(_STREAM_PUUID, "-", "+")
        assert len(entries) == 1
        env = MessageEnvelope.from_redis_fields(entries[0][1])
        payload = json.loads(env.payload) if isinstance(env.payload, str) else env.payload
        assert payload["puuid"] == _PUUID
        assert payload["game_name"] == "Faker"
        assert payload["tag_line"] == "KR1"
        assert payload["region"] == "na1"

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__envelope_has_manual_20_priority(self, r, cfg):
        """Published envelope has priority='manual_20'."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        entries = await r.xrange(_STREAM_PUUID, "-", "+")
        env = MessageEnvelope.from_redis_fields(entries[0][1])
        assert env.priority == "manual_20"

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__sets_priority_key(self, r, cfg):
        """track sets player:priority:{puuid} via set_priority()."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        prio_val = await r.get(f"player:priority:{_PUUID}")
        assert prio_val == "1"

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__registers_player_hash(self, r, cfg):
        """track writes player:{puuid} hash with game_name, tag_line, region, seeded_at."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        player_key = f"player:{_PUUID}"
        assert await r.hget(player_key, "game_name") == "Faker"
        assert await r.hget(player_key, "tag_line") == "KR1"
        assert await r.hget(player_key, "region") == "na1"
        assert await r.hget(player_key, "seeded_at") is not None

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__adds_to_players_all(self, r, cfg):
        """track adds puuid to players:all sorted set."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        score = await r.zscore("players:all", _PUUID)
        assert score is not None

    @pytest.mark.asyncio
    async def test_track__valid_riot_id__prints_success(self, r, cfg, capsys):
        """track prints [OK] confirmation message on success."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        output = capsys.readouterr().out
        assert "[OK]" in output


# ---------------------------------------------------------------------------
# Cooldown check
# ---------------------------------------------------------------------------


class TestCmdTrackCooldown:
    """STRUCT-1: track respects cooldown — recently seeded players are not re-seeded."""

    @pytest.mark.asyncio
    async def test_track__seeded_recently__skips_publish(self, r, cfg):
        """Player seeded 10 min ago (cooldown=30) -> no publish, returns 0."""
        now = datetime.now(tz=UTC)
        ten_min_ago = (now - timedelta(minutes=10)).isoformat()
        await r.hset(f"player:{_PUUID}", mapping={"seeded_at": ten_min_ago})
        # Pre-cache puuid so no API call needed for resolution
        await r.set("player:name:faker#kr1", _PUUID)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            args = _make_args()
            result = await cmd_track(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        assert await r.xlen(_STREAM_PUUID) == 0

    @pytest.mark.asyncio
    async def test_track__last_crawled_recently__skips_publish(self, r, cfg):
        """Player with last_crawled_at 10 min ago (cooldown=30) -> no publish."""
        now = datetime.now(tz=UTC)
        ten_min_ago = (now - timedelta(minutes=10)).isoformat()
        await r.hset(f"player:{_PUUID}", mapping={"last_crawled_at": ten_min_ago})
        await r.set("player:name:faker#kr1", _PUUID)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            result = await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        assert result == 0
        assert await r.xlen(_STREAM_PUUID) == 0

    @pytest.mark.asyncio
    async def test_track__cooldown_expired__publishes_successfully(self, r, cfg):
        """Player seeded 60 min ago (cooldown=30) -> cooldown expired, re-seeds."""
        now = datetime.now(tz=UTC)
        sixty_min_ago = (now - timedelta(minutes=60)).isoformat()
        await r.hset(f"player:{_PUUID}", mapping={"seeded_at": sixty_min_ago})
        await r.set("player:name:faker#kr1", _PUUID)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            result = await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        assert result == 0
        assert await r.xlen(_STREAM_PUUID) == 1

    @pytest.mark.asyncio
    async def test_track__no_previous_seed__no_cooldown_block(self, r, cfg):
        """Brand new player (no player:{puuid} hash) -> no cooldown, proceeds."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_riot_account_response())

            riot = RiotClient("RGAPI-test")
            result = await cmd_track(r, riot, cfg, _make_args())
            await riot.close()

        assert result == 0
        assert await r.xlen(_STREAM_PUUID) == 1


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------


class TestCmdTrackInvalidInput:
    """STRUCT-1: track rejects malformed Riot IDs."""

    @pytest.mark.asyncio
    async def test_track__no_hash_separator__returns_error(self, r, cfg):
        """Riot ID without '#' -> returns 1, no API call, no publish."""
        with respx.mock:
            # No routes — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            args = _make_args(riot_id="FakerKR1")
            result = await cmd_track(r, riot, cfg, args)
            await riot.close()

        assert result == 1
        assert await r.xlen(_STREAM_PUUID) == 0

    @pytest.mark.asyncio
    async def test_track__no_hash_separator__prints_error(self, r, cfg, capsys):
        """Riot ID without '#' -> prints error message to stderr."""
        with respx.mock:
            riot = RiotClient("RGAPI-test")
            args = _make_args(riot_id="FakerKR1")
            await cmd_track(r, riot, cfg, args)
            await riot.close()

        captured = capsys.readouterr()
        assert "[ERROR]" in captured.err


# ---------------------------------------------------------------------------
# Riot API error propagation
# ---------------------------------------------------------------------------


class TestCmdTrackApiErrors:
    """STRUCT-1: track propagates Riot API errors correctly."""

    @pytest.mark.asyncio
    async def test_track__player_not_found__returns_1(self, r, cfg):
        """Riot API 404 (player not found) -> returns 1, no publish."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Ghost/NA1"
            ).mock(return_value=httpx.Response(404, json={"status": {"status_code": 404}}))

            riot = RiotClient("RGAPI-test")
            args = _make_args(riot_id="Ghost#NA1")
            result = await cmd_track(r, riot, cfg, args)
            await riot.close()

        assert result == 1
        assert await r.xlen(_STREAM_PUUID) == 0

    @pytest.mark.asyncio
    async def test_track__api_auth_error__returns_1(self, r, cfg):
        """Riot API 403 -> returns 1, no publish."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=httpx.Response(403, json={"status": {"status_code": 403}}))

            riot = RiotClient("RGAPI-test")
            args = _make_args()
            result = await cmd_track(r, riot, cfg, args)
            await riot.close()

        assert result == 1
        assert await r.xlen(_STREAM_PUUID) == 0

    @pytest.mark.asyncio
    async def test_track__rate_limited__returns_1(self, r, cfg):
        """Riot API 429 -> returns 1, no publish."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=httpx.Response(429, json={"status": {"status_code": 429}}))

            riot = RiotClient("RGAPI-test")
            args = _make_args()
            result = await cmd_track(r, riot, cfg, args)
            await riot.close()

        assert result == 1
        assert await r.xlen(_STREAM_PUUID) == 0


# ---------------------------------------------------------------------------
# System halted
# ---------------------------------------------------------------------------


class TestCmdTrackSystemHalted:
    """STRUCT-1: track refuses to seed when system:halted is set."""

    @pytest.mark.asyncio
    async def test_track__system_halted__returns_1(self, r, cfg):
        """system:halted=1 -> returns 1, no API call, no publish."""
        await r.set("system:halted", "1")

        with respx.mock:
            # No routes — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            args = _make_args()
            result = await cmd_track(r, riot, cfg, args)
            await riot.close()

        assert result == 1
        assert await r.xlen(_STREAM_PUUID) == 0
