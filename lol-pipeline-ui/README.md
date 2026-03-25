# lol-pipeline-ui

FastAPI web UI for viewing player stats and pipeline status. Serves on port 8080.

**Read-only:** This service makes no Redis write calls. Fragment and DDragon/spell caches are
in-memory dicts. For write operations (DLQ replay/clear, halt/resume) use the Admin UI
(`lol-pipeline-admin-ui`, port 8081) or the CLI (`just admin <cmd>`).

If a player is not found, the `/stats` page shows a message directing the user to run
`just admin track "GameName#TagLine" --region <region>`.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Redirect to `/stats` |
| `/stats?riot_id=Name%23Tag` | Per-player stats; shows "not found" message if no data exists |
| `/stats/matches?puuid=...` | Paginated match history fragment (lazy-loaded) |
| `/stats/match-detail?match_id=...` | Expandable match detail fragment |
| `/players` | Paginated player list with rank sort and region filter |
| `/champions` | Champion tier list by patch |
| `/matchups` | Head-to-head champion matchup lookup |
| `/streams` | Pipeline health (stream depths, system:halted status) |
| `/dlq` | Dead-letter queue browser (read-only) |
| `/logs` | Merged structured logs from all services with auto-refresh |

## Key env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `RIOT_API_KEY` | required | For player lookup on `/stats` |
| `REDIS_URL` | required | For stream depths and player data |
| `LOG_DIR` | unset | Directory for log files; required for `/logs` route |
