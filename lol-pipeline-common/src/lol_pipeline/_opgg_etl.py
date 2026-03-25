"""ETL: transform op.gg game response to match-v5-shaped dicts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_OPGG_REGION_MAP = {
    "na": "NA1",
    "kr": "KR",
    "euw": "EUW1",
    "eune": "EUN1",
    "br": "BR1",
    "jp": "JP1",
    "lan": "LA1",
    "las": "LA2",
    "oce": "OC1",
    "tr": "TR1",
    "ru": "RU",
}

_DROPPED_PARTICIPANT_FIELDS = {"op_score", "lane_score", "clips", "keyword"}


def _normalize_participant(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a single op.gg participant dict to match-v5 participant shape."""
    summoner = raw.get("summoner", {})
    stats = raw.get("stats", {})
    items: list[int] = list(raw.get("items", [0, 0, 0, 0, 0, 0, 0]))
    # Pad/trim to 7 items
    while len(items) < 7:
        items.append(0)
    p: dict[str, Any] = {
        "puuid": summoner.get("puuid", ""),
        "summonerId": summoner.get("summoner_id", ""),
        "championId": raw.get("champion_id", 0),
        "teamPosition": raw.get("position", ""),
        "kills": stats.get("kill", 0),
        "deaths": stats.get("death", 0),
        "assists": stats.get("assist", 0),
        "totalMinionsKilled": stats.get("cs", 0),
        "totalDamageDealtToChampions": stats.get("damage_dealt_to_champions", 0),
        "item0": items[0],
        "item1": items[1],
        "item2": items[2],
        "item3": items[3],
        "item4": items[4],
        "item5": items[5],
        "item6": items[6],
    }
    # Drop proprietary fields (already excluded by building from scratch)
    return p


def _normalize_team(raw_team: dict[str, Any], team_id: int) -> dict[str, Any]:
    """Map op.gg team to match-v5 team shape."""
    stat = raw_team.get("game_stat", {})
    return {
        "teamId": team_id,
        "win": bool(stat.get("is_win", False)),
        "bans": [],
        "objectives": {
            "kills": {"first": False, "kills": stat.get("kill", 0)},
            "deaths": {"first": False, "kills": stat.get("death", 0)},
        },
    }


def normalize_game(raw_game: dict[str, Any], region: str = "") -> dict[str, Any]:
    """Transform a single op.gg game dict to match-v5-shaped dict.

    Raises ``KeyError`` if required top-level fields are missing.
    """
    game_id: str = raw_game["id"]
    created_at: str = raw_game.get("created_at", "")
    # Convert ISO timestamp to epoch ms
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        game_creation_ms = int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        game_creation_ms = 0

    platform = _OPGG_REGION_MAP.get(region.lower(), region.upper())
    match_id = f"OPGG_{platform}_{game_id}"

    teams_raw: list[dict[str, Any]] = raw_game.get("teams", [])
    participants: list[dict[str, Any]] = []
    teams: list[dict[str, Any]] = []
    team_ids = [100, 200]
    for i, team_raw in enumerate(teams_raw):
        team_id = team_ids[i] if i < len(team_ids) else (i + 1) * 100
        teams.append(_normalize_team(team_raw, team_id))
        for raw_p in team_raw.get("participants", []):
            p = _normalize_participant(raw_p)
            p["teamId"] = team_id
            p["win"] = bool(team_raw.get("game_stat", {}).get("is_win", False))
            participants.append(p)

    return {
        "metadata": {
            "data_version": "2",
            "match_id": match_id,
            "participants": [p["puuid"] for p in participants],
        },
        "info": {
            "gameCreation": game_creation_ms,
            "gameDuration": raw_game.get("game_length_second", 0),
            "gameMode": raw_game.get("game_type", "CLASSIC"),
            "gameType": raw_game.get("game_type", "MATCHED_GAME"),
            "platformId": platform,
            "queueId": raw_game.get("queue_info", {}).get("queue_id", 0),
            "participants": participants,
            "teams": teams,
            "source": "opgg",
            "fetched_at": datetime.now(tz=UTC).isoformat(),
        },
    }
