"""Parser constants — team IDs and participant field mappings."""

from __future__ import annotations

_TEAM_ID_BLUE = 100
_TEAM_ID_RED = 200
_TEAM_ID_MAP: dict[int, str] = {
    _TEAM_ID_BLUE: "team_blue",
    _TEAM_ID_RED: "team_red",
}

# Maps Redis hash field name -> (Riot API field name, default value).
# Used by _participant_fields() to build the participant hash from match JSON.
_PARTICIPANT_FIELD_MAP: dict[str, tuple[str, int | str]] = {
    "champion_id": ("championId", ""),
    "team_id": ("teamId", ""),
    "team_position": ("teamPosition", ""),
    "role": ("role", ""),
    "kills": ("kills", 0),
    "deaths": ("deaths", 0),
    "assists": ("assists", 0),
    "gold_earned": ("goldEarned", 0),
    "total_damage_dealt_to_champions": ("totalDamageDealtToChampions", 0),
    "total_minions_killed": ("totalMinionsKilled", 0),
    "vision_score": ("visionScore", 0),
    "summoner1_id": ("summoner1Id", 0),
    "summoner2_id": ("summoner2Id", 0),
    "champion_level": ("champLevel", 0),
    "gold_spent": ("goldSpent", 0),
    "physical_damage": ("physicalDamageDealtToChampions", 0),
    "magic_damage": ("magicDamageDealtToChampions", 0),
    "true_damage": ("trueDamageDealtToChampions", 0),
    "damage_taken": ("totalDamageTaken", 0),
    "damage_mitigated": ("damageSelfMitigated", 0),
    "healing_done": ("totalHeal", 0),
    "wards_placed": ("wardsPlaced", 0),
    "wards_killed": ("wardsKilled", 0),
    "detector_wards": ("detectorWardsPlaced", 0),
    "neutral_minions": ("neutralMinionsKilled", 0),
    "turret_kills": ("turretKills", 0),
    "double_kills": ("doubleKills", 0),
    "triple_kills": ("tripleKills", 0),
    "quadra_kills": ("quadraKills", 0),
    "penta_kills": ("pentaKills", 0),
    "time_played": ("timePlayed", 0),
}
