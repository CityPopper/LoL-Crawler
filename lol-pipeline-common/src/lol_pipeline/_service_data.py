"""Service loop constants — extracted from service.py."""

from __future__ import annotations

import os

_MAX_HANDLER_RETRIES = int(os.getenv("MAX_HANDLER_RETRIES", "3"))

# Maximum nack_to_dlq attempts before abandoning the message.
# When nack_to_dlq fails this many times (retry counter reaches
# max_retries + _MAX_NACK_ATTEMPTS), the message is logged and dropped
# to prevent unbounded retry counter growth.
_MAX_NACK_ATTEMPTS = 3

# TTL for Redis-backed retry counters: 7 days.
_RETRY_KEY_TTL = 604800

# Retry key format prefix.
_RETRY_KEY_PREFIX = "consumer:retry"
