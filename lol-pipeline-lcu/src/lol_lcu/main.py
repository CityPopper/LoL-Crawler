"""LCU collector main logic — collect matches, deduplicate, append to JSONL."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from lol_lcu.lcu_client import LcuAuthError, LcuClient, LcuNotRunningError
from lol_lcu.log import get_logger
from lol_lcu.models import LcuMatch

log = get_logger("lcu")


def load_existing_game_ids(jsonl_path: Path) -> set[int]:
    """Load game IDs already recorded in a JSONL file."""
    if not jsonl_path.exists():
        return set()
    ids: set[int] = set()
    for line in jsonl_path.read_text().splitlines():
        line = line.strip()
        if line:
            data = json.loads(line)
            ids.add(data["game_id"])
    return ids


def _identity_map(game: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Build participantId -> player identity from participantIdentities.

    The LCU match history uses the v4 format: participant stats live in
    ``participants[]`` keyed by participantId, while identity info (puuid,
    gameName, etc.) lives in ``participantIdentities[].player``.
    """
    result: dict[int, dict[str, Any]] = {}
    for entry in game.get("participantIdentities", []):
        pid = entry.get("participantId")
        player = entry.get("player", {})
        if pid is not None:
            result[pid] = player
    return result


def _extract_player_stats(game: dict[str, Any], puuid: str) -> dict[str, Any] | None:
    """Extract the current player's stats from a game's participants."""
    id_map = _identity_map(game)
    for p in game.get("participants", []):
        pid = p.get("participantId")
        identity = id_map.get(pid, {})
        if identity.get("puuid") == puuid:
            stats = p.get("stats", {})
            return {
                "champion_id": p.get("championId", 0),
                "win": stats.get("win", False),
                "kills": stats.get("kills", 0),
                "deaths": stats.get("deaths", 0),
                "assists": stats.get("assists", 0),
                "gold_earned": stats.get("goldEarned", 0),
                "damage_to_champions": stats.get("totalDamageDealtToChampions", 0),
                "items": [stats.get(f"item{i}", 0) for i in range(7)],
            }
    return None


def _build_participants(game: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a minimal participant list from the game data.

    Joins ``participants`` with ``participantIdentities`` to attach each
    player's puuid (the LCU v4 format stores them separately).
    """
    id_map = _identity_map(game)
    result = []
    for p in game.get("participants", []):
        pid = p.get("participantId")
        identity = id_map.get(pid, {})
        result.append(
            {
                "puuid": identity.get("puuid", ""),
                "championId": p.get("championId", 0),
            }
        )
    return result


def collect_once(client: LcuClient, data_dir: str) -> int:
    """Collect new matches and append to JSONL. Returns count of new matches."""
    summoner = client.current_summoner()
    puuid = summoner["puuid"]
    riot_id = f"{summoner['gameName']}#{summoner['tagLine']}"

    data_path = Path(data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    jsonl_file = data_path / f"{puuid}.jsonl"

    existing_ids = load_existing_game_ids(jsonl_file)
    log.info("Loaded %d existing game IDs for %s", len(existing_ids), riot_id)

    # Paginate newest-first
    new_count = 0
    beg = 0
    page_size = 20

    while True:
        games = client.match_history(puuid, beg_index=beg, end_index=beg + page_size)
        if not games:
            break

        all_known = True
        for game in games:
            game_id = game.get("gameId", 0)
            if game_id in existing_ids:
                continue
            all_known = False

            player = _extract_player_stats(game, puuid)
            if player is None:
                continue

            match = LcuMatch(
                game_id=game_id,
                game_creation=game.get("gameCreation", 0),
                game_duration=game.get("gameDuration", 0),
                queue_id=game.get("queueId", 0),
                game_mode=game.get("gameMode", ""),
                puuid=puuid,
                riot_id=riot_id,
                participants=_build_participants(game),
                **player,
            )
            with open(jsonl_file, "a") as f:
                f.write(match.to_json_line() + "\n")
            existing_ids.add(game_id)
            new_count += 1

        if all_known or len(games) < page_size:
            break
        beg += page_size

    log.info("Collected %d new matches for %s", new_count, riot_id)
    return new_count


_AUTH_RETRY_MAX = 3
_AUTH_RETRY_DELAY = 2  # seconds


def _collect_with_auth_retry(install_path: str, data_dir: str) -> int | None:
    """Collect once, retrying up to _AUTH_RETRY_MAX times on stale credentials."""
    for attempt in range(_AUTH_RETRY_MAX):
        client = LcuClient(install_path=install_path)
        try:
            return collect_once(client, data_dir)
        except LcuAuthError:
            log.warning(
                "Stale lockfile credentials (attempt %d/%d) — retrying",
                attempt + 1,
                _AUTH_RETRY_MAX,
            )
            if attempt < _AUTH_RETRY_MAX - 1:
                time.sleep(_AUTH_RETRY_DELAY)
    log.error("LCU auth failed after %d retries — lockfile may be stale", _AUTH_RETRY_MAX)
    return None


def run(data_dir: str, poll_interval_minutes: int = 0) -> None:
    """Main entry point — collect once or poll continuously."""
    install_path = os.environ.get("LEAGUE_INSTALL_PATH", "")
    if not install_path:
        log.error("LEAGUE_INSTALL_PATH not set")
        return

    try:
        LcuClient(install_path=install_path)
    except LcuNotRunningError:
        log.warning("League client not running — showing historical summary")
        _show_summary(data_dir)
        return

    _collect_with_auth_retry(install_path, data_dir)

    if poll_interval_minutes > 0:
        log.info("Polling every %d minutes", poll_interval_minutes)
        while True:
            time.sleep(poll_interval_minutes * 60)
            try:
                _collect_with_auth_retry(install_path, data_dir)
            except LcuNotRunningError:
                log.warning("League client not running — will retry next interval")


def _show_summary(data_dir: str) -> None:
    """Show a summary of existing JSONL data."""
    data_path = Path(data_dir)
    if not data_path.exists():
        log.info("No LCU data directory found at %s", data_dir)
        return
    for jsonl_file in sorted(data_path.glob("*.jsonl")):
        ids = load_existing_game_ids(jsonl_file)
        log.info("%s: %d matches", jsonl_file.stem, len(ids))
