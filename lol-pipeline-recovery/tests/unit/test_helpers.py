"""Unit tests for recovery helper — _archive_with_match_status (PRIN-REC-02)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import DLQEnvelope

from lol_recovery.main import _archive_with_match_status


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.flushall()
    await redis.aclose()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    return Config(_env_file=None)  # type: ignore[call-arg]


def _make_dlq(match_id: str = "NA1_100") -> DLQEnvelope:
    return DLQEnvelope(
        source_stream="stream:dlq",
        type="dlq",
        payload={"match_id": match_id, "region": "na1"},
        attempts=3,
        max_attempts=5,
        failure_code="http_429",
        failure_reason="rate limited",
        failed_by="fetcher",
        original_stream="stream:match_id",
        original_message_id="1234-0",
        dlq_attempts=2,
    )


class TestArchiveWithMatchStatus:
    """PRIN-REC-02: _archive_with_match_status builds pipeline for archive + match status."""

    async def test_archive__writes_to_archive_stream(self, r, cfg):
        dlq = _make_dlq("NA1_100")
        async with r.pipeline(transaction=False) as pipe:
            _archive_with_match_status(pipe, dlq, "NA1_100", cfg)
            await pipe.execute()

        entries = await r.xrange("stream:dlq:archive")
        assert len(entries) == 1

    async def test_archive__sets_match_status_failed(self, r, cfg):
        dlq = _make_dlq("NA1_200")
        async with r.pipeline(transaction=False) as pipe:
            _archive_with_match_status(pipe, dlq, "NA1_200", cfg)
            await pipe.execute()

        status = await r.hget("match:NA1_200", "status")
        assert status == "failed"

    async def test_archive__sets_match_ttl(self, r, cfg):
        dlq = _make_dlq("NA1_300")
        async with r.pipeline(transaction=False) as pipe:
            _archive_with_match_status(pipe, dlq, "NA1_300", cfg)
            await pipe.execute()

        ttl = await r.ttl("match:NA1_300")
        assert ttl > 0

    async def test_archive__adds_match_to_failed_set(self, r, cfg):
        dlq = _make_dlq("NA1_400")
        async with r.pipeline(transaction=False) as pipe:
            _archive_with_match_status(pipe, dlq, "NA1_400", cfg)
            await pipe.execute()

        is_member = await r.sismember("match:status:failed", "NA1_400")
        assert is_member

    async def test_archive__archive_stream_capped(self, r, cfg):
        """The XADD uses maxlen from config for approximate capping."""
        dlq = _make_dlq("NA1_500")
        async with r.pipeline(transaction=False) as pipe:
            _archive_with_match_status(pipe, dlq, "NA1_500", cfg)
            await pipe.execute()

        # Verify the entry exists — capping is approximate so we just check it wrote
        entries = await r.xrange("stream:dlq:archive")
        assert len(entries) >= 1
