"""IT-11 — DLQ round-trip preserves priority field."""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from helpers import MATCH_ID, PUUID, REGION, tlog
from lol_pipeline.constants import STREAM_DLQ
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.streams import nack_to_dlq


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dlq_round_trip__preserves_priority(r: aioredis.Redis) -> None:
    """nack_to_dlq preserves priority='high' in the DLQEnvelope on stream:dlq."""
    tlog("it11")

    # Create an envelope with high priority
    env = MessageEnvelope(
        source_stream="stream:match_id",
        type="match_id",
        payload={"match_id": MATCH_ID, "puuid": PUUID, "region": REGION},
        max_attempts=3,
        dlq_attempts=0,
        priority="high",
    )

    # Nack to DLQ with a retry_after_ms
    await nack_to_dlq(
        r,
        envelope=env,
        failure_code="RATE_LIMITED",
        failed_by="fetcher",
        original_message_id="1234-0",
        failure_reason="429 Too Many Requests",
        retry_after_ms=5000,
    )

    # Read from stream:dlq
    entries = await r.xrange(STREAM_DLQ)
    assert len(entries) == 1

    _dlq_id, fields = entries[0]
    dlq = DLQEnvelope.from_redis_fields(fields)

    # Priority is preserved
    assert dlq.priority == "high"

    # dlq_attempts is preserved from the original envelope (0)
    assert dlq.dlq_attempts == 0

    # retry_after_ms is set
    assert dlq.retry_after_ms == 5000

    # Other fields are correct
    assert dlq.failure_code == "RATE_LIMITED"
    assert dlq.failed_by == "fetcher"
    assert dlq.original_stream == "stream:match_id"
    assert dlq.original_message_id == "1234-0"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dlq_round_trip__normal_priority_default(r: aioredis.Redis) -> None:
    """nack_to_dlq with default priority='normal' also round-trips correctly."""
    tlog("it11")

    env = MessageEnvelope(
        source_stream="stream:parse",
        type="parse",
        payload={"match_id": MATCH_ID},
        max_attempts=3,
        priority="normal",
    )

    await nack_to_dlq(
        r,
        envelope=env,
        failure_code="PARSE_ERROR",
        failed_by="parser",
        original_message_id="5678-0",
    )

    entries = await r.xrange(STREAM_DLQ)
    assert len(entries) == 1

    _dlq_id, fields = entries[0]
    dlq = DLQEnvelope.from_redis_fields(fields)

    assert dlq.priority == "normal"
    assert dlq.retry_after_ms is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dlq_round_trip__payload_preserved(r: aioredis.Redis) -> None:
    """nack_to_dlq preserves the full payload through serialization."""
    tlog("it11")

    original_payload = {
        "match_id": MATCH_ID,
        "puuid": PUUID,
        "region": REGION,
        "extra": "data",
    }
    env = MessageEnvelope(
        source_stream="stream:match_id",
        type="match_id",
        payload=original_payload,
        max_attempts=5,
        attempts=2,
        dlq_attempts=1,
        priority="high",
    )

    await nack_to_dlq(
        r,
        envelope=env,
        failure_code="SERVER_ERROR",
        failed_by="fetcher",
        original_message_id="9999-0",
        retry_after_ms=10000,
    )

    entries = await r.xrange(STREAM_DLQ)
    assert len(entries) == 1

    _dlq_id, fields = entries[0]
    dlq = DLQEnvelope.from_redis_fields(fields)

    assert dlq.payload == original_payload
    assert dlq.attempts == 2
    assert dlq.max_attempts == 5
    assert dlq.dlq_attempts == 1
    assert dlq.priority == "high"
    assert dlq.retry_after_ms == 10000
