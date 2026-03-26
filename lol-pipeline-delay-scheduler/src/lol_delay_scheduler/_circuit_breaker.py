"""Circuit-breaker — per-member failure tracking for the Delay Scheduler."""

from __future__ import annotations

import logging
import time

from lol_pipeline.config import Config

_CONFIG_FIELDS = Config.model_fields


class CircuitBreakerState:
    """Encapsulates mutable circuit-breaker state for the Delay Scheduler."""

    def __init__(self, max_failures: int = 10, open_ttl_s: int = 300) -> None:
        self.member_failures: dict[str, int] = {}
        self.circuit_open: dict[str, float] = {}
        self.max_failures: int = max_failures
        self.open_ttl_s: int = open_ttl_s

    def configure(self, max_failures: int, open_ttl_s: int) -> None:
        """Update circuit-breaker thresholds from Config values."""
        self.max_failures = max_failures
        self.open_ttl_s = open_ttl_s

    def is_open(self, member: str) -> bool:
        """Return True if *member* is in the circuit-open set and TTL has not expired."""
        opened_at = self.circuit_open.get(member)
        if opened_at is None:
            return False
        if time.monotonic() - opened_at >= self.open_ttl_s:
            # TTL expired — allow a single retry; reset failure counter
            del self.circuit_open[member]
            self.member_failures.pop(member, None)
            return False
        return True

    def record_failure(self, member: str, log: logging.Logger) -> None:
        """Increment failure count; open circuit after max_failures."""
        count = self.member_failures.get(member, 0) + 1
        self.member_failures[member] = count
        if count >= self.max_failures:
            self.circuit_open[member] = time.monotonic()
            log.warning(
                "circuit opened for member after %d failures — skipping for %ds",
                count,
                self.open_ttl_s,
                extra={"member_preview": member[:80]},
            )

    def record_success(self, member: str) -> None:
        """Clear failure state on successful dispatch."""
        self.member_failures.pop(member, None)
        self.circuit_open.pop(member, None)


# Singleton instance — module-level state survives across ticks.
# Defaults sourced from Config (single source of truth); overridden at startup
# via init_circuit_config() when env vars customise the values.
_cb = CircuitBreakerState(
    max_failures=_CONFIG_FIELDS["delay_scheduler_max_member_failures"].default,
    open_ttl_s=_CONFIG_FIELDS["delay_scheduler_circuit_open_ttl_s"].default,
)

# Expose the underlying dicts so existing code (and tests) that reference
# _member_failures / _circuit_open by name can still read and mutate them.
_member_failures: dict[str, int] = _cb.member_failures
_circuit_open: dict[str, float] = _cb.circuit_open


def init_circuit_config(max_failures: int, open_ttl_s: int) -> None:
    """Seed circuit-breaker thresholds from Config values."""
    _cb.configure(max_failures, open_ttl_s)


def _is_circuit_open(member: str) -> bool:
    """Return True if *member* is in the circuit-open set and TTL has not expired."""
    return _cb.is_open(member)


def _record_failure(member: str, log: logging.Logger) -> None:
    """Increment failure count; open circuit after max_failures."""
    _cb.record_failure(member, log)


def _record_success(member: str) -> None:
    """Clear failure state on successful dispatch."""
    _cb.record_success(member)
