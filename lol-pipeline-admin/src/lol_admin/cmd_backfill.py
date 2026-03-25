"""Admin CLI: backfill-champions command."""

from __future__ import annotations

import argparse

import redis.asyncio as aioredis
from lol_pipeline.config import Config

from lol_admin._helpers import _print_info, _print_ok

_UPDATE_CHAMPION_LUA = """
local stats_key = KEYS[1]
local index_key = KEYS[2]
local patch_list_key = KEYS[3]
local win     = tonumber(ARGV[1])
local kills   = tonumber(ARGV[2])
local deaths  = tonumber(ARGV[3])
local assists  = tonumber(ARGV[4])
local gold     = tonumber(ARGV[5])
local cs       = tonumber(ARGV[6])
local damage   = tonumber(ARGV[7])
local vision   = tonumber(ARGV[8])
local index_member = ARGV[9]
local game_start   = tonumber(ARGV[10])
local patch        = ARGV[11]
local ttl          = tonumber(ARGV[12])
local double_kills = tonumber(ARGV[13])
local triple_kills = tonumber(ARGV[14])
local quadra_kills = tonumber(ARGV[15])
local penta_kills  = tonumber(ARGV[16])

redis.call('HINCRBY', stats_key, 'games', 1)
redis.call('HINCRBY', stats_key, 'wins', win)
redis.call('HINCRBY', stats_key, 'kills', kills)
redis.call('HINCRBY', stats_key, 'deaths', deaths)
redis.call('HINCRBY', stats_key, 'assists', assists)
redis.call('HINCRBY', stats_key, 'gold', gold)
redis.call('HINCRBY', stats_key, 'cs', cs)
redis.call('HINCRBY', stats_key, 'damage', damage)
redis.call('HINCRBY', stats_key, 'vision', vision)
redis.call('HINCRBY', stats_key, 'double_kills', double_kills)
redis.call('HINCRBY', stats_key, 'triple_kills', triple_kills)
redis.call('HINCRBY', stats_key, 'quadra_kills', quadra_kills)
redis.call('HINCRBY', stats_key, 'penta_kills', penta_kills)
redis.call('EXPIRE', stats_key, ttl)

redis.call('ZINCRBY', index_key, 1, index_member)
redis.call('EXPIRE', index_key, ttl)

redis.call('ZADD', patch_list_key, 'NX', game_start, patch)
redis.call('EXPIRE', patch_list_key, ttl)
return 1
"""


async def _scan_parsed_matches(r: aioredis.Redis) -> set[str]:
    """Collect match IDs whose per-match hash has status=parsed (RDB-2)."""
    result: set[str] = set()
    async for key in r.scan_iter(match="match:*", count=200):
        key_str: str = key
        # Skip non-match keys (match:participants:*, match:status:*)
        if key_str.count(":") != 1:
            continue
        status = await r.hget(key_str, "status")
        if status == "parsed":
            result.add(key_str.removeprefix("match:"))
    return result


async def cmd_backfill_champions(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    """Reprocess all parsed ranked matches to populate champion stats."""
    done_key = "champion:backfill:done"
    parsed = await _scan_parsed_matches(r)
    already_done: set[str] = await r.smembers(done_key)  # type: ignore[misc]
    todo = parsed - already_done
    if not todo:
        _print_info("No matches to backfill (all already processed)")
        return 0
    count = 0
    batch: list[str] = []
    for match_id in todo:
        batch.append(match_id)
        if len(batch) >= 100:
            processed = await _backfill_batch(r, batch)
            count += processed
            await r.sadd(done_key, *batch)  # type: ignore[misc]
            batch = []
            _print_info(f"Progress: {count} matches backfilled...")
    if batch:
        processed = await _backfill_batch(r, batch)
        count += processed
        await r.sadd(done_key, *batch)  # type: ignore[misc]
    await r.expire(done_key, 90 * 86400)
    _print_ok(f"Backfilled champion stats from {count} ranked matches")
    return 0


async def _backfill_participant(
    r: aioredis.Redis,
    p: dict[str, str],
    patch: str,
    game_start: str,
    ttl: int,
) -> None:
    """Update champion stats for a single participant."""
    team_position = p.get("team_position", "")
    champion_name = p.get("champion_name", "")
    if not team_position or not champion_name:
        return
    stats_key = f"champion:stats:{champion_name}:{patch}:{team_position}"
    index_key = f"champion:index:{patch}"
    index_member = f"{champion_name}:{team_position}"
    await r.eval(  # type: ignore[misc]
        _UPDATE_CHAMPION_LUA,
        3,
        stats_key,
        index_key,
        "patch:list",
        int(p.get("win", "0")),
        int(p.get("kills", "0")),
        int(p.get("deaths", "0")),
        int(p.get("assists", "0")),
        int(p.get("gold_earned", "0")),
        int(p.get("total_minions_killed", "0")),
        int(p.get("total_damage_dealt_to_champions", "0")),
        int(p.get("vision_score", "0")),
        index_member,
        game_start,
        patch,
        ttl,
        int(p.get("double_kills", "0")),
        int(p.get("triple_kills", "0")),
        int(p.get("quadra_kills", "0")),
        int(p.get("penta_kills", "0")),
    )


async def _backfill_batch(r: aioredis.Redis, match_ids: list[str]) -> int:
    """Process a batch of matches for champion stats backfill.

    Returns count of ranked matches processed.
    """
    async with r.pipeline(transaction=False) as pipe:
        for mid in match_ids:
            pipe.hgetall(f"match:{mid}")
        metadata_list: list[dict[str, str]] = await pipe.execute()
    count = 0
    ttl = 90 * 86400  # 90 days
    for match_id, meta in zip(match_ids, metadata_list, strict=True):
        if not meta or meta.get("queue_id") != "420":
            continue
        patch = meta.get("patch", "")
        if not patch:
            continue
        participant_keys: list[str] = []
        async for key in r.scan_iter(match=f"participant:{match_id}:*", count=20):
            participant_keys.append(key)
        if not participant_keys:
            continue
        async with r.pipeline(transaction=False) as pipe:
            for key in participant_keys:
                pipe.hgetall(key)
            participants: list[dict[str, str]] = await pipe.execute()
        game_start = meta.get("game_start", "0")
        for p in participants:
            if p:
                await _backfill_participant(r, p, patch, game_start, ttl)
        count += 1
    return count
