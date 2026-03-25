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
    _extract_full_perks,
    _extract_gold_timelines,
    _extract_kill_events,
    _extract_perks,
    _normalize_patch,
    _parse_match,
    _parse_timeline,
    _write_bans,
    _write_matchups,
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
        assert await r.xlen(_OUT_STREAM) == 10

    @pytest.mark.asyncio
    async def test_match_participants_set_written(self, r, cfg, log):
        """Regression: match:participants:{match_id} set must contain all 10 PUUIDs.

        B14 (v1.1.0) accidentally removed the SADD, breaking the UI match
        detail endpoint which reads this set via SMEMBERS.
        """
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        members = await r.smembers(f"match:participants:{match_id}")
        assert len(members) == 10
        assert "test-puuid-0001" in members
        ttl = await r.ttl(f"match:participants:{match_id}")
        assert ttl > 0  # TTL must be set

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

    @pytest.mark.asyncio
    async def test_system_halted_preserves_pel(self, r, cfg, log):
        """TCG-11: system:halted path must NOT ack — message stays in PEL."""
        await r.set("system:halted", "1")
        raw_store = RawStore(r)
        env = _parse_envelope()
        msg_id = await _setup_message(r, env)

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 1


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

    @pytest.mark.asyncio
    async def test_reparse__bans_matchups_not_double_counted(self, r, cfg, log):
        """TOCTOU fix: SADD atomic guard prevents HINCRBY double-count on retry.

        Parsing the same ranked match twice must produce ban count=1 and
        matchup games=1, not 2.
        """
        raw_store = RawStore(r)
        match_id = "NA1_IDEMPOTENT_BAN"
        participants = [
            _make_participant(
                "puuid-top-a",
                teamId=100,
                teamPosition="TOP",
                championName="Garen",
                win=True,
            ),
            _make_participant(
                "puuid-top-b",
                teamId=200,
                teamPosition="TOP",
                championName="Renekton",
                win=False,
            ),
        ]
        data = _make_match_data(
            match_id,
            participants,
            queueId=420,
            teams=[
                {"teamId": 100, "bans": [{"championId": 238, "pickTurn": 1}]},
                {"teamId": 200, "bans": [{"championId": 67, "pickTurn": 2}]},
            ],
        )
        await raw_store.set(match_id, json.dumps(data))

        # Parse first time
        env1 = _parse_envelope(match_id)
        msg_id1 = await _setup_message(r, env1)
        await _parse_match(r, raw_store, cfg, msg_id1, env1, log)

        # Parse second time (simulates retry or concurrent worker)
        env2 = _parse_envelope(match_id)
        msg_id2 = await _setup_message(r, env2)
        await _parse_match(r, raw_store, cfg, msg_id2, env2, log)

        # Bans: each champion should be counted exactly once
        patch = "14.1"
        ban_key = f"champion:bans:{patch}"
        assert await r.hget(ban_key, "238") == "1"
        assert await r.hget(ban_key, "67") == "1"
        assert await r.hget(ban_key, "_total_games") == "1"

        # Matchups: games should be 1, not 2
        assert await r.hget("matchup:Garen:Renekton:TOP:14.1", "games") == "1"
        assert await r.hget("matchup:Renekton:Garen:TOP:14.1", "games") == "1"


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
        """match:{match_id} hash gets match_data_ttl_seconds TTL after parsing."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        ttl = await r.ttl(f"match:{match_id}")
        assert 0 < ttl <= cfg.match_data_ttl_seconds

    @pytest.mark.asyncio
    async def test_participant_keys_have_ttl(self, r, cfg, log):
        """participant:{match_id}:{puuid} hashes get match_data_ttl_seconds TTL."""
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
            assert 0 < ttl <= cfg.match_data_ttl_seconds

    @pytest.mark.asyncio
    async def test_ttl_constant_defaults_to_7_days(self, cfg):
        """match_data_ttl_seconds defaults to 604800 (7 days)."""
        assert cfg.match_data_ttl_seconds == 604800


class TestAtomicMatchWrite:
    """RDB-2: match HSET + EXPIRE atomic; HSETNX is the atomic idempotency guard."""

    @pytest.mark.asyncio
    async def test_match_hash_status_written(self, r, cfg, log):
        """match:{id} hash has status=parsed and a TTL after parsing."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.hget(f"match:{match_id}", "status") == "parsed"
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
        """discover:players is trimmed to max_discover_players after ZADD."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        count = await r.zcard("discover:players")
        assert count <= cfg.max_discover_players

    @pytest.mark.asyncio
    async def test_discover_cap_trims_lowest_scores(self, r, log, monkeypatch):
        """When discover:players exceeds cap, lowest-scored entries are removed."""
        # Set a tiny cap so we can test trimming
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("MAX_DISCOVER_PLAYERS", "5")
        small_cfg = Config(_env_file=None)  # type: ignore[call-arg]

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

        await _parse_match(r, raw_store, small_cfg, msg_id, env, log)

        # After trimming, should have at most 5 entries
        count = await r.zcard("discover:players")
        assert count <= 5

        # The remaining entries should be the ones with highest scores (newest)
        remaining = await r.zrange("discover:players", 0, -1, withscores=True)
        for _member, score in remaining:
            # All remaining entries should have the newer timestamp (1700000000000)
            assert score >= 1700000000000.0

    @pytest.mark.asyncio
    async def test_max_discover_players_default(self, cfg):
        """max_discover_players defaults to 50000."""
        assert cfg.max_discover_players == 50000


class TestPlayerMatchesCap:
    """P10-CR-6: player:matches:{puuid} sorted set capped at PLAYER_MATCHES_MAX."""

    @pytest.mark.asyncio
    async def test_player_matches_trimmed_after_parse(self, r, log, monkeypatch):
        """player:matches:{puuid} is trimmed to player_matches_max after parsing."""
        # Set a tiny cap for testing
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("PLAYER_MATCHES_MAX", "5")
        cfg = Config(_env_file=None)  # type: ignore[call-arg]

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
    async def test_player_matches_max_default(self, cfg):
        """player_matches_max defaults to 500."""
        assert cfg.player_matches_max == 500

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
        """player:matches:{puuid} is trimmed to player_matches_max (OPT-6)."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        fixture_data = json.loads(_load_fixture("match_normal.json"))
        puuid = fixture_data["metadata"]["participants"][0]

        # Pre-fill with more than player_matches_max entries
        overload: dict[str, float] = {
            f"NA1_OLD{i}": float(1600000000 + i) for i in range(cfg.player_matches_max + 10)
        }
        await r.zadd(f"player:matches:{puuid}", overload)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))
        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        count = await r.zcard(f"player:matches:{puuid}")
        assert count <= cfg.player_matches_max + 1  # +1 for the new match just added


class TestNormalizePatch:
    """_normalize_patch extracts major.minor from game version strings."""

    def test_normalize_three_part(self):
        assert _normalize_patch("13.24.1") == "13.24"

    def test_normalize_two_part(self):
        assert _normalize_patch("14.1") == "14.1"

    def test_normalize_single_part(self):
        assert _normalize_patch("14") == "14"

    def test_normalize_empty(self):
        assert _normalize_patch("") == ""


class TestExtractPerks:
    """_extract_perks extracts keystone, primary style, and sub style from participant data."""

    def test_extract_perks_normal(self):
        p = {
            "perks": {
                "styles": [
                    {
                        "style": 8100,
                        "selections": [
                            {"perk": 8112},
                            {"perk": 8126},
                        ],
                    },
                    {
                        "style": 8300,
                        "selections": [
                            {"perk": 8304},
                        ],
                    },
                ],
            },
        }
        keystone, primary, sub = _extract_perks(p)
        assert keystone == 8112
        assert primary == 8100
        assert sub == 8300

    def test_extract_perks_empty(self):
        assert _extract_perks({}) == (0, 0, 0)
        assert _extract_perks({"perks": {}}) == (0, 0, 0)
        assert _extract_perks({"perks": {"styles": []}}) == (0, 0, 0)

    def test_extract_perks_no_sub_style(self):
        p = {
            "perks": {
                "styles": [
                    {
                        "style": 8100,
                        "selections": [{"perk": 8112}],
                    },
                ],
            },
        }
        keystone, primary, sub = _extract_perks(p)
        assert keystone == 8112
        assert primary == 8100
        assert sub == 0


def _make_participant(puuid, **overrides):
    """Build a minimal participant dict with optional overrides."""
    base = {
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
    base.update(overrides)
    return base


def _make_match_data(match_id, participants, **info_overrides):
    """Build a minimal match data dict."""
    info = {
        "gameStartTimestamp": 1700000000000,
        "gameDuration": 900,
        "gameMode": "CLASSIC",
        "gameType": "MATCHED_GAME",
        "gameVersion": "14.1.1",
        "queueId": 420,
        "platformId": "NA1",
        "participants": participants,
    }
    info.update(info_overrides)
    return {
        "metadata": {
            "matchId": match_id,
            "participants": [p["puuid"] for p in participants],
        },
        "info": info,
    }


class TestExtendedParticipantFields:
    """Extended participant fields are stored in the participant hash."""

    @pytest.mark.asyncio
    async def test_participant_has_summoner_spells(self, r, cfg, log):
        raw_store = RawStore(r)
        match_id = "NA1_SPELLS"
        p = _make_participant("puuid-spells", summoner1Id=4, summoner2Id=14)
        data = _make_match_data(match_id, [p])
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"participant:{match_id}:puuid-spells")
        assert h["summoner1_id"] == "4"
        assert h["summoner2_id"] == "14"

    @pytest.mark.asyncio
    async def test_participant_has_perk_fields(self, r, cfg, log):
        raw_store = RawStore(r)
        match_id = "NA1_PERKS"
        p = _make_participant(
            "puuid-perks",
            perks={
                "styles": [
                    {"style": 8100, "selections": [{"perk": 8112}, {"perk": 8126}]},
                    {"style": 8300, "selections": [{"perk": 8304}]},
                ],
            },
        )
        data = _make_match_data(match_id, [p])
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"participant:{match_id}:puuid-perks")
        assert h["perk_keystone"] == "8112"
        assert h["perk_primary_style"] == "8100"
        assert h["perk_sub_style"] == "8300"

    @pytest.mark.asyncio
    async def test_participant_has_multi_kills(self, r, cfg, log):
        raw_store = RawStore(r)
        match_id = "NA1_MULTI"
        p = _make_participant(
            "puuid-multi",
            doubleKills=3,
            tripleKills=2,
            quadraKills=1,
            pentaKills=0,
        )
        data = _make_match_data(match_id, [p])
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"participant:{match_id}:puuid-multi")
        assert h["double_kills"] == "3"
        assert h["triple_kills"] == "2"
        assert h["quadra_kills"] == "1"
        assert h["penta_kills"] == "0"

    @pytest.mark.asyncio
    async def test_participant_has_damage_breakdown(self, r, cfg, log):
        raw_store = RawStore(r)
        match_id = "NA1_DMG"
        p = _make_participant(
            "puuid-dmg",
            physicalDamageDealtToChampions=8000,
            magicDamageDealtToChampions=5000,
            trueDamageDealtToChampions=2000,
        )
        data = _make_match_data(match_id, [p])
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"participant:{match_id}:puuid-dmg")
        assert h["physical_damage"] == "8000"
        assert h["magic_damage"] == "5000"
        assert h["true_damage"] == "2000"


class TestPatchInMatchHash:
    """Normalized patch field is stored in the match hash."""

    @pytest.mark.asyncio
    async def test_match_hash_has_patch(self, r, cfg, log):
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # match_normal.json has gameVersion "13.24.1" -> patch "13.24"
        assert await r.hget(f"match:{match_id}", "patch") == "13.24"


def _ranked_info_with_bans(bans_team_100=None, bans_team_200=None):
    """Build ranked match info dict with teams/bans data."""
    if bans_team_100 is None:
        bans_team_100 = [
            {"championId": 238, "pickTurn": 1},
            {"championId": 67, "pickTurn": 2},
        ]
    if bans_team_200 is None:
        bans_team_200 = [
            {"championId": 86, "pickTurn": 3},
            {"championId": -1, "pickTurn": 4},  # no ban
        ]
    return {
        "queueId": 420,
        "gameStartTimestamp": 1700000000000,
        "gameDuration": 1800,
        "gameMode": "CLASSIC",
        "gameVersion": "14.1.1",
        "teams": [
            {"teamId": 100, "bans": bans_team_100},
            {"teamId": 200, "bans": bans_team_200},
        ],
        "participants": [],
    }


class TestWriteBans:
    @pytest.mark.asyncio
    async def test_bans_extracted_for_ranked(self, r, cfg, log):
        """Ranked match with bans writes to champion:bans:{patch}."""
        info = _ranked_info_with_bans()
        await _write_bans(r, "NA1_BAN1", info, "14.1", cfg, log)

        ban_key = "champion:bans:14.1"
        assert await r.hget(ban_key, "238") == "1"
        assert await r.hget(ban_key, "67") == "1"
        assert await r.hget(ban_key, "86") == "1"
        assert await r.hget(ban_key, "_total_games") == "1"
        # TTL should be set (90 days)
        ttl = await r.ttl(ban_key)
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_bans_skipped_for_non_ranked(self, r, cfg, log):
        """ARAM match (queueId=450) does not write bans."""
        info = _ranked_info_with_bans()
        info["queueId"] = 450
        await _write_bans(r, "NA1_ARAM", info, "14.1", cfg, log)

        assert await r.exists("champion:bans:14.1") == 0

    @pytest.mark.asyncio
    async def test_bans_skipped_when_disabled(self, r, log, monkeypatch):
        """cfg.track_bans=False skips ban extraction."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("TRACK_BANS", "false")
        disabled_cfg = Config(_env_file=None)  # type: ignore[call-arg]
        info = _ranked_info_with_bans()
        await _write_bans(r, "NA1_NOBANS", info, "14.1", disabled_cfg, log)

        assert await r.exists("champion:bans:14.1") == 0

    @pytest.mark.asyncio
    async def test_bans_negative_champion_id_ignored(self, r, cfg, log):
        """championId=-1 (no ban) is skipped; only positive IDs stored."""
        info = _ranked_info_with_bans(
            bans_team_100=[{"championId": -1, "pickTurn": 1}],
            bans_team_200=[{"championId": -1, "pickTurn": 2}],
        )
        await _write_bans(r, "NA1_NOBANCHAMP", info, "14.1", cfg, log)

        ban_key = "champion:bans:14.1"
        # _total_games incremented, but no champion IDs stored
        assert await r.hget(ban_key, "_total_games") == "1"
        all_fields = await r.hgetall(ban_key)
        assert len(all_fields) == 1  # only _total_games


def _ranked_matchup_participants():
    """Build participants for a standard ranked 5v5 with all positions filled."""
    return [
        _make_participant(
            "puuid-top-a", teamId=100, teamPosition="TOP", championName="Garen", win=True
        ),
        _make_participant(
            "puuid-jgl-a", teamId=100, teamPosition="JUNGLE", championName="LeeSin", win=True
        ),
        _make_participant(
            "puuid-mid-a", teamId=100, teamPosition="MID", championName="Annie", win=True
        ),
        _make_participant(
            "puuid-bot-a", teamId=100, teamPosition="BOTTOM", championName="Jinx", win=True
        ),
        _make_participant(
            "puuid-sup-a", teamId=100, teamPosition="UTILITY", championName="Thresh", win=True
        ),
        _make_participant(
            "puuid-top-b", teamId=200, teamPosition="TOP", championName="Renekton", win=False
        ),
        _make_participant(
            "puuid-jgl-b", teamId=200, teamPosition="JUNGLE", championName="Sejuani", win=False
        ),
        _make_participant(
            "puuid-mid-b", teamId=200, teamPosition="MID", championName="Zed", win=False
        ),
        _make_participant(
            "puuid-bot-b", teamId=200, teamPosition="BOTTOM", championName="Caitlyn", win=False
        ),
        _make_participant(
            "puuid-sup-b", teamId=200, teamPosition="UTILITY", championName="Janna", win=False
        ),
    ]


class TestWriteMatchups:
    @pytest.mark.asyncio
    async def test_matchup_data_written_for_ranked(self, r, cfg, log):
        """Ranked match writes matchup hashes for each lane."""
        participants = _ranked_matchup_participants()
        info = {"queueId": 420, "participants": participants}
        await _write_matchups(r, "NA1_MU1", info, "14.1", cfg, log)

        # TOP: Garen vs Renekton
        data = await r.hgetall("matchup:Garen:Renekton:TOP:14.1")
        assert data["games"] == "1"
        assert data["wins"] == "1"  # Garen won

    @pytest.mark.asyncio
    async def test_matchup_reverse_recorded(self, r, cfg, log):
        """Both A-vs-B and B-vs-A matchup hashes are stored."""
        participants = _ranked_matchup_participants()
        info = {"queueId": 420, "participants": participants}
        await _write_matchups(r, "NA1_MU2", info, "14.1", cfg, log)

        # Forward: Garen vs Renekton
        fwd = await r.hgetall("matchup:Garen:Renekton:TOP:14.1")
        assert fwd["games"] == "1"
        assert fwd["wins"] == "1"

        # Reverse: Renekton vs Garen
        rev = await r.hgetall("matchup:Renekton:Garen:TOP:14.1")
        assert rev["games"] == "1"
        assert rev["wins"] == "0"  # Renekton lost

    @pytest.mark.asyncio
    async def test_matchup_index_populated(self, r, cfg, log):
        """matchup:index sets contain opponents."""
        participants = _ranked_matchup_participants()
        info = {"queueId": 420, "participants": participants}
        await _write_matchups(r, "NA1_MU3", info, "14.1", cfg, log)

        # Garen's index at TOP should contain Renekton
        members = await r.smembers("matchup:index:Garen:TOP:14.1")
        assert "Renekton" in members

        # Renekton's index at TOP should contain Garen
        members = await r.smembers("matchup:index:Renekton:TOP:14.1")
        assert "Garen" in members

    @pytest.mark.asyncio
    async def test_matchup_skipped_for_non_ranked(self, r, cfg, log):
        """Non-ranked match (queueId=450) does not write matchups."""
        participants = _ranked_matchup_participants()
        info = {"queueId": 450, "participants": participants}
        await _write_matchups(r, "NA1_MU_ARAM", info, "14.1", cfg, log)

        assert await r.exists("matchup:Garen:Renekton:TOP:14.1") == 0

    @pytest.mark.asyncio
    async def test_matchup_skipped_when_disabled(self, r, log, monkeypatch):
        """cfg.track_matchups=False skips matchup computation."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("TRACK_MATCHUPS", "false")
        disabled_cfg = Config(_env_file=None)  # type: ignore[call-arg]
        participants = _ranked_matchup_participants()
        info = {"queueId": 420, "participants": participants}
        await _write_matchups(r, "NA1_MU_OFF", info, "14.1", disabled_cfg, log)

        assert await r.exists("matchup:Garen:Renekton:TOP:14.1") == 0


def _make_timeline_data(participants_map):
    """Build a minimal timeline data dict with item and skill events.

    participants_map: list of {participantId, puuid}
    """
    return {
        "info": {
            "participants": participants_map,
            "frames": [
                {
                    "events": [
                        {
                            "type": "ITEM_PURCHASED",
                            "participantId": 1,
                            "itemId": 1001,
                            "timestamp": 60000,
                        },
                        {
                            "type": "ITEM_PURCHASED",
                            "participantId": 1,
                            "itemId": 3006,
                            "timestamp": 120000,
                        },
                        {
                            "type": "SKILL_LEVEL_UP",
                            "participantId": 1,
                            "skillSlot": 1,
                            "levelUpType": "NORMAL",
                            "timestamp": 90000,
                        },
                        {
                            "type": "SKILL_LEVEL_UP",
                            "participantId": 1,
                            "skillSlot": 2,
                            "levelUpType": "NORMAL",
                            "timestamp": 180000,
                        },
                        {
                            "type": "SKILL_LEVEL_UP",
                            "participantId": 1,
                            "skillSlot": 1,
                            "levelUpType": "EVOLVE",  # non-NORMAL, should be skipped
                            "timestamp": 200000,
                        },
                    ],
                },
            ],
        },
    }


class TestParseTimeline:
    @pytest.fixture
    def tl_cfg(self, monkeypatch):
        """Config with fetch_timeline=True for timeline tests."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("FETCH_TIMELINE", "true")
        return Config(_env_file=None)  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_timeline_build_order_extracted(self, r, tl_cfg, log):
        """Item purchase events stored as JSON list in build:{match_id}:{puuid}."""
        match_id = "NA1_TL1"
        timeline = _make_timeline_data([{"participantId": 1, "puuid": "puuid-tl-1"}])
        await r.set(f"raw:timeline:{match_id}", json.dumps(timeline))

        await _parse_timeline(r, match_id, tl_cfg, log)

        raw = await r.get(f"build:{match_id}:puuid-tl-1")
        assert raw is not None
        items = json.loads(raw)
        assert items == [1001, 3006]

    @pytest.mark.asyncio
    async def test_timeline_skill_order_extracted(self, r, tl_cfg, log):
        """Skill level-up events (NORMAL only) stored as JSON list."""
        match_id = "NA1_TL2"
        timeline = _make_timeline_data([{"participantId": 1, "puuid": "puuid-tl-2"}])
        await r.set(f"raw:timeline:{match_id}", json.dumps(timeline))

        await _parse_timeline(r, match_id, tl_cfg, log)

        raw = await r.get(f"skills:{match_id}:puuid-tl-2")
        assert raw is not None
        skills = json.loads(raw)
        assert skills == [1, 2]  # EVOLVE event excluded

    @pytest.mark.asyncio
    async def test_timeline_skipped_when_disabled(self, r, log, monkeypatch):
        """cfg.fetch_timeline=False skips timeline parsing."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("FETCH_TIMELINE", "false")
        disabled_cfg = Config(_env_file=None)  # type: ignore[call-arg]
        match_id = "NA1_TL_OFF"
        timeline = _make_timeline_data([{"participantId": 1, "puuid": "puuid-tl-off"}])
        await r.set(f"raw:timeline:{match_id}", json.dumps(timeline))

        await _parse_timeline(r, match_id, disabled_cfg, log)

        assert await r.exists(f"build:{match_id}:puuid-tl-off") == 0

    @pytest.mark.asyncio
    async def test_timeline_missing_data_handled(self, r, tl_cfg, log):
        """Missing raw:timeline key returns gracefully (no error)."""
        match_id = "NA1_TL_MISSING"
        # No raw:timeline:{match_id} key set

        await _parse_timeline(r, match_id, tl_cfg, log)

        # No crash, no keys written
        keys = [k async for k in r.scan_iter(f"build:{match_id}:*")]
        assert len(keys) == 0


class TestParsedSetTTL:
    """RDB-2: per-match hash status field replaces global match:status:parsed SET."""

    @pytest.mark.asyncio
    async def test_match_hash_status_set_on_first_parse(self, r, cfg, log):
        """First parse sets status=parsed in match:{id} hash via HSETNX."""
        raw_store = RawStore(r)
        match_id = "NA1_TTL_FIRST"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.hget(f"match:{match_id}", "status") == "parsed"
        # Per-match hash has a TTL from the match_data_ttl_seconds config
        ttl = await r.ttl(f"match:{match_id}")
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_global_parsed_set_not_used(self, r, cfg, log):
        """match:status:parsed global SET must not be written to."""
        raw_store = RawStore(r)
        match_id = "NA1_TTL_B"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert not await r.exists("match:status:parsed")


def _make_timeline_with_gold(participants_map, frames_data):
    """Build timeline data with participantFrames for gold extraction.

    frames_data: list of dicts mapping participant_id -> totalGold per frame.
    """
    frames = []
    for frame_gold in frames_data:
        pframes = {}
        for pid_str, gold in frame_gold.items():
            pframes[pid_str] = {"totalGold": gold}
        frames.append({"participantFrames": pframes, "events": []})
    return {
        "info": {
            "participants": participants_map,
            "frames": frames,
        },
    }


def _make_timeline_with_kills(participants_map, kill_events):
    """Build timeline data with CHAMPION_KILL events.

    kill_events: list of dicts with keys: killer, victim, assists (list of pid),
                 timestamp, x, y.
    """
    events = []
    for k in kill_events:
        event = {
            "type": "CHAMPION_KILL",
            "killerId": k["killer"],
            "victimId": k["victim"],
            "assistingParticipantIds": k.get("assists", []),
            "timestamp": k["timestamp"],
            "position": {"x": k.get("x", 0), "y": k.get("y", 0)},
        }
        events.append(event)
    frames = [{"events": events, "participantFrames": {}}]
    return {
        "info": {
            "participants": participants_map,
            "frames": frames,
        },
    }


class TestGoldTimeline:
    """T1-1: Gold timeline extraction from participantFrames."""

    @pytest.fixture
    def tl_cfg(self, monkeypatch):
        """Config with fetch_timeline=True for gold timeline tests."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("FETCH_TIMELINE", "true")
        return Config(_env_file=None)  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_gold_timeline__extraction__correct_values(self, r):
        """Gold timeline extracted as JSON integer array per participant."""
        frames_data = [
            {"1": 500, "2": 450},
            {"1": 1200, "2": 1100},
            {"1": 2500, "2": 2300},
        ]
        info = _make_timeline_with_gold(
            [{"participantId": 1, "puuid": "g1"}, {"participantId": 2, "puuid": "g2"}],
            frames_data,
        )["info"]
        result = _extract_gold_timelines(info.get("frames", []))
        assert result == {1: [500, 1200, 2500], 2: [450, 1100, 2300]}

    @pytest.mark.asyncio
    async def test_gold_timeline__cap_120_frames(self, r):
        """Gold timeline capped at 120 frames even if more exist."""
        frames_data = [{"1": i * 100} for i in range(150)]
        info = _make_timeline_with_gold(
            [{"participantId": 1, "puuid": "cap"}],
            frames_data,
        )["info"]
        result = _extract_gold_timelines(info.get("frames", []))
        assert len(result[1]) == 120
        assert result[1][0] == 0
        assert result[1][119] == 11900

    @pytest.mark.asyncio
    async def test_gold_timeline__stored_in_redis(self, r, tl_cfg, log):
        """Gold timeline stored as gold_timeline:{match_id}:{puuid} with TTL."""
        match_id = "NA1_GOLD1"
        timeline = _make_timeline_with_gold(
            [{"participantId": 1, "puuid": "puuid-gold-store"}],
            [{"1": 500}, {"1": 1200}],
        )
        await r.set(f"raw:timeline:{match_id}", json.dumps(timeline))

        await _parse_timeline(r, match_id, tl_cfg, log)

        raw = await r.get(f"gold_timeline:{match_id}:puuid-gold-store")
        assert raw is not None
        assert json.loads(raw) == [500, 1200]

    @pytest.mark.asyncio
    async def test_gold_timeline__empty_frames(self, r):
        """Empty frames list produces empty gold timelines."""
        result = _extract_gold_timelines([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_gold_timeline__skipped_when_disabled(self, r, log, monkeypatch):
        """FETCH_TIMELINE=false means no gold_timeline keys are written."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("FETCH_TIMELINE", "false")
        disabled_cfg = Config(_env_file=None)  # type: ignore[call-arg]
        match_id = "NA1_GOLD_OFF"
        timeline = _make_timeline_with_gold(
            [{"participantId": 1, "puuid": "puuid-gold-off"}],
            [{"1": 500}],
        )
        await r.set(f"raw:timeline:{match_id}", json.dumps(timeline))

        await _parse_timeline(r, match_id, disabled_cfg, log)

        assert await r.exists(f"gold_timeline:{match_id}:puuid-gold-off") == 0


class TestKillEvents:
    """T1-4: Kill event extraction from timeline frames."""

    @pytest.fixture
    def tl_cfg(self, monkeypatch):
        """Config with fetch_timeline=True for kill event tests."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("FETCH_TIMELINE", "true")
        return Config(_env_file=None)  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_kill_events__extraction__correct_format(self, r):
        """Kill events extracted with champion names, not participant IDs."""
        pid_to_champ = {1: "Ahri", 2: "Zed", 3: "Lux"}
        frames = [
            {
                "events": [
                    {
                        "type": "CHAMPION_KILL",
                        "killerId": 1,
                        "victimId": 2,
                        "assistingParticipantIds": [3],
                        "timestamp": 120000,
                        "position": {"x": 5000, "y": 6000},
                    },
                ],
                "participantFrames": {},
            },
        ]
        result = _extract_kill_events(frames, pid_to_champ)
        assert len(result) == 1
        assert result[0] == {
            "t": 120000,
            "killer": "Ahri",
            "victim": "Zed",
            "assists": ["Lux"],
            "x": 5000,
            "y": 6000,
        }

    @pytest.mark.asyncio
    async def test_kill_events__with_multiple_assists(self, r):
        """Kill events include all assist champion names."""
        pid_to_champ = {1: "Ahri", 2: "Zed", 3: "Lux", 4: "Thresh", 5: "Graves"}
        frames = [
            {
                "events": [
                    {
                        "type": "CHAMPION_KILL",
                        "killerId": 1,
                        "victimId": 2,
                        "assistingParticipantIds": [3, 4, 5],
                        "timestamp": 60000,
                        "position": {"x": 100, "y": 200},
                    },
                ],
                "participantFrames": {},
            },
        ]
        result = _extract_kill_events(frames, pid_to_champ)
        assert result[0]["assists"] == ["Lux", "Thresh", "Graves"]

    @pytest.mark.asyncio
    async def test_kill_events__cap_200(self, r):
        """Kill events capped at 200 even if more exist."""
        pid_to_champ = {1: "Ahri", 2: "Zed"}
        events = []
        for i in range(250):
            events.append(
                {
                    "type": "CHAMPION_KILL",
                    "killerId": 1,
                    "victimId": 2,
                    "assistingParticipantIds": [],
                    "timestamp": i * 1000,
                    "position": {"x": 0, "y": 0},
                },
            )
        frames = [{"events": events, "participantFrames": {}}]
        result = _extract_kill_events(frames, pid_to_champ)
        assert len(result) == 200

    @pytest.mark.asyncio
    async def test_kill_events__sorted_by_timestamp(self, r):
        """Kill events are sorted by timestamp ascending."""
        pid_to_champ = {1: "Ahri", 2: "Zed"}
        frames = [
            {
                "events": [
                    {
                        "type": "CHAMPION_KILL",
                        "killerId": 1,
                        "victimId": 2,
                        "assistingParticipantIds": [],
                        "timestamp": 300000,
                        "position": {"x": 0, "y": 0},
                    },
                ],
                "participantFrames": {},
            },
            {
                "events": [
                    {
                        "type": "CHAMPION_KILL",
                        "killerId": 2,
                        "victimId": 1,
                        "assistingParticipantIds": [],
                        "timestamp": 60000,
                        "position": {"x": 0, "y": 0},
                    },
                ],
                "participantFrames": {},
            },
        ]
        result = _extract_kill_events(frames, pid_to_champ)
        assert result[0]["t"] == 60000
        assert result[1]["t"] == 300000

    @pytest.mark.asyncio
    async def test_kill_events__empty_timeline(self, r):
        """Empty frames produces empty kill events list."""
        result = _extract_kill_events([], {})
        assert result == []

    @pytest.mark.asyncio
    async def test_kill_events__stored_in_redis(self, r, tl_cfg, log):
        """Kill events stored as kill_events:{match_id} with TTL."""
        match_id = "NA1_KILL1"
        participants = [
            {"participantId": 1, "puuid": "pk1", "championName": "Ahri"},
            {"participantId": 2, "puuid": "pk2", "championName": "Zed"},
        ]
        kill_events = [
            {"killer": 1, "victim": 2, "assists": [], "timestamp": 90000, "x": 100, "y": 200},
        ]
        timeline = _make_timeline_with_kills(participants, kill_events)
        await r.set(f"raw:timeline:{match_id}", json.dumps(timeline))

        await _parse_timeline(r, match_id, tl_cfg, log)

        raw = await r.get(f"kill_events:{match_id}")
        assert raw is not None
        events = json.loads(raw)
        assert len(events) == 1
        assert events[0]["killer"] == "Ahri"
        assert events[0]["victim"] == "Zed"

    @pytest.mark.asyncio
    async def test_kill_events__skipped_when_disabled(self, r, log, monkeypatch):
        """FETCH_TIMELINE=false means no kill_events keys written."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("FETCH_TIMELINE", "false")
        disabled_cfg = Config(_env_file=None)  # type: ignore[call-arg]
        match_id = "NA1_KILL_OFF"
        timeline = _make_timeline_with_kills(
            [
                {"participantId": 1, "puuid": "pk-off-1", "championName": "Ahri"},
                {"participantId": 2, "puuid": "pk-off-2", "championName": "Zed"},
            ],
            [{"killer": 1, "victim": 2, "assists": [], "timestamp": 90000, "x": 0, "y": 0}],
        )
        await r.set(f"raw:timeline:{match_id}", json.dumps(timeline))

        await _parse_timeline(r, match_id, disabled_cfg, log)

        assert await r.exists(f"kill_events:{match_id}") == 0

    @pytest.mark.asyncio
    async def test_kill_events__unknown_pid__fallback(self, r):
        """Unknown participant ID in kill event uses 'Unknown' fallback, no crash."""
        pid_to_champ = {1: "Ahri"}
        frames = [
            {
                "events": [
                    {
                        "type": "CHAMPION_KILL",
                        "killerId": 1,
                        "victimId": 99,  # unknown PID
                        "assistingParticipantIds": [88],  # unknown PID
                        "timestamp": 60000,
                        "position": {"x": 0, "y": 0},
                    },
                ],
                "participantFrames": {},
            },
        ]
        # Should not raise — uses "Unknown" fallback
        result = _extract_kill_events(frames, pid_to_champ)
        assert len(result) == 1
        assert result[0]["killer"] == "Ahri"
        assert result[0]["victim"] == "Unknown"
        assert result[0]["assists"] == ["Unknown"]


def _make_teams_with_objectives(
    blue_objectives=None,
    red_objectives=None,
    blue_first_blood=False,
    red_first_blood=False,
):
    """Build teams list with objectives for testing T1-2."""
    if blue_objectives is None:
        blue_objectives = {
            "baron": {"kills": 2, "first": True},
            "dragon": {"kills": 3, "first": True},
            "tower": {"kills": 8, "first": True},
            "inhibitor": {"kills": 2, "first": True},
            "riftHerald": {"kills": 1, "first": True},
        }
    if red_objectives is None:
        red_objectives = {
            "baron": {"kills": 1, "first": False},
            "dragon": {"kills": 1, "first": False},
            "tower": {"kills": 3, "first": False},
            "inhibitor": {"kills": 0, "first": False},
            "riftHerald": {"kills": 1, "first": False},
        }
    return [
        {
            "teamId": 100,
            "win": True,
            "objectives": {
                **blue_objectives,
                "champion": {"kills": 30, "first": blue_first_blood},
            },
        },
        {
            "teamId": 200,
            "win": False,
            "objectives": {
                **red_objectives,
                "champion": {"kills": 20, "first": red_first_blood},
            },
        },
    ]


class TestTeamObjectives:
    """T1-2: Team objectives extracted from info.teams[].objectives."""

    @pytest.mark.asyncio
    async def test_team_objectives__ranked_match(self, r, cfg, log):
        """Team objectives are written as fields on match:{match_id} hash."""
        raw_store = RawStore(r)
        match_id = "NA1_OBJ1"
        teams = _make_teams_with_objectives(blue_first_blood=True)
        participants = [
            _make_participant("puuid-obj-1", teamId=100, win=True),
            _make_participant("puuid-obj-2", teamId=200, win=False),
        ]
        data = _make_match_data(match_id, participants, teams=teams, queueId=420)
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"match:{match_id}")
        assert h["team_blue_barons"] == "2"
        assert h["team_blue_dragons"] == "3"
        assert h["team_blue_towers"] == "8"
        assert h["team_blue_inhibitors"] == "2"
        assert h["team_blue_heralds"] == "1"
        assert h["team_blue_first_blood"] == "1"
        assert h["team_red_barons"] == "1"
        assert h["team_red_dragons"] == "1"
        assert h["team_red_towers"] == "3"
        assert h["team_red_inhibitors"] == "0"
        assert h["team_red_heralds"] == "1"
        assert h["team_red_first_blood"] == "0"

    @pytest.mark.asyncio
    async def test_team_objectives__missing_objectives_block(self, r, cfg, log):
        """Teams without objectives block produce zero-value fields."""
        raw_store = RawStore(r)
        match_id = "NA1_OBJ2"
        teams = [
            {"teamId": 100, "win": True},
            {"teamId": 200, "win": False},
        ]
        participants = [
            _make_participant("puuid-obj-3", teamId=100, win=True),
        ]
        data = _make_match_data(match_id, participants, teams=teams)
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"match:{match_id}")
        assert h["team_blue_barons"] == "0"
        assert h["team_blue_dragons"] == "0"
        assert h["team_blue_towers"] == "0"
        assert h["team_blue_inhibitors"] == "0"
        assert h["team_blue_heralds"] == "0"
        assert h["team_blue_first_blood"] == "0"
        assert h["team_red_barons"] == "0"
        assert h["team_red_towers"] == "0"

    @pytest.mark.asyncio
    async def test_team_objectives__uses_team_id_not_array_index(self, r, cfg, log):
        """Mapping uses explicit teamId comparison, not array index."""
        raw_store = RawStore(r)
        match_id = "NA1_OBJ3"
        # Red team FIRST in array to prove index is not used
        teams = [
            {
                "teamId": 200,
                "win": False,
                "objectives": {
                    "baron": {"kills": 9, "first": False},
                    "dragon": {"kills": 8, "first": False},
                    "tower": {"kills": 7, "first": False},
                    "inhibitor": {"kills": 6, "first": False},
                    "riftHerald": {"kills": 5, "first": False},
                    "champion": {"kills": 0, "first": False},
                },
            },
            {
                "teamId": 100,
                "win": True,
                "objectives": {
                    "baron": {"kills": 1, "first": True},
                    "dragon": {"kills": 2, "first": True},
                    "tower": {"kills": 3, "first": True},
                    "inhibitor": {"kills": 4, "first": True},
                    "riftHerald": {"kills": 0, "first": True},
                    "champion": {"kills": 0, "first": True},
                },
            },
        ]
        participants = [
            _make_participant("puuid-obj-4", teamId=100, win=True),
        ]
        data = _make_match_data(match_id, participants, teams=teams)
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"match:{match_id}")
        # Blue (teamId=100) was second in array — must still map correctly
        assert h["team_blue_barons"] == "1"
        assert h["team_blue_dragons"] == "2"
        assert h["team_blue_towers"] == "3"
        assert h["team_blue_inhibitors"] == "4"
        assert h["team_blue_heralds"] == "0"
        assert h["team_blue_first_blood"] == "1"
        # Red (teamId=200) was first in array — must still map correctly
        assert h["team_red_barons"] == "9"
        assert h["team_red_dragons"] == "8"
        assert h["team_red_towers"] == "7"
        assert h["team_red_inhibitors"] == "6"
        assert h["team_red_heralds"] == "5"
        assert h["team_red_first_blood"] == "0"

    @pytest.mark.asyncio
    async def test_team_objectives__no_teams_key(self, r, cfg, log):
        """Match with no teams key still parses without error."""
        raw_store = RawStore(r)
        match_id = "NA1_OBJ4"
        participants = [
            _make_participant("puuid-obj-5", teamId=100, win=True),
        ]
        data = _make_match_data(match_id, participants)
        # _make_match_data does not add teams by default
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"match:{match_id}")
        assert h["team_blue_barons"] == "0"
        assert h["team_red_barons"] == "0"


class TestExtractFullPerks:
    """T1-3: _extract_full_perks returns perk selections + stat shards."""

    def test_full_perks__normal(self):
        """All rune selections and stat shards extracted correctly."""
        p = {
            "perks": {
                "styles": [
                    {
                        "style": 8100,
                        "selections": [
                            {"perk": 8112},
                            {"perk": 8126},
                            {"perk": 8139},
                            {"perk": 8135},
                        ],
                    },
                    {
                        "style": 8300,
                        "selections": [
                            {"perk": 8304},
                            {"perk": 8345},
                        ],
                    },
                ],
                "statPerks": {
                    "offense": 5008,
                    "flex": 5002,
                    "defense": 5001,
                },
            },
        }
        primary_sel, sub_sel, stat_shards = _extract_full_perks(p)
        assert primary_sel == [8112, 8126, 8139, 8135]
        assert sub_sel == [8304, 8345]
        assert stat_shards == [5008, 5002, 5001]

    def test_full_perks__empty_input(self):
        """Empty participant data returns empty arrays."""
        primary_sel, sub_sel, stat_shards = _extract_full_perks({})
        assert primary_sel == []
        assert sub_sel == []
        assert stat_shards == []

    def test_full_perks__no_perks_key(self):
        """Participant without styles/statPerks returns empty arrays."""
        primary_sel, sub_sel, stat_shards = _extract_full_perks({"perks": {}})
        assert primary_sel == []
        assert sub_sel == []
        assert stat_shards == []

    def test_full_perks__no_sub_style(self):
        """Only primary style present, no secondary."""
        p = {
            "perks": {
                "styles": [
                    {
                        "style": 8100,
                        "selections": [{"perk": 8112}, {"perk": 8126}],
                    },
                ],
            },
        }
        primary_sel, sub_sel, stat_shards = _extract_full_perks(p)
        assert primary_sel == [8112, 8126]
        assert sub_sel == []
        assert stat_shards == []

    def test_full_perks__partial_stat_perks(self):
        """StatPerks with only some fields present."""
        p = {
            "perks": {
                "styles": [],
                "statPerks": {
                    "offense": 5008,
                },
            },
        }
        primary_sel, sub_sel, stat_shards = _extract_full_perks(p)
        assert primary_sel == []
        assert sub_sel == []
        assert stat_shards == [5008]

    def test_full_perks__empty_selections(self):
        """Styles with empty selections arrays."""
        p = {
            "perks": {
                "styles": [
                    {"style": 8100, "selections": []},
                    {"style": 8300, "selections": []},
                ],
            },
        }
        primary_sel, sub_sel, stat_shards = _extract_full_perks(p)
        assert primary_sel == []
        assert sub_sel == []
        assert stat_shards == []


class TestFullPerksStoredAsJson:
    """T1-3: Full rune selections stored as JSON arrays on participant hash."""

    @pytest.mark.asyncio
    async def test_rune_selections__stored_as_json_arrays(self, r, cfg, log):
        """perk_primary/sub_selections, perk_stat_shards are JSON arrays."""
        raw_store = RawStore(r)
        match_id = "NA1_RUNES1"
        p = _make_participant(
            "puuid-runes",
            perks={
                "styles": [
                    {
                        "style": 8100,
                        "selections": [
                            {"perk": 8112},
                            {"perk": 8126},
                            {"perk": 8139},
                            {"perk": 8135},
                        ],
                    },
                    {
                        "style": 8300,
                        "selections": [
                            {"perk": 8304},
                            {"perk": 8345},
                        ],
                    },
                ],
                "statPerks": {
                    "offense": 5008,
                    "flex": 5002,
                    "defense": 5001,
                },
            },
        )
        data = _make_match_data(match_id, [p])
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"participant:{match_id}:puuid-runes")
        assert json.loads(h["perk_primary_selections"]) == [8112, 8126, 8139, 8135]
        assert json.loads(h["perk_sub_selections"]) == [8304, 8345]
        assert json.loads(h["perk_stat_shards"]) == [5008, 5002, 5001]

    @pytest.mark.asyncio
    async def test_rune_selections__empty_perks__empty_json_arrays(
        self,
        r,
        cfg,
        log,
    ):
        """Missing perks data stores empty JSON arrays, never omitted."""
        raw_store = RawStore(r)
        match_id = "NA1_RUNES2"
        p = _make_participant("puuid-norunes")
        data = _make_match_data(match_id, [p])
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"participant:{match_id}:puuid-norunes")
        assert h["perk_primary_selections"] == "[]"
        assert h["perk_sub_selections"] == "[]"
        assert h["perk_stat_shards"] == "[]"

    @pytest.mark.asyncio
    async def test_stat_shards__extracted_correctly(self, r, cfg, log):
        """Stat shards from statPerks stored as JSON array of 3 IDs."""
        raw_store = RawStore(r)
        match_id = "NA1_SHARDS"
        p = _make_participant(
            "puuid-shards",
            perks={
                "styles": [
                    {
                        "style": 8200,
                        "selections": [{"perk": 8214}],
                    },
                ],
                "statPerks": {
                    "offense": 5005,
                    "flex": 5003,
                    "defense": 5002,
                },
            },
        )
        data = _make_match_data(match_id, [p])
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, json.dumps(data))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        h = await r.hgetall(f"participant:{match_id}:puuid-shards")
        shards = json.loads(h["perk_stat_shards"])
        assert shards == [5005, 5003, 5002]
        assert len(shards) == 3


class TestRDB4_DiscoverPlayersTTL:
    """RDB-4: discover:players ZSET must get a 30-day safety-net TTL."""

    @pytest.mark.asyncio
    async def test_discover_players_has_ttl_after_parse(self, r, cfg, log):
        """After parsing a match, discover:players must have a TTL > 0."""
        raw_store = RawStore(r)
        match_id = "NA1_1234567890"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # discover:players must exist (10 co-players discovered)
        assert await r.zcard("discover:players") == 10
        # Must have a TTL (30 days = 2592000 seconds)
        ttl = await r.ttl("discover:players")
        assert ttl > 0, "discover:players must have a safety-net TTL"
        assert ttl <= 30 * 24 * 3600  # at most 30 days

    @pytest.mark.asyncio
    async def test_discover_players_ttl_not_reset_on_subsequent_parse(self, r, cfg, log):
        """TTL must not be reset on subsequent parses (only set when no TTL)."""
        raw_store = RawStore(r)

        # First parse — sets TTL
        match_id_1 = "NA1_DISC_TTL_A"
        env1 = _parse_envelope(match_id_1)
        msg_id_1 = await _setup_message(r, env1)
        await raw_store.set(match_id_1, _load_fixture("match_normal.json"))
        await _parse_match(r, raw_store, cfg, msg_id_1, env1, log)

        # Artificially lower the TTL to simulate time passing
        await r.expire("discover:players", 1000)

        # Second parse — should NOT reset TTL (already has one)
        match_id_2 = "NA1_DISC_TTL_B"
        env2 = _parse_envelope(match_id_2)
        msg_id_2 = await _setup_message(r, env2)
        await raw_store.set(match_id_2, _load_fixture("match_normal.json"))
        await _parse_match(r, raw_store, cfg, msg_id_2, env2, log)

        ttl = await r.ttl("discover:players")
        assert ttl <= 1000, "TTL must not be reset when it already exists"


class TestRDB2_NoGlobalParsedSet:
    """RDB-2: Parser must NOT use global match:status:parsed SET.

    Idempotency is enforced via the per-match hash field `match:{id}.status`
    (set by HSETNX), not by an unbounded global SET.
    """

    @pytest.mark.asyncio
    async def test_parsed_set_not_written(self, r, cfg, log):
        """After parsing, match:status:parsed SET must NOT contain the match_id."""
        raw_store = RawStore(r)
        match_id = "NA1_RDB2_SET"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert not await r.sismember("match:status:parsed", match_id), (
            "Parser must not write to global match:status:parsed SET (RDB-2)"
        )

    @pytest.mark.asyncio
    async def test_status_in_per_match_hash(self, r, cfg, log):
        """After parsing, match:{id} hash must have status=parsed."""
        raw_store = RawStore(r)
        match_id = "NA1_RDB2_HASH"
        env = _parse_envelope(match_id)
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        assert await r.hget(f"match:{match_id}", "status") == "parsed"

    @pytest.mark.asyncio
    async def test_idempotency_via_hsetnx(self, r, cfg, log):
        """Second parse must NOT re-run bans/matchups (HSETNX returns 0)."""
        raw_store = RawStore(r)
        match_id = "NA1_RDB2_IDEM"
        participants = [
            _make_participant(
                "puuid-rdb2-a",
                teamId=100,
                teamPosition="TOP",
                championName="Garen",
                win=True,
            ),
            _make_participant(
                "puuid-rdb2-b",
                teamId=200,
                teamPosition="TOP",
                championName="Renekton",
                win=False,
            ),
        ]
        data = _make_match_data(
            match_id,
            participants,
            queueId=420,
            teams=[
                {"teamId": 100, "bans": [{"championId": 238, "pickTurn": 1}]},
                {"teamId": 200, "bans": [{"championId": 67, "pickTurn": 2}]},
            ],
        )
        await raw_store.set(match_id, json.dumps(data))

        # First parse
        env1 = _parse_envelope(match_id)
        msg_id1 = await _setup_message(r, env1)
        await _parse_match(r, raw_store, cfg, msg_id1, env1, log)

        # Second parse
        env2 = _parse_envelope(match_id)
        msg_id2 = await _setup_message(r, env2)
        await _parse_match(r, raw_store, cfg, msg_id2, env2, log)

        # Bans counted once, not twice
        patch = "14.1"
        assert await r.hget(f"champion:bans:{patch}", "238") == "1"
        assert await r.hget(f"champion:bans:{patch}", "_total_games") == "1"

        # No global set
        assert not await r.exists("match:status:parsed")


class TestCorrelationIdPropagation:
    """Outbound analyze envelopes must propagate correlation_id from inbound."""

    @pytest.mark.asyncio
    async def test_parse__propagates_correlation_id_to_analyze_stream(self, r, cfg, log):
        """All stream:analyze envelopes carry the inbound correlation_id."""
        raw_store = RawStore(r)
        match_id = "NA1_CORR"
        env = MessageEnvelope(
            source_stream=_IN_STREAM,
            type="parse",
            payload={"match_id": match_id, "region": "na1"},
            max_attempts=5,
            correlation_id="trace-parser-001",
        )
        msg_id = await _setup_message(r, env)
        await raw_store.set(match_id, _load_fixture("match_normal.json"))

        await _parse_match(r, raw_store, cfg, msg_id, env, log)

        # Read all outbound messages from stream:analyze
        out_entries = await r.xrange(_OUT_STREAM)
        assert len(out_entries) >= 1
        for _entry_id, fields in out_entries:
            restored = MessageEnvelope.from_redis_fields(fields)
            assert restored.correlation_id == "trace-parser-001"
