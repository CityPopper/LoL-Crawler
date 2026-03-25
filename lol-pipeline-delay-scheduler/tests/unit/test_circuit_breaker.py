"""Unit tests for CircuitBreakerState class (PRIN-DLY-02) and helpers (PRIN-DLY-03)."""

from __future__ import annotations

import logging
import time
from unittest.mock import patch

from lol_delay_scheduler._circuit_breaker import CircuitBreakerState
from lol_delay_scheduler._helpers import _is_envelope_id
from lol_delay_scheduler.main import _build_dispatch_args

log = logging.getLogger("test-circuit-breaker")


# -------------------------------------------------------------------------
# CircuitBreakerState — PRIN-DLY-02
# -------------------------------------------------------------------------


class TestCircuitBreakerStateInit:
    """CircuitBreakerState default initialization."""

    def test_init__default_max_failures(self):
        cb = CircuitBreakerState()
        assert cb.max_failures == 10

    def test_init__default_open_ttl(self):
        cb = CircuitBreakerState()
        assert cb.open_ttl_s == 300

    def test_init__custom_thresholds(self):
        cb = CircuitBreakerState(max_failures=5, open_ttl_s=60)
        assert cb.max_failures == 5
        assert cb.open_ttl_s == 60

    def test_init__empty_state(self):
        cb = CircuitBreakerState()
        assert cb.member_failures == {}
        assert cb.circuit_open == {}


class TestCircuitBreakerConfigure:
    """CircuitBreakerState.configure() updates thresholds."""

    def test_configure__updates_max_failures(self):
        cb = CircuitBreakerState()
        cb.configure(max_failures=20, open_ttl_s=600)
        assert cb.max_failures == 20

    def test_configure__updates_open_ttl(self):
        cb = CircuitBreakerState()
        cb.configure(max_failures=20, open_ttl_s=600)
        assert cb.open_ttl_s == 600


class TestCircuitBreakerIsOpen:
    """CircuitBreakerState.is_open() checks circuit state with TTL expiry."""

    def test_is_open__no_failure__returns_false(self):
        cb = CircuitBreakerState()
        assert cb.is_open("member-a") is False

    def test_is_open__circuit_opened__returns_true(self):
        cb = CircuitBreakerState(max_failures=2)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        assert cb.is_open("member-a") is True

    def test_is_open__ttl_expired__returns_false(self):
        cb = CircuitBreakerState(max_failures=2, open_ttl_s=1)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        assert cb.is_open("member-a") is True

        # Simulate TTL expiry
        with patch.object(time, "monotonic", return_value=time.monotonic() + 2):
            assert cb.is_open("member-a") is False

    def test_is_open__ttl_expired__clears_failure_state(self):
        cb = CircuitBreakerState(max_failures=2, open_ttl_s=1)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)

        with patch.object(time, "monotonic", return_value=time.monotonic() + 2):
            cb.is_open("member-a")  # triggers cleanup

        assert "member-a" not in cb.member_failures
        assert "member-a" not in cb.circuit_open

    def test_is_open__different_member__independent(self):
        cb = CircuitBreakerState(max_failures=2)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        assert cb.is_open("member-a") is True
        assert cb.is_open("member-b") is False


class TestCircuitBreakerRecordFailure:
    """CircuitBreakerState.record_failure() increments count and opens circuit."""

    def test_record_failure__increments_count(self):
        cb = CircuitBreakerState(max_failures=5)
        cb.record_failure("member-a", log)
        assert cb.member_failures["member-a"] == 1

    def test_record_failure__multiple_increments(self):
        cb = CircuitBreakerState(max_failures=5)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        assert cb.member_failures["member-a"] == 3

    def test_record_failure__opens_circuit_at_threshold(self):
        cb = CircuitBreakerState(max_failures=3)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        assert cb.is_open("member-a") is False
        cb.record_failure("member-a", log)  # 3rd failure = threshold
        assert cb.is_open("member-a") is True

    def test_record_failure__does_not_open_below_threshold(self):
        cb = CircuitBreakerState(max_failures=5)
        for _ in range(4):
            cb.record_failure("member-a", log)
        assert cb.is_open("member-a") is False


class TestCircuitBreakerRecordSuccess:
    """CircuitBreakerState.record_success() clears all failure state."""

    def test_record_success__clears_failure_count(self):
        cb = CircuitBreakerState(max_failures=5)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        cb.record_success("member-a")
        assert "member-a" not in cb.member_failures

    def test_record_success__clears_open_circuit(self):
        cb = CircuitBreakerState(max_failures=2)
        cb.record_failure("member-a", log)
        cb.record_failure("member-a", log)
        assert cb.is_open("member-a") is True
        cb.record_success("member-a")
        assert cb.is_open("member-a") is False

    def test_record_success__no_prior_state__noop(self):
        cb = CircuitBreakerState()
        cb.record_success("nonexistent")
        assert "nonexistent" not in cb.member_failures


class TestCircuitBreakerTransitions:
    """Full state transition sequences for CircuitBreakerState."""

    def test_closed_to_open_to_half_open_to_closed(self):
        """Closed -> failures open -> TTL expires -> half-open -> success closes."""
        cb = CircuitBreakerState(max_failures=2, open_ttl_s=1)

        # Closed: no failures
        assert cb.is_open("m") is False

        # Accumulate failures -> Open
        cb.record_failure("m", log)
        cb.record_failure("m", log)
        assert cb.is_open("m") is True

        # TTL expires -> Half-open (is_open returns False, allows retry)
        with patch.object(time, "monotonic", return_value=time.monotonic() + 2):
            assert cb.is_open("m") is False

        # Success on retry -> Closed
        cb.record_success("m")
        assert cb.is_open("m") is False
        assert "m" not in cb.member_failures

    def test_half_open_failure_reopens_circuit(self):
        """After TTL expires and retry fails, circuit reopens."""
        cb = CircuitBreakerState(max_failures=2, open_ttl_s=1)

        # Open the circuit
        cb.record_failure("m", log)
        cb.record_failure("m", log)

        # TTL expires (is_open clears state)
        with patch.object(time, "monotonic", return_value=time.monotonic() + 2):
            cb.is_open("m")

        # Retry fails again -> accumulate back to threshold
        cb.record_failure("m", log)
        cb.record_failure("m", log)
        assert cb.is_open("m") is True


# -------------------------------------------------------------------------
# _is_envelope_id — delay-scheduler helper
# -------------------------------------------------------------------------


class TestIsEnvelopeId:
    """_is_envelope_id distinguishes UUID-style IDs from JSON blobs."""

    def test_uuid_format__returns_true(self):
        assert _is_envelope_id("550e8400-e29b-41d4-a716-446655440000") is True

    def test_short_id__returns_true(self):
        assert _is_envelope_id("abc-123") is True

    def test_json_blob__returns_false(self):
        assert _is_envelope_id('{"id": "abc", "type": "test"}') is False

    def test_empty_string__returns_true(self):
        # Empty string is <= 40 chars and doesn't start with '{'
        assert _is_envelope_id("") is True

    def test_long_non_json__returns_false(self):
        assert _is_envelope_id("a" * 41) is False


# -------------------------------------------------------------------------
# _build_dispatch_args — PRIN-DLY-03
# -------------------------------------------------------------------------


class TestBuildDispatchArgs:
    """PRIN-DLY-03: _build_dispatch_args builds flat ARGV for Lua dispatch."""

    def test_build_dispatch_args__basic(self):
        fields = {"type": "match_id", "payload": "data"}
        args = _build_dispatch_args("member-1", 10000, fields)
        assert args[0] == "member-1"
        assert args[1] == "10000"
        # Remaining pairs: type, match_id, payload, data
        assert "type" in args
        assert "match_id" in args

    def test_build_dispatch_args__no_maxlen(self):
        fields = {"key": "val"}
        args = _build_dispatch_args("m", None, fields)
        assert args[1] == "0"

    def test_build_dispatch_args__empty_fields(self):
        args = _build_dispatch_args("m", 100, {})
        assert args == ["m", "100"]

    def test_build_dispatch_args__field_order_preserved(self):
        fields = {"a": "1", "b": "2", "c": "3"}
        args = _build_dispatch_args("m", 50, fields)
        # After member and maxlen, pairs should be a,1,b,2,c,3
        pairs = args[2:]
        assert pairs == ["a", "1", "b", "2", "c", "3"]

    def test_build_dispatch_args__values_are_strings(self):
        fields = {"count": "42"}
        args = _build_dispatch_args("m", 0, fields)
        for arg in args:
            assert isinstance(arg, str)
