"""OpggMatchExtractor -- extract Riot-shaped match data from op.gg blobs.

The extractor calls ``_opgg_etl.normalize_game()`` on the raw op.gg blob,
then applies the transformer to patch missing fields required by the parser.
"""

from __future__ import annotations

from typing import Any

from lol_pipeline._opgg_etl import normalize_game
from lol_pipeline.sources.base import MATCH, DataType, ExtractionError
from lol_pipeline.sources.opgg.transformers import patch_riot_shape


class OpggMatchExtractor:
    """Extractor for op.gg raw game blobs.

    Expects the raw op.gg game dict (as returned by the op.gg API ``data[]``
    items), NOT the already-normalized output of ``normalize_game()``.

    The blob must contain at minimum an ``id`` field and a ``teams`` list
    to be considered extractable.
    """

    source_name = "opgg"
    data_types: frozenset[DataType] = frozenset({MATCH})

    def can_extract(self, blob: dict[str, Any]) -> bool:
        """Check that the blob has the raw op.gg game structure.

        Required keys:
        - ``id``: op.gg internal game ID (used as part of the match_id)
        - ``teams``: list of team dicts containing participants
        """
        return "id" in blob and isinstance(blob.get("teams"), list)

    def extract(self, blob: dict[str, Any], match_id: str, region: str) -> dict[str, Any]:
        """Normalize and transform an op.gg blob to canonical Riot match-v5 shape.

        Steps:
        1. ``normalize_game()`` maps raw op.gg fields to match-v5 structure
        2. ``patch_riot_shape()`` adds ``gameStartTimestamp`` and ``gameVersion``

        Raises ``ExtractionError`` on any normalization or transformation failure.
        """
        try:
            normalized = normalize_game(blob, region)
            normalized["metadata"]["match_id"] = match_id
            return patch_riot_shape(normalized)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ExtractionError(f"failed to extract op.gg blob for {match_id}: {exc}") from exc
