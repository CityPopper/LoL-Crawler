"""Unit tests for lol_pipeline.models."""

from __future__ import annotations

import json
from pathlib import Path

from lol_pipeline.models import DLQEnvelope, MessageEnvelope


class TestMessageEnvelope:
    def test_to_redis_fields_returns_str_dict(self):
        env = MessageEnvelope(
            source_stream="stream:test",
            type="test",
            payload={"key": "value"},
            max_attempts=5,
        )
        fields = env.to_redis_fields()
        assert isinstance(fields, dict)
        for k, v in fields.items():
            assert isinstance(k, str)
            assert isinstance(v, str)

    def test_round_trip(self):
        env = MessageEnvelope(
            source_stream="stream:test",
            type="test",
            payload={"key": "value", "nested": [1, 2, 3]},
            max_attempts=5,
            attempts=2,
            dlq_attempts=1,
        )
        fields = env.to_redis_fields()
        restored = MessageEnvelope.from_redis_fields(fields)
        assert restored.id == env.id
        assert restored.source_stream == env.source_stream
        assert restored.type == env.type
        assert restored.payload == env.payload
        assert restored.attempts == env.attempts
        assert restored.max_attempts == env.max_attempts
        assert restored.enqueued_at == env.enqueued_at
        assert restored.dlq_attempts == env.dlq_attempts

    def test_defaults(self):
        env = MessageEnvelope(
            source_stream="s",
            type="t",
            payload={},
            max_attempts=3,
        )
        assert env.attempts == 0
        assert env.dlq_attempts == 0
        assert len(env.id) == 36  # UUID4 format
        assert env.enqueued_at  # non-empty

    def test_dlq_attempts_defaults_to_zero_on_deserialize(self):
        """Old messages without dlq_attempts field should default to 0."""
        fields = {
            "id": "abc",
            "source_stream": "stream:test",
            "type": "test",
            "payload": "{}",
            "attempts": "0",
            "max_attempts": "5",
            "enqueued_at": "2024-01-01T00:00:00+00:00",
        }
        env = MessageEnvelope.from_redis_fields(fields)
        assert env.dlq_attempts == 0


class TestDLQEnvelope:
    def test_to_redis_fields_includes_all_dlq_fields(self):
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_123"},
            attempts=3,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="1234-0",
            retry_after_ms=30000,
        )
        fields = dlq.to_redis_fields()
        assert fields["failure_code"] == "http_429"
        assert fields["failure_reason"] == "rate limited"
        assert fields["failed_by"] == "fetcher"
        assert fields["original_stream"] == "stream:match_id"
        assert fields["original_message_id"] == "1234-0"
        assert fields["retry_after_ms"] == "30000"

    def test_round_trip(self):
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"x": 1},
            attempts=2,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="999-0",
            retry_after_ms=None,
            dlq_attempts=2,
        )
        fields = dlq.to_redis_fields()
        restored = DLQEnvelope.from_redis_fields(fields)
        assert restored.failure_code == dlq.failure_code
        assert restored.original_stream == dlq.original_stream
        assert restored.retry_after_ms is None
        assert restored.dlq_attempts == 2

    def test_retry_after_ms_null_serialization(self):
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={},
            attempts=1,
            max_attempts=5,
            failure_code="http_404",
            failure_reason="not found",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="111-0",
            retry_after_ms=None,
        )
        fields = dlq.to_redis_fields()
        assert fields["retry_after_ms"] == "null"
        restored = DLQEnvelope.from_redis_fields(fields)
        assert restored.retry_after_ms is None


class TestMessageEnvelopeBoundary:
    def test_max_attempts_zero(self):
        env = MessageEnvelope(
            source_stream="s",
            type="t",
            payload={},
            max_attempts=0,
        )
        assert env.max_attempts == 0

    def test_large_payload(self):
        """Large payloads serialize and deserialize correctly."""
        big = {f"key_{i}": f"value_{i}" for i in range(100)}
        env = MessageEnvelope(source_stream="s", type="t", payload=big, max_attempts=5)
        restored = MessageEnvelope.from_redis_fields(env.to_redis_fields())
        assert restored.payload == big


class TestMessageEnvelopeNegativeAttempts:
    def test_negative_attempts_handled(self):
        """Negative attempts value should be preserved through round-trip."""
        env = MessageEnvelope(
            source_stream="s",
            type="t",
            payload={},
            max_attempts=5,
            attempts=-1,
        )
        fields = env.to_redis_fields()
        assert fields["attempts"] == "-1"
        restored = MessageEnvelope.from_redis_fields(fields)
        assert restored.attempts == -1


class TestDLQEnvelopeBoundary:
    def test_empty_failure_reason(self):
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={},
            attempts=1,
            max_attempts=5,
            failure_code="test",
            failure_reason="",
            failed_by="test",
            original_stream="stream:test",
            original_message_id="1-0",
        )
        fields = dlq.to_redis_fields()
        restored = DLQEnvelope.from_redis_fields(fields)
        assert restored.failure_reason == ""

    def test_missing_optional_fields_on_deserialize(self):
        """Old DLQ entries missing optional fields should use defaults."""
        fields = {
            "id": "abc",
            "source_stream": "stream:dlq",
            "type": "dlq",
            "payload": "{}",
            "attempts": "1",
            "max_attempts": "5",
            "failure_code": "http_429",
            "failed_at": "2024-01-01T00:00:00+00:00",
            "enqueued_at": "2024-01-01T00:00:00+00:00",
        }
        dlq = DLQEnvelope.from_redis_fields(fields)
        assert dlq.failure_reason == ""
        assert dlq.failed_by == ""
        assert dlq.original_stream == ""
        assert dlq.original_message_id == ""
        assert dlq.retry_after_ms is None
        assert dlq.dlq_attempts == 0


class TestEnvelopeSchemaIncludesDlqAttempts:
    """CQ-8: envelope.json must include dlq_attempts to match the dataclass."""

    def test_dlq_attempts_in_envelope_schema(self):
        """The envelope.json schema includes 'dlq_attempts' with type 'string' and default '0'."""
        schema_path = (
            Path(__file__).parent.parent.parent / "contracts" / "schemas" / "envelope.json"
        )
        schema = json.loads(schema_path.read_text())
        props = schema["properties"]
        assert "dlq_attempts" in props, "dlq_attempts missing from envelope.json properties"
        assert props["dlq_attempts"]["type"] == "string"
        assert props["dlq_attempts"]["default"] == "0"
