"""Circuit-breaker — per-member failure tracking for the Delay Scheduler."""

from __future__ import annotations

import logging
import time

# Per-member failure tracking (module-level, survives across ticks).
_member_failures: dict[str, int] = {}
# Circuit-open members: member -> time.monotonic() when circuit was opened.
_circuit_open: dict[str, float] = {}

# Circuit-breaker thresholds — set from Config at startup via init_circuit_config().
_MAX_MEMBER_FAILURES: int = 10
_CIRCUIT_OPEN_TTL_S: int = 300


def init_circuit_config(max_failures: int, open_ttl_s: int) -> None:
    """Seed module-level circuit-breaker thresholds from Config values."""
    global _MAX_MEMBER_FAILURES, _CIRCUIT_OPEN_TTL_S
    _MAX_MEMBER_FAILURES = max_failures
    _CIRCUIT_OPEN_TTL_S = open_ttl_s


def _is_circuit_open(member: str) -> bool:
    """Return True if *member* is in the circuit-open set and TTL has not expired."""
    opened_at = _circuit_open.get(member)
    if opened_at is None:
        return False
    if time.monotonic() - opened_at >= _CIRCUIT_OPEN_TTL_S:
        # TTL expired — allow a single retry; reset failure counter
        del _circuit_open[member]
        _member_failures.pop(member, None)
        return False
    return True


def _record_failure(member: str, log: logging.Logger) -> None:
    """Increment failure count; open circuit after _MAX_MEMBER_FAILURES."""
    count = _member_failures.get(member, 0) + 1
    _member_failures[member] = count
    if count >= _MAX_MEMBER_FAILURES:
        _circuit_open[member] = time.monotonic()
        log.warning(
            "circuit opened for member after %d failures — skipping for %ds",
            count,
            _CIRCUIT_OPEN_TTL_S,
            extra={"member_preview": member[:80]},
        )


def _record_success(member: str) -> None:
    """Clear failure state on successful dispatch."""
    _member_failures.pop(member, None)
    _circuit_open.pop(member, None)
