# LoL Match Intelligence Pipeline

[中文版](README.zh-CN.md)

A streaming data pipeline that collects League of Legends match history via the Riot API and computes per-player aggregate stats. Stateless Python microservices communicate through Redis Streams.

## Pipeline

```
just admin track "GameName#TagLine"
         ↓
stream:puuid → Crawler → Fetcher → Parser → Player Stats
                  ↑                    ↕         ↕
             Discovery        Recovery + Delay Scheduler
                              ↕           Champion Stats
                        Web UI (port 8080)
```

| Service         | Role                                                   |
|-----------------|--------------------------------------------------------|
| Admin           | CLI tool; `track` sub-command seeds players into the pipeline |
| Crawler         | Fetches all match IDs for a player, deduplicates       |
| Fetcher         | Downloads raw match JSON from Riot API                 |
| Parser          | Transforms raw JSON into structured Redis records      |
| Player Stats    | Builds incremental per-player aggregate stats          |
| Champion Stats  | Aggregates ranked solo queue stats per champion, patch, and role |
| Recovery        | Retries failed messages from the DLQ                   |
| Delay Scheduler | Moves rate-limit-delayed messages into target streams  |
| Discovery       | Promotes discovered players into the pipeline when idle |
| Web UI          | Read-only dashboard: player stats, pipeline status — http://localhost:8080 |
| Admin UI        | Write operations: DLQ replay/clear, halt/resume — http://localhost:8081 (opt-in; `--profile tools`) |

## Screenshots

### Dashboard
![Dashboard](screenshots/dashboard_en.png)

### Player Stats
![Player Stats](screenshots/stats_en.png)

### Champion Tier List
![Champions](screenshots/champions_en.png)

### Matchups
![Matchups](screenshots/matchups_en.png)

### Players
![Players](screenshots/players_en.png)

### Streams
![Streams](screenshots/streams_en.png)

### Logs
![Logs](screenshots/logs_en.png)

### Mobile
![Mobile](screenshots/mobile_en.png)

## Setup

```bash
just setup          # copies .env.example → .env
# edit .env and set RIOT_API_KEY
just up             # setup + build + run (hot reload enabled)
```

## Run

Podman is the default runtime. To use Docker instead: `RUNTIME=docker just <cmd>`

```bash
just up                                         # setup + build + run in one step
just admin track "Faker#KR1" --region kr        # track a player (seed into pipeline)
just logs fetcher               # tail logs for a service
just streams                    # inspect Redis stream depths
just stop                       # pause containers (data preserved)
just down                       # remove containers (data preserved)
just reset                      # remove containers + wipe Redis data
```

## Web UI

Navigate to http://localhost:8080. Features:

- **Dashboard** — system status, stream depths, player lookup
- **Stats** — player profile with match history, AI Score, champion breakdown
- **Champions** — tier list by patch with PBI scoring
- **Matchups** — head-to-head champion lookup with autocomplete
- **Players** — ranked player list with region filter
- **Streams** — pipeline health monitor
- **DLQ** — dead letter queue browser with expandable entries
- **Logs** — merged service logs with service filter

Language switcher (EN | 中文) and theme switcher (Default | Art Pop) included.

## Admin CLI

```bash
just admin track "Faker#KR1" --region kr   # resolve Riot ID, check cooldown, enqueue
just admin stats "Faker#KR1" --region kr
just admin dlq list
just admin dlq replay --all
just admin system-resume
just admin reseed "Faker#KR1" --region kr
```

## Testing

```bash
just test           # all unit tests in parallel
just test-svc ui    # single service
just contract       # PACT contract tests
just integration    # integration tests (needs Docker)
just lint           # ruff check + format
just typecheck      # mypy
just check          # lint + typecheck
```

## Environment Variables

Create `.env` (from `.env.example`) and set at minimum:

| Variable        | Description                   |
|-----------------|-------------------------------|
| `RIOT_API_KEY`  | Riot Games API key (required) |
| `REDIS_URL`     | Redis connection string       |

See `.env.example` for all options.
