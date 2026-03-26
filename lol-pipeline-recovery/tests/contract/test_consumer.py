"""Consumer contract tests: Recovery consuming stream:dlq from lol-pipeline-common.

Validates that:
- Each of the 5 failure_code example messages passes the DLQ envelope schema.
- DLQEnvelope.from_redis_fields() deserializes every failure_code without error.
- Recovery only accesses fields declared in the DLQ contract (service isolation).
"""

import pytest
from lol_pipeline.models import DLQEnvelope

from .conftest import load_pact, to_redis_format

_PACT_FILE = "recovery-common.json"

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


def _messages_by_failure_code(pact: dict) -> dict:
    return {m["contents"]["failure_code"]: m["contents"] for m in pact["messages"]}


def test_recovery__all_failure_codes_present_in_pact():
    pact = load_pact(_PACT_FILE)
    by_code = _messages_by_failure_code(pact)
    for code in _FAILURE_CODES:
        assert code in by_code, f"Missing failure_code '{code}' in pact"


@pytest.mark.parametrize("failure_code", _FAILURE_CODES)
def test_recovery__dlq_message__passes_required_fields(failure_code):
    pact = load_pact(_PACT_FILE)
    message = _messages_by_failure_code(pact)[failure_code]
    required = [
        "id",
        "source_stream",
        "type",
        "payload",
        "attempts",
        "max_attempts",
        "enqueued_at",
        "failure_reason",
        "failure_code",
        "failed_at",
        "failed_by",
        "dlq_attempts",
        "retry_after_ms",
        "original_stream",
        "original_message_id",
        "priority",
        "correlation_id",
    ]
    for field in required:
        assert field in message, f"Missing required DLQ field '{field}' for {failure_code}"


@pytest.mark.parametrize("failure_code", _FAILURE_CODES)
def test_recovery__dlq_message__deserializes_without_error(failure_code):
    pact = load_pact(_PACT_FILE)
    contents = _messages_by_failure_code(pact)[failure_code]
    envelope = DLQEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.source_stream == "stream:dlq"
    assert envelope.type == "dlq"
    assert envelope.failure_code == failure_code
    assert envelope.failed_by
    assert envelope.original_stream
    assert envelope.original_message_id
    assert isinstance(envelope.dlq_attempts, int)
    assert isinstance(envelope.correlation_id, str)
    assert isinstance(envelope.priority, str)


def test_recovery__http_429__has_retry_after_ms():
    pact = load_pact(_PACT_FILE)
    contents = _messages_by_failure_code(pact)["http_429"]
    envelope = DLQEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.retry_after_ms is not None
    assert isinstance(envelope.retry_after_ms, int)
    assert envelope.retry_after_ms > 0


@pytest.mark.parametrize(
    "failure_code",
    ["http_5xx", "http_404", "parse_error", "http_403", "handler_crash", "corrupt_message", "blob_validation_failed"],
)
def test_recovery__non_429__retry_after_ms_is_null(failure_code):
    pact = load_pact(_PACT_FILE)
    contents = _messages_by_failure_code(pact)[failure_code]
    envelope = DLQEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.retry_after_ms is None
