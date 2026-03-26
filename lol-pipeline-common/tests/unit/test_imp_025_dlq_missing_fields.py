"""IMP-025: DLQEnvelope.from_redis_fields() raises KeyError on missing required fields."""

from __future__ import annotations

import pytest

from lol_pipeline.models import DLQEnvelope


def _base_fields() -> dict[str, str]:
    """Return a complete set of DLQ fields for round-trip testing."""
    return {
        "id": "abc",
        "source_stream": "stream:dlq",
        "type": "dlq",
        "payload": "{}",
        "attempts": "1",
        "max_attempts": "5",
        "failure_code": "http_429",
        "failure_reason": "rate limited",
        "failed_by": "fetcher",
        "original_stream": "stream:match_id",
        "original_message_id": "123-0",
        "failed_at": "2024-01-01T00:00:00+00:00",
        "enqueued_at": "2024-01-01T00:00:00+00:00",
    }


class TestDLQMissingOriginalStream:
    def test_missing_original_stream_raises_key_error(self):
        """Dict missing 'original_stream' must raise KeyError."""
        fields = _base_fields()
        del fields["original_stream"]
        with pytest.raises(KeyError, match="original_stream"):
            DLQEnvelope.from_redis_fields(fields)

    def test_missing_original_message_id_raises_key_error(self):
        """Dict missing 'original_message_id' must raise KeyError."""
        fields = _base_fields()
        del fields["original_message_id"]
        with pytest.raises(KeyError, match="original_message_id"):
            DLQEnvelope.from_redis_fields(fields)

    def test_complete_fields_succeed(self):
        """Complete fields deserialize without error."""
        fields = _base_fields()
        dlq = DLQEnvelope.from_redis_fields(fields)
        assert dlq.original_stream == "stream:match_id"
        assert dlq.original_message_id == "123-0"
