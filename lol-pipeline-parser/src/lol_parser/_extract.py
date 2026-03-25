"""Extraction functions — pure data transformations from match/timeline JSON."""

from __future__ import annotations

from typing import Any

from lol_parser._data import _GOLD_TIMELINE_MAX_FRAMES, _KILL_EVENTS_MAX


def _normalize_patch(game_version: str) -> str:
    """Extract major.minor from game version (e.g. '13.24.1' -> '13.24')."""
    parts = game_version.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return game_version


def _parse_perk_styles(
    p: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Parse perks.styles into (primary_style, sub_style, stat_perks) dicts."""
    perks = p.get("perks", {})
    styles = perks.get("styles", [])
    primary_style = styles[0] if styles else {}
    sub_style = styles[1] if len(styles) > 1 else {}
    return primary_style, sub_style, perks.get("statPerks", {})


def _extract_perks(p: dict[str, Any]) -> tuple[int, int, int]:
    """Return (keystone_id, primary_style_id, sub_style_id) from participant perks."""
    primary_style, sub_style, _ = _parse_perk_styles(p)
    selections = primary_style.get("selections", [])
    keystone = selections[0].get("perk", 0) if selections else 0
    return keystone, primary_style.get("style", 0), sub_style.get("style", 0)


def _extract_full_perks(
    p: dict[str, Any],
) -> tuple[list[int], list[int], list[int]]:
    """Return (primary_selections, sub_selections, stat_shards) from participant perks.

    Extracts available elements without asserting exact array lengths.
    Returns empty lists for any missing data.
    """
    primary_style, sub_style, stat_perks = _parse_perk_styles(p)
    primary_sel = [s.get("perk", 0) for s in primary_style.get("selections", [])]
    sub_sel = [s.get("perk", 0) for s in sub_style.get("selections", [])]
    stat_shards: list[int] = []
    for key in ("offense", "flex", "defense"):
        if key in stat_perks:
            stat_shards.append(stat_perks[key])
    return primary_sel, sub_sel, stat_shards


def _extract_all_perks(
    p: dict[str, Any],
) -> tuple[int, int, int, list[int], list[int], list[int]]:
    """Extract all perk data in a single pass.

    Returns (keystone, primary_style_id, sub_style_id,
             primary_selections, sub_selections, stat_shards).
    """
    primary_style, sub_style, stat_perks = _parse_perk_styles(p)
    selections = primary_style.get("selections", [])
    keystone = selections[0].get("perk", 0) if selections else 0
    primary_sel = [s.get("perk", 0) for s in selections]
    sub_sel = [s.get("perk", 0) for s in sub_style.get("selections", [])]
    stat_shards: list[int] = []
    for key in ("offense", "flex", "defense"):
        if key in stat_perks:
            stat_shards.append(stat_perks[key])
    return (
        keystone,
        primary_style.get("style", 0),
        sub_style.get("style", 0),
        primary_sel,
        sub_sel,
        stat_shards,
    )


def _extract_team_objectives(info: dict[str, Any]) -> dict[str, str]:
    """Extract team objective fields from info.teams[], keyed by teamId (100/200).

    Maps via explicit teamId comparison (100=blue, 200=red), NOT array index.
    Returns a flat dict of string fields ready for HSET on match:{match_id}.
    """
    teams = info.get("teams", [])
    team_map: dict[int, dict[str, Any]] = {}
    for team in teams:
        tid = team.get("teamId", 0)
        if tid in (100, 200):
            team_map[tid] = team.get("objectives", {})

    result: dict[str, str] = {}
    for tid, prefix in ((100, "team_blue"), (200, "team_red")):
        obj = team_map.get(tid, {})
        result[f"{prefix}_dragons"] = str(obj.get("dragon", {}).get("kills", 0))
        result[f"{prefix}_barons"] = str(obj.get("baron", {}).get("kills", 0))
        result[f"{prefix}_towers"] = str(obj.get("tower", {}).get("kills", 0))
        result[f"{prefix}_inhibitors"] = str(obj.get("inhibitor", {}).get("kills", 0))
        result[f"{prefix}_heralds"] = str(obj.get("riftHerald", {}).get("kills", 0))
        champion_obj = obj.get("champion", {})
        result[f"{prefix}_first_blood"] = "1" if champion_obj.get("first") else "0"
    return result


def _extract_timeline_events(
    frames: list[dict[str, Any]],
) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """Extract build and skill orders from timeline frames."""
    build_orders: dict[int, list[int]] = {}
    skill_orders: dict[int, list[int]] = {}
    for frame in frames:
        for event in frame.get("events", []):
            event_type = event.get("type", "")
            pid = event.get("participantId", 0)
            if not pid:
                continue
            if event_type == "ITEM_PURCHASED":
                build_orders.setdefault(pid, []).append(
                    event.get("itemId", 0),
                )
            elif event_type == "SKILL_LEVEL_UP" and event.get("levelUpType") == "NORMAL":
                skill_orders.setdefault(pid, []).append(
                    event.get("skillSlot", 0),
                )
    return build_orders, skill_orders


def _extract_gold_timelines(
    frames: list[dict[str, Any]],
) -> dict[int, list[int]]:
    """Extract per-participant gold totals from timeline participantFrames.

    Returns a dict mapping participant ID (int) to a list of totalGold values,
    one per frame, capped at 120 frames.
    """
    gold: dict[int, list[int]] = {}
    for frame in frames[:_GOLD_TIMELINE_MAX_FRAMES]:
        pframes = frame.get("participantFrames", {})
        for pid_str, pdata in pframes.items():
            try:
                pid = int(pid_str)
            except (ValueError, TypeError):
                continue
            gold.setdefault(pid, []).append(pdata.get("totalGold", 0))
    return gold


def _extract_kill_events(
    frames: list[dict[str, Any]],
    pid_to_champ: dict[int, str],
) -> list[dict[str, Any]]:
    """Extract CHAMPION_KILL events from timeline, denormalized with champion names.

    Returns a list of kill event dicts sorted by timestamp, capped at 200.
    Unknown participant IDs resolve to "Unknown" (logged at call site).
    """
    kills: list[dict[str, Any]] = []
    for frame in frames:
        for event in frame.get("events", []):
            if event.get("type") != "CHAMPION_KILL":
                continue
            killer_id = event.get("killerId", 0)
            victim_id = event.get("victimId", 0)
            assist_ids: list[int] = event.get("assistingParticipantIds", [])
            pos = event.get("position", {})
            kills.append(
                {
                    "t": event.get("timestamp", 0),
                    "killer": pid_to_champ.get(killer_id, "Unknown"),
                    "victim": pid_to_champ.get(victim_id, "Unknown"),
                    "assists": [pid_to_champ.get(a, "Unknown") for a in assist_ids],
                    "x": pos.get("x", 0),
                    "y": pos.get("y", 0),
                }
            )
    kills.sort(key=lambda e: e["t"])
    return kills[:_KILL_EVENTS_MAX]
