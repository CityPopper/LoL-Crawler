"""Recovery constants — extracted from main.py."""

from __future__ import annotations

_IN_STREAM = "stream:dlq"
_ARCHIVE_STREAM = "stream:dlq:archive"
_DELAYED_KEY = "delayed:messages"
_GROUP = "recovery"
_CLAIM_IDLE_MS = 60_000

# Exponential backoff delays (ms) indexed by dlq_attempts.
_BACKOFF_MS = [5_000, 15_000, 60_000, 300_000]

# Status set TTL: 7 days (aligned with match data TTL).
_STATUS_TTL = 604800
