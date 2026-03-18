# lol-pipeline-ui

FastAPI web UI for seeding players and viewing stats. Serves on port 8080.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Home — seed form + stream depth overview |
| `/stats?riot_id=Name%23Tag` | Per-player stats: verified API data (✓) + unverified LCU data (⚠) |
| `/lcu` | LCU data overview — all collected match history by player and game mode |

## LCU data

- Loaded from `LCU_DATA_DIR` (default `/lcu-data`) on startup
- If `LCU_POLL_INTERVAL_MINUTES > 0`, reloaded automatically in the background every N minutes
- LCU data is clearly marked as **unverified** (⚠) and never mixed with API stats counters

## Key env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `LCU_DATA_DIR` | `/lcu-data` | Path to directory containing `{puuid}.jsonl` files |
| `LCU_POLL_INTERVAL_MINUTES` | `0` | Reload JSONL from disk every N minutes (0 = startup only) |
| `RIOT_API_KEY` | required | For player lookup on `/stats` |
| `REDIS_URL` | required | For stream depths and player data |
