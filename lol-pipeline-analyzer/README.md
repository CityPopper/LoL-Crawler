# lol-pipeline-analyzer

Reads player PUUIDs from `stream:analyze` and aggregates per-player stats from parsed match data into `player:stats:{puuid}`.

## Behaviour

1. Consumes `stream:analyze` via consumer group `analyzers`
2. Acquires a per-PUUID Redis lock to prevent duplicate concurrent processing
3. Reads unprocessed matches from `player:matches:{puuid}` (sorted set) using a cursor stored at `player:stats:cursor:{puuid}`
4. For each new match: reads `participant:{match_id}:{puuid}` and increments aggregate counters
5. Derives win_rate, avg_kills, avg_deaths, avg_assists, KDA from totals
6. Advances cursor so crashes don't cause re-processing

## Redis writes

| Key | Type | Contents |
|-----|------|----------|
| `player:stats:{puuid}` | Hash | total_games, total_wins, total_kills, avg_kills, kda, win_rate, … |
| `player:champions:{puuid}` | Sorted set | champion_name → games played |
| `player:roles:{puuid}` | Sorted set | role → games played |
| `player:stats:cursor:{puuid}` | String | Last processed game_start timestamp |

## Lock

Uses `player:stats:lock:{puuid}` with TTL `ANALYZER_LOCK_TTL_SECONDS` (default 300s) to prevent duplicate analysis.
