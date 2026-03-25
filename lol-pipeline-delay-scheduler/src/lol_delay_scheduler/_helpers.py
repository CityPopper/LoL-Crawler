"""Delay Scheduler helpers — pure utility functions extracted from main.py."""

from __future__ import annotations

from lol_delay_scheduler._constants import _STREAM_MAXLEN
from lol_pipeline.streams import DEFAULT_STREAM_MAXLEN


def _maxlen_for_stream(stream: str) -> int | None:
    """Return the maxlen policy for *stream* (None = no trimming)."""
    return _STREAM_MAXLEN.get(stream, DEFAULT_STREAM_MAXLEN)


def _is_envelope_id(member: str) -> bool:
    """Return True if *member* looks like a UUID envelope ID (not a JSON blob)."""
    # UUID v4: 36 chars, no braces/brackets.  JSON blobs start with '{'.
    return len(member) <= 40 and not member.startswith("{")
