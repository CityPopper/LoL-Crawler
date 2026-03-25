"""Backward-compat shim — re-exports from ``_helpers.py``.

New code should import from ``lol_pipeline._helpers`` directly.
This module exists so that existing tests and third-party consumers
that import ``from lol_pipeline.helpers import ...`` continue to work.
"""

# Re-export stdlib modules so ``patch("lol_pipeline.helpers.socket")`` etc. still work.
import os  # noqa: F401
import socket  # noqa: F401

from lol_pipeline._helpers import (  # noqa: F401
    _CONTROL_CHAR_RE,
    _MAX_GAME_NAME_LEN,
    _MAX_SANITIZED_LEN,
    _MAX_TAG_LINE_LEN,
    _sanitize,
    consumer_id,
    handle_riot_api_error,
    is_system_halted,
    name_cache_key,
    register_player,
    validate_name_lengths,
)
