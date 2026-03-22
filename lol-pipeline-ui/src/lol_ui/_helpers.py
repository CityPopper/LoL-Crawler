"""Shared UI helpers — small utilities used across multiple modules."""

from __future__ import annotations

import json


def _safe_int(value: str | None, default: int = 0) -> int:
    """Parse an integer from a string, returning *default* on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _safe_float(value: str | None, default: float = 0.0) -> float:
    """Parse a float from a string, returning *default* on failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _parse_item_ids(participant: dict[str, str], *, slots: int = 7) -> list[str]:
    """Parse item IDs from a participant hash, padded to *slots* entries.

    Handles both JSON arrays (``"[3006,3047,0,...]"``) and comma-separated
    strings (``"3006,3047,0,..."``) stored in the ``items`` field.

    Returns a list of string item IDs, always exactly *slots* long,
    padded with ``"0"`` for empty slots.
    """
    raw_items = participant.get("items", "")
    try:
        item_list = json.loads(raw_items) if raw_items.startswith("[") else raw_items.split(",")
    except (json.JSONDecodeError, AttributeError):
        item_list = []
    return (list(map(str, item_list)) + ["0"] * slots)[:slots]
