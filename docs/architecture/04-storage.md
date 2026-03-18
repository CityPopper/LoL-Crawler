# Storage

## Redis Key Schema

All application state lives in Redis. No other database.

| Key Pattern                          | Type       | TTL      | Contents                                                    |
|--------------------------------------|------------|----------|-------------------------------------------------------------|
| `system:halted`                      | String     | none     | Set to `"1"` by Recovery on HTTP 403; cleared manually     |
| `player:{puuid}`                     | Hash       | none     | `game_name`, `tag_line`, `region`, `seeded_at` (epoch ms as string), `last_crawled_at` (epoch ms as string) |
| `player:matches:{puuid}`             | Sorted Set | none     | member=`match_id`, score=`game_start` epoch ms              |
| `player:stats:{puuid}`               | Hash       | none     | Raw totals: `total_games`, `total_wins`, `total_kills`, `total_deaths`, `total_assists`; Derived: `win_rate`, `avg_kills`, `avg_deaths`, `avg_assists`, `kda` |
| `player:stats:cursor:{puuid}`        | String     | none     | `game_start` epoch ms of last match processed by Analyzer   |
| `player:stats:lock:{puuid}`          | String     | 300s     | Worker ID; distributed lock for Analyzer; TTL = `ANALYZER_LOCK_TTL_SECONDS` |
| `player:champions:{puuid}`           | Sorted Set | none     | member=`champion_name`, score=games played on that champion |
| `player:roles:{puuid}`               | Sorted Set | none     | member=`role`, score=games played in that role              |
| `match:{match_id}`                   | Hash       | none     | `queue_id`, `game_mode`, `game_type`, `game_version`, `game_duration`, `game_start`, `platform_id`, `region`, `status` |
| `match:participants:{match_id}`      | Set        | none     | PUUIDs of all participants in this match                    |
| `match:status:parsed`                | Set        | none     | Secondary index: match IDs with status=parsed (written by Parser) |
| `match:status:failed`                | Set        | none     | Secondary index: match IDs with status=failed (written by Recovery) |
| `participant:{match_id}:{puuid}`     | Hash       | none     | `champion_id`, `champion_name`, `team_id`, `team_position`, `role`, `win`, `kills`, `deaths`, `assists`, `gold_earned`, `total_damage_dealt_to_champions`, `total_minions_killed`, `vision_score`, `items` |
| `raw:match:{match_id}`               | String     | none     | Raw match JSON blob; also persisted to disk when `MATCH_DATA_DIR` is set |
| `discover:players`                   | Sorted Set | none     | member=`{puuid}:{region}`, score=most-recent `game_start` epoch ms; GT update semantics |
| `delayed:messages`                   | Sorted Set | none     | member=serialized envelope, score=ready epoch ms            |
| `ratelimit:short`                    | Sorted Set | 1000ms   | member=`req_id`, score=epoch ms; sliding 1s window          |
| `ratelimit:long`                     | Sorted Set | 120000ms | member=`req_id`, score=epoch ms; sliding 2min window        |

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
write-through — every `set()` call also writes `{MATCH_DATA_DIR}/{platform}/{match_id}.json`
(e.g. `match-data/NA1/NA1_1234567890.json`). On a Redis miss, `get()` falls back to disk
and repopulates Redis automatically. This ensures match data survives Redis resets.

```
Fetcher                     Parser
  │                           │
  │  set(match_id, json)      │  get(match_id)
  │    ├─ Redis SET nx=True   │    ├─ Redis GET  → hit: return
  │    └─ disk write (if set) │    └─ disk read (if miss) → Redis SET + return
  │                           │
  └──────────── lol-pipeline-fetcher/match-data/ ────────────┘
```

**Location on disk:** `lol-pipeline-fetcher/match-data/{platform}/{match_id}.json`

Both the fetcher (read-write) and parser (read-only) containers mount this directory.
The `MATCH_DATA_DIR` env var must be set to `/match-data` in both containers (already
configured in `docker-compose.yml` and `.env.example`).

**Recovery after Redis reset:**
```bash
just down -v        # wipe Redis
just up             # start stack — match-data/ still on disk
# On first access, parser's RawStore.get() reads from disk and repopulates Redis
```

---

## LCU On-Disk Storage

LCU match history is stored outside Redis as append-only JSONL files. This data is not verified against the Riot API and covers game modes unavailable in Match-v5.

**Location:** `lol-pipeline-lcu/lcu-data/{puuid}.jsonl`

**Format:** One JSON object per line (JSON Lines). Each line is a serialized `LcuMatch`:

```json
{"game_id": 123456789, "game_creation": 1700000000000, "game_duration": 1800,
 "queue_id": 900, "game_mode": "URF", "champion_id": 91,
 "win": true, "kills": 15, "deaths": 3, "assists": 7,
 "gold_earned": 14000, "damage_to_champions": 45000,
 "puuid": "...", "riot_id": "Faker#KR1"}
```

**Properties:**
- Append-only: new matches are appended; existing lines are never modified or deleted
- Deduplication: the collector loads existing `game_id` values before appending, preventing duplicates across runs
- UI reads: the Web UI loads all JSONL files into memory at startup (`app.state.lcu`); restart the UI to pick up newly collected data

**Important:** These files are the only historical record for unsupported queue types (rotating modes). Back them up and do not delete them.

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
