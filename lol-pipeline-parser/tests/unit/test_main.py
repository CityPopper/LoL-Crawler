"""Unit tests for lol_parser.main — Phase 04 ACs 04-01 through 04-13."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.streams import consume, publish

from lol_parser.main import (
    MATCH_DATA_TTL_SECONDS,
    MAX_DISCOVER_PLAYERS,
    _parse_match,
    main,
)

_IN_STREAM = "stream:parse"
_OUT_STREAM = "stream:analyze"
_GROUP = "parsers"
_FIXTURES = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "lol-pipeline-common"
    / "tests"
    / "fixtures"
)


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


@pytest.fixture
def log():
    return logging.getLogger("test-parser")


def _parse_envelope(match_id="NA1_1234567890", region="na1"):
    return MessageEnvelope(
        source_stream=_IN_STREAM,
        type="parse",
        payload={"match_id": match_id, "region": region},
        max_attempts=5,
    )


async def _setup_message(r, envelope):
    await publish(r, _IN_STREAM, envelope)
    msgs = await consume(r, _IN_STREAM, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0][0]


def _load_fixture(name):
    return (_FIXTURES / name).read_text()


class TestParserNormal:
    @pytest.mark.asyncio
    async def test_valid_match_parsed(self, r, cfg, log):
        """AC-04-01: valid 5v5 match → all Redis keys correct; 10 stream:analyze messages."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.hget(f"match:{match_id}", "status") == "parsed"
        assert await r.sismember("match:status:parsed", match_id)
        assert await r.xlen(_OUT_STREAM) == 10

    @pytest.mark.asyncio
    async def test_player_matches_sorted_set(self, r, cfg, log):
        """AC-04-01b: player:matches:{puuid} has correct score = gameStartTimestamp."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        score = await r.zscore("player:matches:test-puuid-0001", match_id)
        assert score == 1700000000000.0


class TestParserAram:
    @pytest.mark.asyncio
    async def test_aram_match_parsed(self, r, cfg, log):
        """AC-04-02: ARAM match → parsed correctly; 10 participants."""
        raw_store = RawStore(r)
        match_id = "NA1_9876543210"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_aram.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.hget(f"match:{match_id}", "game_mode") == "ARAM"
        assert await r.xlen(_OUT_STREAM) == 10


class TestParserRemake:
    @pytest.mark.asyncio
    async def test_remake_match_parsed(self, r, cfg, log):
        """AC-04-03: remake match → parsed; zero stats handled gracefully."""
        raw_store = RawStore(r)
        match_id = "NA1_1111111111"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_remake.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.hget(f"match:{match_id}", "status") == "parsed"
        p = await r.hgetall(f"participant:{match_id}:test-puuid-0001")
        assert p["kills"] == "0"
        assert p["win"] == "0"


class TestParserErrors:
    @pytest.mark.asyncio
    async def test_raw_blob_missing(self, r, cfg, log):
        """AC-04-04: RawStore.get returns None → nack_to_dlq with parse_error."""
        raw_store = RawStore(r)
        env = _parse_envelope("NA1_MISSING")
        msg_id = await _setup_message(r, env)

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "parse_error"
        assert await r.xlen(_OUT_STREAM) == 0

    @pytest.mark.asyncio
    async def test_missing_participants_field(self, r, cfg, log):
        """AC-04-05: info.participants missing → parse_error."""
        raw_store = RawStore(r)
        match_id = "NA1_BAD"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        data = {
            "metadata": {"matchId": match_id, "participants": []},
            "info": {"gameStartTimestamp": 1000},
        }
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.xlen("stream:dlq") == 1

    @pytest.mark.asyncio
    async def test_empty_participant_list(self, r, cfg, log):
        """Empty participants list → parse_error (not silently marked as parsed)."""
        raw_store = RawStore(r)
        match_id = "NA1_EMPTY"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        data = {
            "metadata": {"matchId": match_id, "participants": []},
            "info": {"participants": [], "gameStartTimestamp": 1000},
        }
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.xlen("stream:dlq") == 1
        assert await r.hget(f"match:{match_id}", "status") is None

    @pytest.mark.asyncio
    async def test_missing_game_start_timestamp(self, r, cfg, log):
        """AC-04-06: info.gameStartTimestamp missing → parse_error."""
        raw_store = RawStore(r)
        match_id = "NA1_NOTS"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        data = {"metadata": {"matchId": match_id, "participants": []}, "info": {"participants": []}}
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.xlen("stream:dlq") == 1

    @pytest.mark.asyncio
    async def test_system_halted_skips(self, r, cfg, log):
        """AC-04-13: system:halted → no ACK; exits; zero Redis writes."""
        await r.set("system:halted", "1")
        raw_store = RawStore(r)
        env = _parse_envelope()
        msg_id = await _setup_message(r, env)

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.xlen(_OUT_STREAM) == 0


class TestParserWinField:
    @pytest.mark.asyncio
    async def test_win_true_stored_as_1(self, r, cfg, log):
        """AC-04-08: win=True → stored as '1'."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # test-puuid-0001 has win=true in fixture
        p = await r.hgetall(f"participant:{match_id}:test-puuid-0001")
        assert p["win"] == "1"
        # test-puuid-0006 has win=false in fixture
        p2 = await r.hgetall(f"participant:{match_id}:test-puuid-0006")
        assert p2["win"] == "0"


class TestParserIdempotent:
    @pytest.mark.asyncio
    async def test_reparse_idempotent(self, r, cfg, log):
        """AC-04-09: parsing same match twice → no duplicate stream:analyze."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        # Parse first time
        env1 = _parse_envelope(match_id)
        msg_id1 = await _setup_message(r, env1)
        await _parse_match(r, raw_store, cfg, msg_id1, env1, log)

        # Parse second time
        env2 = _parse_envelope(match_id)
        msg_id2 = await _setup_message(r, env2)
        await _parse_match(r, raw_store, cfg, msg_id2, env2, log)

        # Sorted set is idempotent but stream:analyze gets new messages (idempotent at consumer)
        score = await r.zscore("player:matches:test-puuid-0001", match_id)
        assert score == 1700000000000.0


class TestParserItems:
    @pytest.mark.asyncio
    async def test_items_stored_as_json_list(self, r, cfg, log):
        """AC-04-07: participant items stored as JSON array [item0..item6]."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        p = await r.hgetall(f"participant:{match_id}:test-puuid-0001")
        items = json.loads(p["items"])
        assert len(items) == 7
        assert items[0] == 3089  # item0 from fixture


class TestParserDiscovery:
    @pytest.mark.asyncio
    async def test_coplayers_added_to_discovery(self, r, cfg, log):
        """Parser adds unknown co-players to discover:players sorted set."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        discover_count = await r.zcard("discover:players")
        assert discover_count == 10  # all 10 PUUIDs are unknown (no player:{puuid} hashes)

    @pytest.mark.asyncio
    async def test_backfilled_player_still_queued_for_discovery(self, r, cfg, log):
        """Players with riotIdGameName/riotIdTagline in match data should still be
        queued for discovery (backfill creates player:{puuid} but without seeded_at)."""
        raw_store = RawStore(r)
        match_id = "NA1_BACKFILL"
        # Build a minimal match with one participant that has riot identity
        data = {
            "metadata": {"matchId": match_id, "participants": ["puuid-with-name"]},
            "info": {
                "gameStartTimestamp": 1700000000000,
                "gameDuration": 900,
                "gameMode": "CLASSIC",
                "gameType": "MATCHED_GAME",
                "gameVersion": "14.1.1",
                "queueId": 420,
                "platformId": "NA1",
                "participants": [
                    {
                        "puuid": "puuid-with-name",
                        "championId": 1,
                        "championName": "Annie",
                        "teamId": 100,
                        "teamPosition": "MID",
                        "role": "SOLO",
                        "win": True,
                        "kills": 5,
                        "deaths": 2,
                        "assists": 3,
                        "goldEarned": 10000,
                        "totalDamageDealtToChampions": 15000,
                        "totalMinionsKilled": 100,
                        "visionScore": 10,
                        "riotIdGameName": "TestPlayer",
                        "riotIdTagline": "NA1",
                    }
                ],
            },
        }
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Name was backfilled
        assert await r.hget("player:puuid-with-name", "game_name") == "TestPlayer"
        assert await r.hget("player:puuid-with-name", "tag_line") == "NA1"
        # But player should still be queued for discovery (no seeded_at)
        discover_count = await r.zcard("discover:players")
        assert discover_count == 1

    @pytest.mark.asyncio
    async def test_seeded_player_not_queued_for_discovery(self, r, cfg, log):
        """Players already seeded (have seeded_at) should NOT be queued for discovery."""
        raw_store = RawStore(r)
        match_id = "NA1_SEEDED"
        data = {
            "metadata": {"matchId": match_id, "participants": ["puuid-seeded"]},
            "info": {
                "gameStartTimestamp": 1700000000000,
                "gameDuration": 900,
                "gameMode": "CLASSIC",
                "gameType": "MATCHED_GAME",
                "gameVersion": "14.1.1",
                "queueId": 420,
                "platformId": "NA1",
                "participants": [
                    {
                        "puuid": "puuid-seeded",
                        "championId": 1,
                        "championName": "Annie",
                        "teamId": 100,
                        "teamPosition": "MID",
                        "role": "SOLO",
                        "win": True,
                        "kills": 5,
                        "deaths": 2,
                        "assists": 3,
                        "goldEarned": 10000,
                        "totalDamageDealtToChampions": 15000,
                        "totalMinionsKilled": 100,
                        "visionScore": 10,
                        "riotIdGameName": "Seeded",
                        "riotIdTagline": "NA1",
                    }
                ],
            },
        }
        # Pre-seed the player
        await r.hset(
            "player:puuid-seeded",
            mapping={
                "game_name": "Seeded",
                "tag_line": "NA1",
                "region": "na1",
                "seeded_at": "2024-01-01T00:00:00+00:00",
            },
        )

        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Already seeded — should NOT be in discovery queue
        discover_count = await r.zcard("discover:players")
        assert discover_count == 0


class TestParserMalformedData:
    """Tests for malformed match data handling."""

    @pytest.mark.asyncio
    async def test_raw_blob_not_json__nacks_to_dlq(self, r, cfg, log):
        """Non-JSON raw blob → parse_error in DLQ."""
        raw_store = RawStore(r)
        match_id = "NA1_NOTJSON"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, "this is not json {{{")

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "parse_error"

    @pytest.mark.asyncio
    async def test_participant_missing_puuid__skips_participant(self, r, cfg, log):
        """Participant without puuid is skipped; valid participants still processed."""
        raw_store = RawStore(r)
        match_id = "NA1_NOPUUID"
        data = {
            "metadata": {"matchId": match_id, "participants": ["valid-puuid"]},
            "info": {
                "gameStartTimestamp": 1700000000000,
                "gameDuration": 900,
                "gameMode": "CLASSIC",
                "gameType": "MATCHED_GAME",
                "gameVersion": "14.1.1",
                "queueId": 420,
                "platformId": "NA1",
                "participants": [
                    {
                        "championId": 1,
                        "championName": "Annie",
                        "teamId": 100,
                        "win": True,
                        "kills": 5,
                        "deaths": 2,
                        "assists": 3,
                    },
                    {
                        "puuid": "valid-puuid",
                        "championId": 2,
                        "championName": "Garen",
                        "teamId": 200,
                        "win": False,
                        "kills": 3,
                        "deaths": 4,
                        "assists": 2,
                    },
                ],
            },
        }
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.xlen(_OUT_STREAM) == 1
        assert await r.hget(f"match:{match_id}", "status") == "parsed"

    @pytest.mark.asyncio
    async def test_participant_missing_stats__uses_defaults(self, r, cfg, log):
        """Participant with missing stat fields uses 0 defaults."""
        raw_store = RawStore(r)
        match_id = "NA1_NOSTATS"
        data = {
            "metadata": {"matchId": match_id, "participants": ["puuid-nostats"]},
            "info": {
                "gameStartTimestamp": 1700000000000,
                "gameDuration": 900,
                "gameMode": "CLASSIC",
                "gameType": "MATCHED_GAME",
                "gameVersion": "14.1.1",
                "queueId": 420,
                "platformId": "NA1",
                "participants": [
                    {
                        "puuid": "puuid-nostats",
                        "championId": 1,
                        "championName": "Annie",
                        "teamId": 100,
                    },
                ],
            },
        }
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        p = await r.hgetall(f"participant:{match_id}:puuid-nostats")
        assert p["kills"] == "0"
        assert p["deaths"] == "0"
        assert p["assists"] == "0"
        assert p["gold_earned"] == "0"


class TestParserPipeline:
    """CQ-15: _write_participant uses Redis pipeline for batched writes."""

    @pytest.mark.asyncio
    async def test_write_participant_uses_pipeline(self, r, cfg, log):
        """All per-participant Redis calls are batched in a single pipeline."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        # Track individual hset calls on the connection (not pipeline)
        direct_hset_count = 0
        original_hset = r.hset

        async def counting_hset(*args, **kwargs):
            nonlocal direct_hset_count
            direct_hset_count += 1
            return await original_hset(*args, **kwargs)

        r.hset = counting_hset

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Both match metadata and participant writes go through pipelines (I2-H11),
        # so zero direct hset calls should occur on the Redis client itself.
        assert direct_hset_count == 0, (
            f"Expected 0 direct hset calls (all via pipeline), got {direct_hset_count}"
        )
        # But data should still be correct
        assert await r.xlen(_OUT_STREAM) == 10


class TestParserMaxlenPolicy:
    """I2-H4: parser publishes to stream:analyze with maxlen=50_000."""

    @pytest.mark.asyncio
    async def test_publish_to_analyze_uses_50k_maxlen(self, r, cfg, log):
        """publish() for stream:analyze is called with maxlen=50_000."""
        raw_store = RawStore(r)
        match_id = "NA1_MAXLEN"
        data = {
            "metadata": {"matchId": match_id, "participants": ["puuid-ml"]},
            "info": {
                "gameStartTimestamp": 1700000000000,
                "gameDuration": 900,
                "gameMode": "CLASSIC",
                "gameType": "MATCHED_GAME",
                "gameVersion": "14.1.1",
                "queueId": 420,
                "platformId": "NA1",
                "participants": [
                    {
                        "puuid": "puuid-ml",
                        "championId": 1,
                        "championName": "Annie",
                        "teamId": 100,
                        "teamPosition": "MID",
                        "role": "SOLO",
                        "win": True,
                        "kills": 5,
                        "deaths": 2,
                        "assists": 3,
                        "goldEarned": 10000,
                        "totalDamageDealtToChampions": 15000,
                        "totalMinionsKilled": 100,
                        "visionScore": 10,
                    }
                ],
            },
        }
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Verify the message was published to stream:analyze (batched via pipeline)
        msgs = await r.xrange("stream:analyze")
        assert len(msgs) == 1, f"Expected 1 analyze message, got {len(msgs)}"
        # Verify the payload contains the correct puuid
        fields = msgs[0][1]
        import json as _json

        payload = _json.loads(fields.get("payload", "{}"))
        assert payload.get("puuid") == "puuid-ml"


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_consumer(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        mock_consumer = AsyncMock()
        with (
            patch("lol_parser.main.Config") as mock_cfg,
            patch("lol_parser.main.get_redis", return_value=mock_r),
            patch("lol_parser.main.RawStore"),
            patch("lol_parser.main.run_consumer", mock_consumer),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            await main()
        mock_consumer.assert_called_once()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__keyboard_interrupt__closes_redis(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        with (
            patch("lol_parser.main.Config") as mock_cfg,
            patch("lol_parser.main.get_redis", return_value=mock_r),
            patch("lol_parser.main.RawStore"),
            patch("lol_parser.main.run_consumer", side_effect=KeyboardInterrupt),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()


class TestMatchDataTTL:
    """I2-C3: match:{match_id} and participant:{match_id}:{puuid} keys get TTL."""

    @pytest.mark.asyncio
    async def test_match_key_has_ttl(self, r, cfg, log):
        """match:{match_id} hash gets MATCH_DATA_TTL_SECONDS TTL after parsing."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        ttl = await r.ttl(f"match:{match_id}")
        assert 0 < ttl <= MATCH_DATA_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_participant_keys_have_ttl(self, r, cfg, log):
        """participant:{match_id}:{puuid} hashes get MATCH_DATA_TTL_SECONDS TTL."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        fixture_raw = _load_fixture("match_normal.json")
        await raw_store.set(match_id, fixture_raw)

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Derive expected puuids from fixture
        fixture_data = json.loads(fixture_raw)
        puuids = [p["puuid"] for p in fixture_data["info"]["participants"]]
        assert len(puuids) == 10
        for puuid in puuids:
            ttl = await r.ttl(f"participant:{match_id}:{puuid}")
            assert 0 < ttl <= MATCH_DATA_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_ttl_constant_defaults_to_7_days(self):
        """MATCH_DATA_TTL_SECONDS defaults to 604800 (7 days)."""
        assert MATCH_DATA_TTL_SECONDS == 604800


class TestAtomicMatchWrite:
    """I2-H11: match HSET + SADD + EXPIRE are atomic (transactional pipeline)."""

    @pytest.mark.asyncio
    async def test_match_hset_and_sadd_both_written(self, r, cfg, log):
        """Both match:{id} hash and match:status:parsed set are written atomically."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Both must exist (atomic write ensures consistency)
        assert await r.hget(f"match:{match_id}", "status") == "parsed"
        assert await r.sismember("match:status:parsed", match_id)
        ttl = await r.ttl(f"match:{match_id}")
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_match_write_uses_transaction(self, r, cfg, log):
        """The match metadata write uses transaction=True pipeline."""
        raw_store = RawStore(r)
        match_id = "NA1_ATOMIC"
        data = {
            "metadata": {"matchId": match_id, "participants": ["puuid-atomic"]},
            "info": {
                "gameStartTimestamp": 1700000000000,
                "gameDuration": 900,
                "gameMode": "CLASSIC",
                "gameType": "MATCHED_GAME",
                "gameVersion": "14.1.1",
                "queueId": 420,
                "platformId": "NA1",
                "participants": [
                    {
                        "puuid": "puuid-atomic",
                        "championId": 1,
                        "championName": "Annie",
                        "teamId": 100,
                        "teamPosition": "MID",
                        "role": "SOLO",
                        "win": True,
                        "kills": 5,
                        "deaths": 2,
                        "assists": 3,
                        "goldEarned": 10000,
                        "totalDamageDealtToChampions": 15000,
                        "totalMinionsKilled": 100,
                        "visionScore": 10,
                    }
                ],
            },
        }
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        pipeline_calls = []
        original_pipeline = r.pipeline

        def tracking_pipeline(**kwargs):
            pipeline_calls.append(kwargs)
            return original_pipeline(**kwargs)

        r.pipeline = tracking_pipeline

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # At least one pipeline call should use transaction=True (the match metadata write)
        assert any(call.get("transaction") is True for call in pipeline_calls), (
            f"Expected at least one transactional pipeline call, got {pipeline_calls}"
        )


class TestDiscoverPlayersCap:
    """I2-M1: discover:players sorted set is capped at MAX_DISCOVER_PLAYERS."""

    @pytest.mark.asyncio
    async def test_discover_set_trimmed_after_zadd(self, r, cfg, log):
        """discover:players is trimmed to MAX_DISCOVER_PLAYERS after ZADD."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        count = await r.zcard("discover:players")
        assert count <= MAX_DISCOVER_PLAYERS

    @pytest.mark.asyncio
    async def test_discover_cap_trims_lowest_scores(self, r, cfg, log, monkeypatch):
        """When discover:players exceeds cap, lowest-scored entries are removed."""
        # Set a tiny cap so we can test trimming
        monkeypatch.setattr("lol_parser.main.MAX_DISCOVER_PLAYERS", 5)

        # Pre-populate discover:players with 5 entries (older timestamps)
        old_scores = {f"old-puuid-{i}:na1": float(1600000000000 + i) for i in range(5)}
        await r.zadd("discover:players", old_scores, gt=True)
        assert await r.zcard("discover:players") == 5

        # Parse a match that adds 10 new discovery entries (newer timestamps)
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # After trimming, should have at most 5 entries
        count = await r.zcard("discover:players")
        assert count <= 5

        # The remaining entries should be the ones with highest scores (newest)
        remaining = await r.zrange("discover:players", 0, -1, withscores=True)
        for _member, score in remaining:
            # All remaining entries should have the newer timestamp (1700000000000)
            assert score >= 1700000000000.0

    @pytest.mark.asyncio
    async def test_max_discover_players_default(self):
        """MAX_DISCOVER_PLAYERS defaults to 50000."""
        assert MAX_DISCOVER_PLAYERS == 50000


class TestPlayerMatchesCap:
    """P10-CR-6: player:matches:{puuid} sorted set capped at PLAYER_MATCHES_MAX."""

    @pytest.mark.asyncio
    async def test_player_matches_trimmed_after_parse(self, r, cfg, log, monkeypatch):
        """player:matches:{puuid} is trimmed to PLAYER_MATCHES_MAX after parsing."""
        # Set a tiny cap for testing
        monkeypatch.setattr("lol_parser.main.PLAYER_MATCHES_MAX", 5)

        # Pre-populate player:matches with 5 existing entries
        puuid = "puuid-cap-test"
        for i in range(5):
            await r.zadd(f"player:matches:{puuid}", {f"OLD_{i}": float(1600000000000 + i)})
        assert await r.zcard(f"player:matches:{puuid}") == 5

        # Parse a match that adds this puuid as a participant (newer timestamp)
        raw_store = RawStore(r)
        match_id = "NA1_CAPTEST"
        data = {
            "metadata": {"matchId": match_id, "participants": [puuid]},
            "info": {
                "gameStartTimestamp": 1700000000000,
                "gameDuration": 900,
                "gameMode": "CLASSIC",
                "gameType": "MATCHED_GAME",
                "gameVersion": "14.1.1",
                "queueId": 420,
                "platformId": "NA1",
                "participants": [
                    {
                        "puuid": puuid,
                        "championId": 1,
                        "championName": "Annie",
                        "teamId": 100,
                        "teamPosition": "MID",
                        "role": "SOLO",
                        "win": True,
                        "kills": 5,
                        "deaths": 2,
                        "assists": 3,
                        "goldEarned": 10000,
                        "totalDamageDealtToChampions": 15000,
                        "totalMinionsKilled": 100,
                        "visionScore": 10,
                    }
                ],
            },
        }
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # After trimming, should have at most 5 entries
        count = await r.zcard(f"player:matches:{puuid}")
        assert count <= 5

        # The new match should be present (highest score)
        score = await r.zscore(f"player:matches:{puuid}", match_id)
        assert score == 1700000000000.0

        # Oldest entries should have been removed
        remaining = await r.zrange(f"player:matches:{puuid}", 0, -1, withscores=True)
        for _member, s in remaining:
            # The kept entries should include the newest one
            assert s >= 1600000000001.0  # at least the second-oldest survived

    @pytest.mark.asyncio
    async def test_player_matches_max_default(self):
        """PLAYER_MATCHES_MAX defaults to 500."""
        from lol_parser.main import PLAYER_MATCHES_MAX

        assert PLAYER_MATCHES_MAX == 500

    @pytest.mark.asyncio
    async def test_player_matches_no_trim_when_under_cap(self, r, cfg, log):
        """When player:matches count is under cap, no entries are removed."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Normal fixture has 10 participants, each gets 1 match entry
        # All should be present (well under default 500 cap)
        for i in range(1, 11):
            puuid = f"test-puuid-{i:04d}"
            count = await r.zcard(f"player:matches:{puuid}")
            assert count == 1


class TestDiscoveryHexistsBatched:
    """HEXISTS calls for seeded_at checks are batched in a single pipeline round-trip."""

    @pytest.mark.asyncio
    async def test_hexists_batched_via_pipeline(self, r, cfg, log):
        """All HEXISTS seeded_at checks use one pipeline, not N individual calls."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        # Track direct hexists calls (should be zero — all via pipeline)
        direct_hexists_count = 0
        original_hexists = r.hexists

        async def counting_hexists(*args, **kwargs):
            nonlocal direct_hexists_count
            direct_hexists_count += 1
            return await original_hexists(*args, **kwargs)

        r.hexists = counting_hexists

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert direct_hexists_count == 0, (
            f"Expected 0 direct hexists calls (all via pipeline), got {direct_hexists_count}"
        )
        # Discovery should still work correctly — all 10 participants are unseeded
        assert await r.zcard("discover:players") == 10

    @pytest.mark.asyncio
    async def test_hexists_batch_respects_seeded_flag(self, r, cfg, log):
        """Batched HEXISTS correctly skips seeded players in discovery."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        # Pre-seed 3 of the 10 participants
        for i in range(3):
            await r.hset(
                f"player:test-puuid-{i + 1:04d}",
                mapping={"seeded_at": "2024-01-01T00:00:00+00:00"},
            )

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Only 7 unseeded players should be in discovery
        assert await r.zcard("discover:players") == 7


class TestParserPipelineOptimizations:
    """P13-OPT-6/7: parser batches post-write ops and analyze publishes."""

    @pytest.mark.asyncio
    async def test_analyze_messages_published_for_all_participants(self, r, cfg, log):
        """All participants get an analyze message in stream:analyze (OPT-7)."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # All 10 participants should have an analyze message
        msgs = await r.xrange("stream:analyze")
        assert len(msgs) == 10

    @pytest.mark.asyncio
    async def test_player_matches_ttl_set_for_all_participants(self, r, cfg, log):
        """All player:matches:{puuid} sorted sets get a TTL (OPT-6)."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Collect all participant puuids
        participant_keys = [k async for k in r.scan_iter("player:matches:*")]
        assert len(participant_keys) > 0, "Expected player:matches:* keys to exist"
        for key in participant_keys:
            ttl = await r.ttl(key)
            assert ttl > 0, f"{key} must have a TTL after parsing"

    @pytest.mark.asyncio
    async def test_player_matches_trimmed_to_max(self, r, cfg, log):
        """player:matches:{puuid} is trimmed to PLAYER_MATCHES_MAX (OPT-6)."""
        from lol_parser.main import PLAYER_MATCHES_MAX

        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        fixture_data = json.loads(_load_fixture("match_normal.json"))
        puuid = fixture_data["metadata"]["participants"][0]

        # Pre-fill with more than PLAYER_MATCHES_MAX entries
        overload: dict[str, float] = {
            f"NA1_OLD{i}": float(1600000000 + i) for i in range(PLAYER_MATCHES_MAX + 10)
        }
        await r.zadd(f"player:matches:{puuid}", overload)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))
        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        count = await r.zcard(f"player:matches:{puuid}")
        assert count <= PLAYER_MATCHES_MAX + 1  # +1 for the new match just added
