"""Hypothesis property-based (fuzz) tests for MessageEnvelope and DLQEnvelope."""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from lol_pipeline.models import DLQEnvelope, MessageEnvelope

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Values that Redis HGETALL could return (always strings), plus adversarial types
_redis_str = st.text(min_size=0, max_size=200)
_redis_value = st.one_of(
    _redis_str,
    st.integers().map(str),
    st.just(""),
    st.just("null"),
    st.just("{}"),
    st.just("[]"),
    st.just('{"key": "value"}'),
)

# Value types that could appear due to bugs or type confusion (non-string)
_any_value = st.one_of(
    _redis_value,
    st.none(),
    st.integers(),
    st.binary(max_size=50).map(lambda b: b.decode("utf-8", errors="replace")),
)

_MSG_REQUIRED_KEYS = [
    "id",
    "source_stream",
    "type",
    "payload",
    "attempts",
    "max_attempts",
    "enqueued_at",
]

_DLQ_REQUIRED_KEYS = [
    "id",
    "source_stream",
    "type",
    "payload",
    "attempts",
    "max_attempts",
    "failure_code",
    "failed_at",
    "enqueued_at",
]

_EXPECTED_MSG_ERRORS = (KeyError, json.JSONDecodeError, ValueError, TypeError)
_EXPECTED_DLQ_ERRORS = (KeyError, json.JSONDecodeError, ValueError, TypeError)


# Strategy: generate a dict with random subsets of required keys + random values
@st.composite
def _random_msg_fields(draw: st.DrawFn) -> dict[str, str]:
    """Random subset of MessageEnvelope fields with random string values."""
    all_keys = [*_MSG_REQUIRED_KEYS, "dlq_attempts", "priority"]
    chosen_keys = draw(st.lists(st.sampled_from(all_keys), min_size=0, max_size=len(all_keys)))
    # Also allow totally random extra keys
    extra_keys = draw(st.lists(_redis_str, min_size=0, max_size=3))
    result: dict[str, str] = {}
    for k in chosen_keys + extra_keys:
        result[k] = draw(_redis_value)
    return result


@st.composite
def _random_dlq_fields(draw: st.DrawFn) -> dict[str, str]:
    """Random subset of DLQEnvelope fields with random string values."""
    all_keys = [
        *_DLQ_REQUIRED_KEYS,
        "failure_reason",
        "failed_by",
        "original_stream",
        "original_message_id",
        "retry_after_ms",
        "dlq_attempts",
        "priority",
    ]
    chosen_keys = draw(st.lists(st.sampled_from(all_keys), min_size=0, max_size=len(all_keys)))
    extra_keys = draw(st.lists(_redis_str, min_size=0, max_size=3))
    result: dict[str, str] = {}
    for k in chosen_keys + extra_keys:
        result[k] = draw(_redis_value)
    return result


# Strategy: generate a valid MessageEnvelope for round-trip testing
_json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=50),
)

_json_values = st.recursive(
    _json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)

_json_payload = st.dictionaries(st.text(max_size=20), _json_values, max_size=10)


@st.composite
def _valid_message_envelope(draw: st.DrawFn) -> MessageEnvelope:
    return MessageEnvelope(
        source_stream=draw(st.text(min_size=1, max_size=50)),
        type=draw(st.text(min_size=1, max_size=30)),
        payload=draw(_json_payload),
        max_attempts=draw(st.integers(min_value=0, max_value=1000)),
        attempts=draw(st.integers(min_value=-100, max_value=1000)),
        dlq_attempts=draw(st.integers(min_value=0, max_value=100)),
        priority=draw(st.sampled_from(["normal", "high"])),
    )


@st.composite
def _valid_dlq_envelope(draw: st.DrawFn) -> DLQEnvelope:
    return DLQEnvelope(
        source_stream=draw(st.text(min_size=1, max_size=50)),
        type=draw(st.text(min_size=1, max_size=30)),
        payload=draw(_json_payload),
        attempts=draw(st.integers(min_value=0, max_value=1000)),
        max_attempts=draw(st.integers(min_value=0, max_value=1000)),
        failure_code=draw(st.text(min_size=1, max_size=30)),
        failure_reason=draw(st.text(max_size=100)),
        failed_by=draw(st.text(max_size=30)),
        original_stream=draw(st.text(max_size=50)),
        original_message_id=draw(st.text(max_size=30)),
        retry_after_ms=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=2**31))),
        dlq_attempts=draw(st.integers(min_value=0, max_value=100)),
        priority=draw(st.sampled_from(["normal", "high"])),
    )


# ---------------------------------------------------------------------------
# MessageEnvelope fuzz tests
# ---------------------------------------------------------------------------


class TestMessageEnvelopeFuzz:
    @given(fields=_random_msg_fields())
    @settings(max_examples=200)
    def test_from_redis_fields__random_dict__no_unexpected_exception(
        self, fields: dict[str, str]
    ) -> None:
        """Deserializing random dicts either succeeds or raises expected errors."""
        try:
            env = MessageEnvelope.from_redis_fields(fields)
            # If it succeeded, verify it returned a valid envelope
            assert isinstance(env, MessageEnvelope)
            assert isinstance(env.source_stream, str)
            assert isinstance(env.type, str)
            assert isinstance(env.attempts, int)
            assert isinstance(env.max_attempts, int)
            assert isinstance(env.dlq_attempts, int)
            assert isinstance(env.priority, str)
        except _EXPECTED_MSG_ERRORS:
            pass  # Expected — these are the only allowed error types

    @given(env=_valid_message_envelope())
    @settings(max_examples=200)
    def test_round_trip__valid_envelope__identity(self, env: MessageEnvelope) -> None:
        """to_redis_fields -> from_redis_fields preserves all fields."""
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
        assert restored.priority == env.priority

    @given(
        fields=st.fixed_dictionaries(
            {
                "id": _redis_str,
                "source_stream": _redis_str,
                "type": _redis_str,
                "payload": st.just("{}"),
                "attempts": st.integers(min_value=-(2**31), max_value=2**31).map(str),
                "max_attempts": st.integers(min_value=-(2**31), max_value=2**31).map(str),
                "enqueued_at": _redis_str,
                "dlq_attempts": st.one_of(
                    st.just("not_a_number"),
                    st.just(""),
                    st.just("3.14"),
                    st.integers().map(str),
                ),
            }
        )
    )
    @settings(max_examples=100)
    def test_from_redis_fields__varied_dlq_attempts__expected_errors(
        self, fields: dict[str, str]
    ) -> None:
        """dlq_attempts with non-integer strings raises ValueError, not crashes."""
        try:
            env = MessageEnvelope.from_redis_fields(fields)
            assert isinstance(env.dlq_attempts, int)
        except _EXPECTED_MSG_ERRORS:
            pass


# ---------------------------------------------------------------------------
# DLQEnvelope fuzz tests
# ---------------------------------------------------------------------------


class TestDLQEnvelopeFuzz:
    @given(fields=_random_dlq_fields())
    @settings(max_examples=200)
    def test_from_redis_fields__random_dict__no_unexpected_exception(
        self, fields: dict[str, str]
    ) -> None:
        """Deserializing random dicts either succeeds or raises expected errors."""
        try:
            dlq = DLQEnvelope.from_redis_fields(fields)
            assert isinstance(dlq, DLQEnvelope)
            assert isinstance(dlq.failure_code, str)
            assert isinstance(dlq.dlq_attempts, int)
            assert dlq.retry_after_ms is None or isinstance(dlq.retry_after_ms, int)
        except _EXPECTED_DLQ_ERRORS:
            pass

    @given(env=_valid_dlq_envelope())
    @settings(max_examples=200)
    def test_round_trip__valid_dlq_envelope__identity(self, env: DLQEnvelope) -> None:
        """to_redis_fields -> from_redis_fields preserves all fields."""
        fields = env.to_redis_fields()
        restored = DLQEnvelope.from_redis_fields(fields)
        assert restored.id == env.id
        assert restored.source_stream == env.source_stream
        assert restored.type == env.type
        assert restored.payload == env.payload
        assert restored.attempts == env.attempts
        assert restored.max_attempts == env.max_attempts
        assert restored.failure_code == env.failure_code
        assert restored.failure_reason == env.failure_reason
        assert restored.failed_by == env.failed_by
        assert restored.original_stream == env.original_stream
        assert restored.original_message_id == env.original_message_id
        assert restored.failed_at == env.failed_at
        assert restored.enqueued_at == env.enqueued_at
        assert restored.retry_after_ms == env.retry_after_ms
        assert restored.dlq_attempts == env.dlq_attempts
        assert restored.priority == env.priority

    @given(
        retry_value=st.one_of(
            st.just("null"),
            st.just(""),
            st.just("not_a_number"),
            st.just("-1"),
            st.just("0"),
            st.just(str(2**63)),
            st.just(str(-(2**63))),
            st.just("3.14"),
            st.just("inf"),
            st.just("nan"),
            st.text(max_size=50),
        )
    )
    @settings(max_examples=100)
    def test_retry_after_ms__adversarial_values__expected_errors(self, retry_value: str) -> None:
        """retry_after_ms parsing with non-numeric/negative/overflow values."""
        fields = {
            "id": "test-id",
            "source_stream": "stream:dlq",
            "type": "dlq",
            "payload": "{}",
            "attempts": "1",
            "max_attempts": "5",
            "failure_code": "test",
            "failed_at": "2024-01-01T00:00:00+00:00",
            "enqueued_at": "2024-01-01T00:00:00+00:00",
            "retry_after_ms": retry_value,
        }
        try:
            dlq = DLQEnvelope.from_redis_fields(fields)
            # If it succeeded, verify the result is sane
            assert dlq.retry_after_ms is None or isinstance(dlq.retry_after_ms, int)
        except _EXPECTED_DLQ_ERRORS:
            pass

    @given(
        retry_ms=st.one_of(
            st.none(),
            st.integers(min_value=-(2**31), max_value=2**31),
        )
    )
    @settings(max_examples=50)
    def test_retry_after_ms__round_trip__preserves_value(self, retry_ms: int | None) -> None:
        """retry_after_ms round-trips correctly for None and integer values."""
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
            retry_after_ms=retry_ms,
        )
        fields = dlq.to_redis_fields()
        restored = DLQEnvelope.from_redis_fields(fields)
        assert restored.retry_after_ms == retry_ms

    @given(
        fields=st.fixed_dictionaries(
            {
                "id": _redis_str,
                "source_stream": _redis_str,
                "type": _redis_str,
                "payload": st.just("{}"),
                "attempts": st.integers(min_value=0, max_value=100).map(str),
                "max_attempts": st.integers(min_value=0, max_value=100).map(str),
                "failure_code": _redis_str,
                "failed_at": _redis_str,
                "enqueued_at": _redis_str,
            },
            optional={
                "failure_reason": _redis_str,
                "failed_by": _redis_str,
                "original_stream": _redis_str,
                "original_message_id": _redis_str,
                "retry_after_ms": _redis_str,
                "dlq_attempts": _redis_str,
                "priority": _redis_str,
                "unknown_extra_field": _redis_str,
            },
        )
    )
    @settings(max_examples=200)
    def test_from_redis_fields__with_optional_and_extra_keys__no_crash(
        self, fields: dict[str, str]
    ) -> None:
        """DLQ deserialization with optional/extra keys never crashes unexpectedly."""
        try:
            dlq = DLQEnvelope.from_redis_fields(fields)
            assert isinstance(dlq, DLQEnvelope)
        except _EXPECTED_DLQ_ERRORS:
            pass
