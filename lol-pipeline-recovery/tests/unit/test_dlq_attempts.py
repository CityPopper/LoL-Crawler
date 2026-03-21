"""Test that Recovery increments dlq_attempts when requeuing to delayed:messages."""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest
from lol_pipeline.models import DLQEnvelope, MessageEnvelope

from lol_recovery.main import _requeue_delayed

_DELAYED_KEY = "delayed:messages"


@pytest.fixture
def r() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _make_dlq(dlq_attempts: int = 0) -> DLQEnvelope:
    return DLQEnvelope(
        source_stream="stream:dlq",
        type="dlq",
        payload={"match_id": "NA1_1234", "region": "na1"},
        attempts=3,
        max_attempts=5,
        failure_code="http_429",
        failure_reason="rate limited",
        failed_by="fetcher",
        original_stream="stream:match_id",
        original_message_id="1234-0",
        dlq_attempts=dlq_attempts,
    )


_DLQ_STREAM = "stream:dlq"
_GROUP = "recovery"


async def _setup_dlq_msg(r: fakeredis.aioredis.FakeRedis, dlq: DLQEnvelope) -> str:
    """Add a DLQ entry to stream:dlq and return msg_id."""
    msg_id: str = await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())
    try:
        await r.xgroup_create(_DLQ_STREAM, _GROUP, id="0", mkstream=True)
    except Exception:  # noqa: S110
        pass
    await r.xreadgroup(_GROUP, "test-consumer", {_DLQ_STREAM: ">"}, count=1)
    return msg_id


class TestDlqAttemptsIncrement:
    @pytest.mark.asyncio
    async def test_requeued_envelope_has_incremented_dlq_attempts(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Input: DLQEnvelope with dlq_attempts=1
        # After _requeue_delayed: the MessageEnvelope in delayed:messages should have dlq_attempts=2
        dlq = _make_dlq(dlq_attempts=1)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _requeue_delayed(r, dlq, delay_ms=5000, msg_id=msg_id)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        assert len(members) == 1
        fields = json.loads(members[0])
        env = MessageEnvelope.from_redis_fields(fields)
        assert env.dlq_attempts == 2

    @pytest.mark.asyncio
    async def test_first_requeue_sets_dlq_attempts_to_1(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Input: DLQEnvelope with dlq_attempts=0 (first recovery)
        # Output: requeued MessageEnvelope has dlq_attempts=1
        dlq = _make_dlq(dlq_attempts=0)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _requeue_delayed(r, dlq, delay_ms=5000, msg_id=msg_id)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        fields = json.loads(members[0])
        env = MessageEnvelope.from_redis_fields(fields)
        assert env.dlq_attempts == 1
