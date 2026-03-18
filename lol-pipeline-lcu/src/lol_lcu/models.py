"""LcuMatch dataclass and JSONL serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class LcuMatch:
    """A single match from the LCU match history API."""

    game_id: int
    game_creation: int  # epoch ms
    game_duration: int  # seconds
    queue_id: int
    game_mode: str
    champion_id: int
    win: bool
    kills: int
    deaths: int
    assists: int
    gold_earned: int
    damage_to_champions: int
    puuid: str
    riot_id: str
    items: list[int] = field(default_factory=list)
    participants: list[dict] = field(default_factory=list)  # type: ignore[type-arg]

    def to_json_line(self) -> str:
        """Serialize to a single JSON line (no trailing newline)."""
        return json.dumps(
            {
                "game_id": self.game_id,
                "game_creation": self.game_creation,
                "game_duration": self.game_duration,
                "queue_id": self.queue_id,
                "game_mode": self.game_mode,
                "champion_id": self.champion_id,
                "win": self.win,
                "kills": self.kills,
                "deaths": self.deaths,
                "assists": self.assists,
                "gold_earned": self.gold_earned,
                "damage_to_champions": self.damage_to_champions,
                "puuid": self.puuid,
                "riot_id": self.riot_id,
                "items": self.items,
                "participants": self.participants,
            }
        )

    @classmethod
    def from_json_line(cls, line: str) -> LcuMatch:
        """Deserialize from a JSON line."""
        data = json.loads(line)
        return cls(**data)
