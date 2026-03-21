# lol-pipeline-ui

FastAPI web UI for seeding players and viewing stats. Serves on port 8080.

## Pages

| Route | Description |
|-------|-------------|
| `/` | Redirect to `/stats` |
| `/stats?riot_id=Name%23Tag` | Per-player stats with auto-seed |
| `/stats/matches?riot_id=...` | Match history list |
| `/players` | All tracked players |
| `/streams` | Pipeline health (stream depths, consumer groups) |
| `/logs` | Merged service logs |

## Key env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `RIOT_API_KEY` | required | For player lookup on `/stats` |
| `REDIS_URL` | required | For stream depths and player data |
