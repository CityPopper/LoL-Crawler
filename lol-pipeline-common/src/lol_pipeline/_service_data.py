"""Service loop constants — extracted from service.py."""

from __future__ import annotations

import os

_MAX_HANDLER_RETRIES = int(os.getenv("MAX_HANDLER_RETRIES", "3"))

# TTL for Redis-backed retry counters: 7 days.
_RETRY_KEY_TTL = 604800

# Retry key format prefix.
_RETRY_KEY_PREFIX = "consumer:retry"
