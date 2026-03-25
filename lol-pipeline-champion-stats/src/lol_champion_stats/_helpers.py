"""Champion stats helpers — validation, Redis key builders."""

from __future__ import annotations

from typing import NamedTuple

from lol_pipeline.constants import RANKED_SOLO_QUEUE_ID


class RankedContext(NamedTuple):
    """Validated ranked match context for a participant."""

    champion_name: str
    patch: str
    team_position: str


def _extract_ranked_context(
    participant: dict[str, str],
    match_meta: dict[str, str],
) -> RankedContext | None:
    """Extract and validate ranked context from participant + match metadata.

    Returns ``None`` when the match is not ranked solo/duo or required
    fields (patch, team_position, champion_name) are missing.
    """
    queue_id = match_meta.get("queue_id", "")
    patch = match_meta.get("patch", "")
    team_position = participant.get("team_position", "")
    champion_name = participant.get("champion_name", "")
    if queue_id != RANKED_SOLO_QUEUE_ID or not patch or not team_position or not champion_name:
        return None
    return RankedContext(
        champion_name=champion_name,
        patch=patch,
        team_position=team_position,
    )


def _stats_key(champion: str, patch: str, position: str) -> str:
    """Build Redis key for champion aggregate stats."""
    return f"champion:stats:{champion}:{patch}:{position}"


def _builds_key(champion: str, patch: str, position: str) -> str:
    """Build Redis key for champion build fingerprints."""
    return f"champion:builds:{champion}:{patch}:{position}"


def _runes_key(champion: str, patch: str, position: str) -> str:
    """Build Redis key for champion rune usage."""
    return f"champion:runes:{champion}:{patch}:{position}"


def _matchup_key(champion_a: str, champion_b: str, position: str, patch: str) -> str:
    """Build Redis key for head-to-head matchup stats.

    Champions are expected to be pre-sorted alphabetically by the caller.
    """
    return f"matchup:{champion_a}:{champion_b}:{position}:{patch}"
