"""Op.gg transformer -- bridge ETL output to canonical Riot match-v5 shape.

The existing ``_opgg_etl.normalize_game()`` produces ``gameCreation`` but
the parser requires ``gameStartTimestamp``.  This transformer patches the
gap without modifying the ETL module.

Additionally, ``gameVersion`` is absent from op.gg data.  The transformer
injects an empty string so the parser's ``_normalize_patch()`` degrades
gracefully instead of raising a KeyError.
"""

from __future__ import annotations

from typing import Any


def patch_riot_shape(normalized: dict[str, Any]) -> dict[str, Any]:
    """Patch a normalized op.gg match dict to satisfy parser requirements.

    Mutations applied to ``info``:
    - ``gameStartTimestamp`` = ``gameCreation`` (op.gg does not distinguish)
    - ``gameVersion`` = ``""`` if absent (op.gg does not provide patch info)

    Returns the same dict (mutated in-place) for convenience.
    """
    info: dict[str, Any] = normalized.get("info", {})

    # Critical fix: parser._validate() requires gameStartTimestamp
    if "gameStartTimestamp" not in info:
        info["gameStartTimestamp"] = info.get("gameCreation", 0)

    # Parser uses gameVersion for patch normalization; default to empty string
    if "gameVersion" not in info:
        info["gameVersion"] = ""

    return normalized
