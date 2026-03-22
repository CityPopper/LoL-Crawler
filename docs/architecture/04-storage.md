# Storage

## Redis Key Schema

All application state lives in Redis. No other database.

| Key Pattern                          | Type       | TTL                       | Contents                                                    |
|--------------------------------------|------------|---------------------------|-------------------------------------------------------------|
| `system:halted`                      | String     | none                      | Set to `"1"` by Recovery on HTTP 403; cleared manually     |
| `player:{puuid}`                     | Hash       | 30d (`PLAYER_DATA_TTL_SECONDS`) | `game_name`, `tag_line`, `region`, `seeded_at` (ISO 8601 string), `last_crawled_at` (ISO 8601 string); TTL refreshed on each crawl/seed |
| `player:matches:{puuid}`             | Sorted Set | 30d (`PLAYER_DATA_TTL_SECONDS`) | member=`match_id`, score=`game_start` epoch ms; capped at `PLAYER_MATCHES_MAX` entries |
| `player:stats:{puuid}`               | Hash       | 30d (`PLAYER_DATA_TTL_SECONDS`) | Raw totals: `total_games`, `total_wins`, `total_kills`, `total_deaths`, `total_assists`; Derived: `win_rate`, `avg_kills`, `avg_deaths`, `avg_assists`, `kda` |
| `player:stats:cursor:{puuid}`        | String     | 30d (`PLAYER_DATA_TTL_SECONDS`) | `game_start` epoch ms of last match processed by Analyzer   |
| `player:stats:lock:{puuid}`          | String     | 300s (`ANALYZER_LOCK_TTL_SECONDS`) | Worker ID; distributed lock for Analyzer              |
| `player:champions:{puuid}`           | Sorted Set | 30d (`PLAYER_DATA_TTL_SECONDS`) | member=`champion_name`, score=games played on that champion |
| `player:roles:{puuid}`               | Sorted Set | 30d (`PLAYER_DATA_TTL_SECONDS`) | member=`role`, score=games played in that role              |
| `match:{match_id}`                   | Hash       | 7d (`MATCH_DATA_TTL_SECONDS`)   | `queue_id`, `game_mode`, `game_type`, `game_version`, `patch`, `game_duration`, `game_start`, `platform_id`, `region`, `status` |
| `match:participants:{match_id}`      | Set        | 7d (`MATCH_DATA_TTL_SECONDS`)   | PUUIDs of all participants in the match; written by Parser alongside participant hashes |
| `match:status:parsed`                | Set        | 90d (hardcoded)           | Secondary index: match IDs with status=parsed (written by Parser) |
| `match:status:failed`                | Set        | 90d (hardcoded)           | Secondary index: match IDs with status=failed (written by Recovery) |
| `participant:{match_id}:{puuid}`     | Hash       | 7d (`MATCH_DATA_TTL_SECONDS`)   | Core: `champion_id`, `champion_name`, `team_id`, `team_position`, `role`, `win`, `kills`, `deaths`, `assists`, `gold_earned`, `gold_spent`, `total_damage_dealt_to_champions`, `total_minions_killed`, `vision_score`, `items`, `champion_level`, `time_played`; Damage breakdown: `physical_damage`, `magic_damage`, `true_damage`, `damage_taken`, `damage_mitigated`, `healing_done`; Vision: `wards_placed`, `wards_killed`, `detector_wards`; Jungle: `neutral_minions`, `turret_kills`; Multikills: `double_kills`, `triple_kills`, `quadra_kills`, `penta_kills`; Runes: `perk_keystone`, `perk_primary_style`, `perk_sub_style`; Summoners: `summoner1_id`, `summoner2_id`. Full field list: see `_queue_participant()` in `lol-pipeline-parser/src/lol_parser/main.py` |
| `raw:match:{match_id}`               | String     | 24h (`RAW_STORE_TTL_SECONDS`)   | Raw match JSON blob; also persisted to disk when `MATCH_DATA_DIR` is set |
| `raw:timeline:{match_id}`           | String     | 7d (`MATCH_DATA_TTL_SECONDS`)   | Raw timeline JSON blob; written by Fetcher when `FETCH_TIMELINE=true`, read by Parser |
| `discover:players`                   | Sorted Set | none                      | member=`{puuid}:{region}`, score=most-recent `game_start` epoch ms; GT update semantics; capped at `MAX_DISCOVER_PLAYERS` |
| `delayed:messages`                   | Sorted Set | none                      | member=serialized envelope, score=ready epoch ms            |
| `player:name:{game_name}#{tag_line}` | String     | 86400s (24h)              | PUUID cache; maps lowercased Riot ID to PUUID               |
| `players:all`                        | Sorted Set | none                      | member=`puuid`, score=seed epoch; capped at 50K; used by UI for player listing |
| `consumer:retry:{stream}:{msg_id}`   | String     | 7d (hardcoded)            | Crash-restart-safe retry counter for poison message detection |
| `autoseed:cooldown:{puuid}`          | String     | 300s (hardcoded)          | Rate-limit key preventing repeated UI auto-seeds for same player |
| `name_cache:index`                   | Sorted Set | none                      | LRU eviction index for `player:name:*` keys; capped at 10K entries |
| `ddragon:version`                    | String     | 24h (hardcoded)           | Cached Data Dragon version string fetched by UI             |
| `ddragon:champion_ids`              | String     | 24h (hardcoded)           | JSON mapping `{champion_numeric_id: champion_name}`; cached by UI from Data Dragon API (see `lol-pipeline-ui/src/lol_ui/ddragon.py`) |
| `ratelimit:short`                    | Sorted Set | 1000ms                    | member=`req_id`, score=epoch ms; sliding 1s window          |
| `ratelimit:long`                     | Sorted Set | 120000ms                  | member=`req_id`, score=epoch ms; sliding 2min window        |
| `ratelimit:limits:short`             | String     | 1h (hardcoded)            | Dynamic 1s window limit from Riot API `X-App-Rate-Limit` header |
| `ratelimit:limits:long`              | String     | 1h (hardcoded)            | Dynamic 2min window limit from Riot API `X-App-Rate-Limit` header |
| `player:priority:{puuid}`            | String     | 24h (`PRIORITY_KEY_TTL`)  | Priority marker; set by `set_priority()` (Seed/UI auto-seed) |

---

## Champion Analytics Keys

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `champion:stats:{name}:{patch}:{role}` | Hash | 90d (`CHAMPION_STATS_TTL_SECONDS`) | Per-champion aggregate stats (games, wins, kills, deaths, assists, gold, cs, damage, vision, multikills) |
| `champion:index:{patch}` | Sorted Set | 90d (`CHAMPION_STATS_TTL_SECONDS`) | Champions per patch index; member=`{name}:{role}`, score=games played |
| `champion:bans:{patch}` | Hash | 90d (`CHAMPION_STATS_TTL_SECONDS`) | Ban counts per champion per patch |
| `matchup:{A}:{B}:{position}:{patch}` | Hash | 90d (`CHAMPION_STATS_TTL_SECONDS`) | Head-to-head matchup stats between two champions in a lane |
| `matchup:index:{champ}:{position}:{patch}` | Set | 90d (`CHAMPION_STATS_TTL_SECONDS`) | Matchup index: all opponents faced by a champion in a position |
| `player:rank:{puuid}` | Hash | 24h (hardcoded) | Player rank data (`tier`, `division`, `lp`, `wins`, `losses`) from league-v4 |
| `player:rank:history:{puuid}` | Sorted Set | 30d (`PLAYER_DATA_TTL_SECONDS`) | Rank history timeline; member=`{tier}:{division}:{lp}`, score=epoch ms; capped at 500 entries |
| `crawl:cursor:{puuid}` | String | 10m (hardcoded) | Pagination resume cursor for crawler match-list API calls |
| `seen:matches` | Set | 7d (`SEEN_MATCHES_TTL_SECONDS`) | Global match dedup set; prevents re-fetching already-known matches |
| `patch:list` | Sorted Set | 90d (`CHAMPION_STATS_TTL_SECONDS`) | Known game patches; member=patch string, score=earliest game_start epoch |
| `build:{match_id}:{puuid}` | String | 7d (`MATCH_DATA_TTL_SECONDS`) | Item build order from match timeline (when `FETCH_TIMELINE=true`) |
| `skills:{match_id}:{puuid}` | String | 7d (`MATCH_DATA_TTL_SECONDS`) | Skill order from match timeline (when `FETCH_TIMELINE=true`) |
| `ratelimit:throttle` | String | 2s (hardcoded) | Near-capacity throttle hint; set when sliding window is >80% full |

---

## Match Status Lifecycle

Field: `match:{match_id}.status`

Values written by services:

```
  [key does not exist]
         │
         │  Fetcher: raw blob written to RawStore
         ▼
      fetched
         │
         │  Parser: structured data written to Redis
         ▼
      parsed  ──► SADD match:status:parsed  (secondary index)
```

**Terminal error states** (no further processing):

| Status      | Set by          | Meaning                                          |
|-------------|-----------------|--------------------------------------------------|
| `not_found` | Fetcher         | Riot API returned HTTP 404                       |
| `failed`    | Recovery        | `dlq_attempts` exhausted; archived to dlq:archive|

**Secondary index:** The Parser writes `match_id` to `match:status:parsed` (Set) after
successful parse. Recovery writes `match_id` to `match:status:failed` (Set) when archiving.
This allows admin commands to enumerate matches by status without key scanning. The general
pattern key is `match:status:{status}`.

---

## RawStore Abstraction

The Fetcher writes and the Parser reads raw match JSON via `RawStore` (`lol_pipeline/raw_store.py`).
All writes are write-once (no-op if the key/file already exists).

**Interface:**
```python
class RawStore:
    async def exists(self, match_id: str) -> bool: ...
    async def get(self, match_id: str) -> str | None: ...
    async def set(self, match_id: str, data: str) -> None: ...
```

**Disk persistence (recommended):** When `MATCH_DATA_DIR` is set, `RawStore` becomes
write-through — every `set()` call appends to a JSONL bundle file at
`{MATCH_DATA_DIR}/{platform}/{YYYY-MM}.jsonl`. Each line is tab-separated:
`{match_id}\t{data}`. On a Redis miss, `get()` falls back to scanning the bundle file
and repopulates Redis automatically. This ensures match data survives Redis resets.

Individual `{match_id}.json` files are a read-only legacy fallback — `get()` checks for
them if the match is not found in the JSONL bundle, but `set()` always writes to the
bundle format.

```
Fetcher                     Parser
  │                           │
  │  set(match_id, json)      │  get(match_id)
  │    ├─ Redis SET nx=True   │    ├─ Redis GET  → hit: return
  │    └─ JSONL append        │    └─ JSONL scan (if miss) → Redis SET + return
  │                           │
  └──────────── lol-pipeline-fetcher/match-data/ ────────────┘
```

**Location on disk:** `lol-pipeline-fetcher/match-data/{platform}/{YYYY-MM}.jsonl`
(e.g. `match-data/NA1/2026-03.jsonl`)

Both the fetcher (read-write) and parser (read-only) containers mount this directory.
The `MATCH_DATA_DIR` env var must be set to `/match-data` in both containers (already
configured in `docker-compose.yml` and `.env.example`).

**Recovery after Redis reset:**
```bash
just reset          # wipe Redis
just up             # start stack — match-data/ still on disk
# On first access, parser's RawStore.get() reads from disk and repopulates Redis
```

---

## Redis Persistence Configuration

Redis must be configured with both AOF and RDB for durability:

```
appendonly yes
appendfsync everysec
save 900 1
save 300 10
save 60 10000
```

In production, use a managed Redis instance (Redis Cloud, ElastiCache) with automatic
failover. The `REDIS_URL` env var is the only change needed between environments.
