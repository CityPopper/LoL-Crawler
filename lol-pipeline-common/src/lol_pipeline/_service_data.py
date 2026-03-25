"""Service loop constants — extracted from service.py.

Values read from :class:`lol_pipeline.config.Config` when available,
with hardcoded defaults for test environments where required env vars
(``RIOT_API_KEY``, ``REDIS_URL``) may be absent.
"""

from __future__ import annotations

import logging

from lol_pipeline.config import Config

_log = logging.getLogger(__name__)


def _cfg_or_default(attr: str, default: int) -> int:
    """Read *attr* from Config if available, otherwise return *default*."""
    try:
        return int(getattr(Config(), attr))
    except Exception:
        _log.debug("Config() unavailable — using default for %s", attr)
        return default


_MAX_HANDLER_RETRIES: int = _cfg_or_default("max_handler_retries", 3)

# Maximum nack_to_dlq attempts before abandoning the message.
# When nack_to_dlq fails this many times (retry counter reaches
# max_retries + _MAX_NACK_ATTEMPTS), the message is logged and dropped
# to prevent unbounded retry counter growth.
_MAX_NACK_ATTEMPTS: int = _cfg_or_default("max_nack_attempts", 3)

# TTL for Redis-backed retry counters: 7 days.
_RETRY_KEY_TTL: int = _cfg_or_default("retry_key_ttl", 604800)

# Retry key format prefix.
_RETRY_KEY_PREFIX = "consumer:retry"
