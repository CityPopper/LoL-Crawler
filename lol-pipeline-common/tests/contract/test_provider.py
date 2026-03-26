"""Provider contract tests: lol-pipeline-common satisfies Recovery and Delay Scheduler contracts.

Common is the provider for:
- DLQ envelopes (via nack_to_dlq in streams.py)   → consumed by Recovery
- Delayed message envelopes (via requeue_delayed)  → consumed by Delay Scheduler

Validates that:
- DLQEnvelope.to_redis_fields() / from_redis_fields() preserves all contracted fields.
- MessageEnvelope produced for delayed:messages round-trips cleanly.
- All failure_codes in the Recovery pact can be produced by DLQEnvelope.
"""

import uuid
from datetime import UTC, datetime

import pytest
from jsonschema import validate

from lol_pipeline.models import DLQEnvelope, MessageEnvelope

from .conftest import load_consumer_pact, load_schema

_FAILURE_CODES = [
    "http_429",
    "http_5xx",
    "http_404",
    "parse_error",
    "http_403",
    "handler_crash",
    "corrupt_message",
    "blob_validation_failed",
]


def _make_dlq_envelope(failure_code: str, retry_after_ms: int | None = None) -> DLQEnvelope:
    return DLQEnvelope(
        id=str(uuid.uuid4()),
        source_stream="stream:dlq",
        type="dlq",
        payload={"match_id": "NA1_4567890123", "region": "na1"},
        attempts=5,
        max_attempts=5,
        enqueued_at=datetime.now(tz=UTC).isoformat(),
        failure_reason=f"Test failure: {failure_code}",
        failure_code=failure_code,
        failed_at=datetime.now(tz=UTC).isoformat(),
        failed_by="fetcher",
        dlq_attempts=0,
        retry_after_ms=retry_after_ms,
        original_stream="stream:match_id",
        original_message_id="1704067200000-0",
    )


def _make_delayed_envelope() -> MessageEnvelope:
    return MessageEnvelope(
        id=str(uuid.uuid4()),
        source_stream="stream:match_id",
        type="match_id",
        payload={"match_id": "NA1_4567890123", "puuid": "test-puuid", "region": "na1"},
        attempts=1,
        max_attempts=5,
        enqueued_at=datetime.now(tz=UTC).isoformat(),
    )


@pytest.mark.parametrize("failure_code", _FAILURE_CODES)
def test_common__dlq_envelope__round_trips(failure_code):
    retry_ms = 61000 if failure_code == "http_429" else None
    envelope = _make_dlq_envelope(failure_code, retry_after_ms=retry_ms)
    fields = envelope.to_redis_fields()
    restored = DLQEnvelope.from_redis_fields(fields)
    assert restored.failure_code == failure_code
    assert restored.source_stream == "stream:dlq"
    assert restored.type == "dlq"
    assert restored.failed_by
    assert restored.original_stream
    assert restored.original_message_id
    assert isinstance(restored.dlq_attempts, int)
    assert isinstance(restored.correlation_id, str)
    assert isinstance(restored.priority, str)


def test_common__dlq_envelope__http_429__preserves_retry_after_ms():
    envelope = _make_dlq_envelope("http_429", retry_after_ms=61000)
    restored = DLQEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.retry_after_ms == 61000


@pytest.mark.parametrize("failure_code", ["http_5xx", "http_404", "parse_error", "http_403"])
def test_common__dlq_envelope__non_429__retry_after_ms_is_null(failure_code):
    envelope = _make_dlq_envelope(failure_code, retry_after_ms=None)
    restored = DLQEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.retry_after_ms is None


def test_common__dlq_envelope__satisfies_recovery_pact():
    pact = load_consumer_pact("recovery", "recovery-common.json")
    assert len(pact["messages"]) == len(_FAILURE_CODES)
    for message in pact["messages"]:
        contents = message["contents"]
        assert contents["source_stream"] == "stream:dlq"
        assert contents["type"] == "dlq"
        assert contents["failure_code"] in _FAILURE_CODES


def test_common__delayed_message__round_trips():
    envelope = _make_delayed_envelope()
    restored = MessageEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.source_stream == "stream:match_id"
    assert restored.type == "match_id"
    assert isinstance(restored.attempts, int)
    assert isinstance(restored.dlq_attempts, int)
    assert isinstance(restored.correlation_id, str)
    assert isinstance(restored.priority, str)


def test_common__delayed_message__satisfies_delay_scheduler_pact():
    pact = load_consumer_pact("delay-scheduler", "delay-scheduler-common.json")
    schema = load_schema("envelope.json")
    for message in pact["messages"]:
        contents = message["contents"]
        validate(instance=contents, schema=schema)
        assert contents["source_stream"].startswith("stream:")
        assert contents["type"] in {"puuid", "match_id", "parse", "analyze"}


# --- D3: Full envelope field validation via to_redis_fields() round-trip ---

_ENVELOPE_REDIS_KEYS = {
    "id",
    "source_stream",
    "type",
    "payload",
    "attempts",
    "max_attempts",
    "enqueued_at",
    "dlq_attempts",
    "priority",
    "correlation_id",
}

_DLQ_ENVELOPE_REDIS_KEYS = _ENVELOPE_REDIS_KEYS | {
    "failure_code",
    "failure_reason",
    "failed_at",
    "failed_by",
    "retry_after_ms",
    "original_stream",
    "original_message_id",
}


def test_common__envelope_to_redis_fields__contains_all_contracted_keys():
    """MessageEnvelope.to_redis_fields() must produce every field the schema requires."""
    envelope = _make_delayed_envelope()
    fields = envelope.to_redis_fields()
    assert set(fields.keys()) == _ENVELOPE_REDIS_KEYS


@pytest.mark.parametrize("failure_code", _FAILURE_CODES)
def test_common__dlq_envelope_to_redis_fields__contains_all_contracted_keys(
    failure_code,
):
    """DLQEnvelope.to_redis_fields() must produce every field the DLQ schema requires."""
    retry_ms = 61000 if failure_code == "http_429" else None
    envelope = _make_dlq_envelope(failure_code, retry_after_ms=retry_ms)
    fields = envelope.to_redis_fields()
    assert set(fields.keys()) == _DLQ_ENVELOPE_REDIS_KEYS


def test_common__dlq_envelope__round_trips_correlation_id():
    """Non-default correlation_id must survive DLQ round-trip."""
    envelope = _make_dlq_envelope("http_429", retry_after_ms=61000)
    envelope.correlation_id = "test-corr-id-abc"
    envelope.priority = "high"
    restored = DLQEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.correlation_id == "test-corr-id-abc"
    assert restored.priority == "high"


def test_common__delayed_message__round_trips_correlation_id():
    """Non-default correlation_id must survive MessageEnvelope round-trip."""
    envelope = _make_delayed_envelope()
    envelope.correlation_id = "test-corr-id-xyz"
    envelope.priority = "manual_20"
    restored = MessageEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.correlation_id == "test-corr-id-xyz"
    assert restored.priority == "manual_20"
