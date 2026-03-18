"""Unit tests for lol_admin.main — Phase 06 ACs 06-01 through 06-11."""

from __future__ import annotations

import argparse
import json

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.models import DLQEnvelope
from lol_pipeline.riot_api import RiotClient

from lol_admin.main import (
    _resolve_puuid,
    cmd_dlq_clear,
    cmd_dlq_list,
    cmd_dlq_replay,
    cmd_replay_fetch,
    cmd_replay_parse,
    cmd_reseed,
    cmd_stats,
    cmd_system_resume,
)

_DLQ_STREAM = "stream:dlq"


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


def _make_dlq(failure_code="http_429", match_id="NA1_123"):
    return DLQEnvelope(
        source_stream="stream:dlq",
        type="dlq",
        payload={"match_id": match_id, "region": "na1"},
        attempts=3,
        max_attempts=5,
        failure_code=failure_code,
        failure_reason="test",
        failed_by="fetcher",
        original_stream="stream:match_id",
        original_message_id="1234-0",
    )


async def _add_dlq_entries(r, count=1):
    """Add DLQ entries to stream:dlq, return list of entry IDs."""
    ids = []
    for i in range(count):
        dlq = _make_dlq(match_id=f"NA1_{i}")
        entry_id = await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())
        ids.append(entry_id)
    return ids


class TestDlqList:
    @pytest.mark.asyncio
    async def test_empty_dlq(self, r, capsys):
        """AC-06-01: dlq list empty → stdout '(empty)', exit 0."""
        args = argparse.Namespace()
        result = await cmd_dlq_list(r, args)
        assert result == 0
        assert "(empty)" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_three_entries(self, r, capsys):
        """AC-06-02: 3 DLQ entries → 3 records printed."""
        await _add_dlq_entries(r, 3)
        args = argparse.Namespace()
        result = await cmd_dlq_list(r, args)
        assert result == 0
        lines = [l for l in capsys.readouterr().out.strip().split("\n") if l]
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert "failure_code" in record
            assert "attempts" in record
            assert "enqueued_at" in record


class TestDlqReplay:
    @pytest.mark.asyncio
    async def test_replay_all(self, r, cfg):
        """AC-06-03: dlq replay --all → all XADDed; DLQ empty."""
        await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=True, id=None)
        result = await cmd_dlq_replay(r, cfg, args)
        assert result == 0
        assert await r.xlen(_DLQ_STREAM) == 0
        assert await r.xlen("stream:match_id") == 2

    @pytest.mark.asyncio
    async def test_replay_single(self, r, cfg):
        """AC-06-04: dlq replay <id> → only that entry replayed."""
        ids = await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=False, id=ids[0])
        result = await cmd_dlq_replay(r, cfg, args)
        assert result == 0
        assert await r.xlen(_DLQ_STREAM) == 1
        assert await r.xlen("stream:match_id") == 1


class TestDlqClear:
    @pytest.mark.asyncio
    async def test_clear_all(self, r):
        """AC-06-05: dlq clear --all → DLQ empty."""
        await _add_dlq_entries(r, 3)
        args = argparse.Namespace(all=True)
        result = await cmd_dlq_clear(r, args)
        assert result == 0
        assert await r.xlen(_DLQ_STREAM) == 0


class TestReplayParse:
    @pytest.mark.asyncio
    async def test_replay_parse_all(self, r, cfg):
        """AC-06-06: replay-parse --all with 5 entries → stream:parse += 5."""
        for i in range(5):
            await r.sadd("match:status:parsed", f"NA1_{i}")
        args = argparse.Namespace(all=True)
        result = await cmd_replay_parse(r, cfg, args)
        assert result == 0
        assert await r.xlen("stream:parse") == 5


class TestReplayFetch:
    @pytest.mark.asyncio
    async def test_replay_fetch(self, r, cfg):
        """AC-06-07: replay-fetch <match_id> → stream:match_id += 1."""
        args = argparse.Namespace(match_id="NA1_999")
        result = await cmd_replay_fetch(r, cfg, args)
        assert result == 0
        assert await r.xlen("stream:match_id") == 1


class TestReseed:
    @pytest.mark.asyncio
    async def test_reseed(self, r, cfg):
        """AC-06-08: reseed → clears cooldown; publishes to stream:puuid."""
        puuid = "test-puuid-0001"
        await r.hset(f"player:{puuid}", mapping={"seeded_at": "2024-01-01", "last_crawled_at": "2024-01-01"})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Faker", "tagLine": "KR1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Faker#KR1", region="na1")
            result = await cmd_reseed(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 1
        # Cooldown fields should be deleted
        assert await r.hget(f"player:{puuid}", "seeded_at") is None
        assert await r.hget(f"player:{puuid}", "last_crawled_at") is None


class TestSystemResume:
    @pytest.mark.asyncio
    async def test_system_resume(self, r, capsys):
        """AC-06-10: system-resume → system:halted deleted."""
        await r.set("system:halted", "1")
        args = argparse.Namespace()
        result = await cmd_system_resume(r, args)
        assert result == 0
        assert await r.exists("system:halted") == 0
        assert "resumed" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_system_resume_not_set(self, r):
        """AC-06-10b: system-resume when not set → exit 0; no error."""
        args = argparse.Namespace()
        result = await cmd_system_resume(r, args)
        assert result == 0


class TestResolvePuuidCache:
    @pytest.mark.asyncio
    async def test_cached_puuid_skips_api(self, r):
        """Cached player:name: key → no API call needed."""
        await r.set("player:name:faker#kr1", "cached-puuid")
        with respx.mock:
            # No routes — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, "Faker#KR1", "na1", r)
            await riot.close()
        assert result == "cached-puuid"


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_found(self, r, cfg, capsys):
        """AC-06-09: stats for existing player → prints fields."""
        puuid = "test-puuid-0001"
        await r.hset(f"player:stats:{puuid}", mapping={"total_games": "10", "win_rate": "0.6000"})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Faker", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Faker#NA1", region="na1")
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        output = capsys.readouterr().out
        assert "total_games" in output
        assert "win_rate" in output
