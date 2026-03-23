# LoL Match Intelligence Pipeline

A streaming data pipeline that collects League of Legends match history via the Riot API and computes per-player aggregate stats. Stateless Python microservices communicate through Redis Streams.

## Pipeline

```
Seed → Crawler → Fetcher → Parser → Analyzer
                    ↑                    ↕
               Discovery        Recovery + Delay Scheduler
                                         ↕
                                   Web UI (port 8080)
```

| Service         | Role                                                   |
|-----------------|--------------------------------------------------------|
| Seed            | Resolves a Riot ID to PUUID, enqueues the player       |
| Crawler         | Fetches all match IDs for a player, deduplicates       |
| Fetcher         | Downloads raw match JSON from Riot API                 |
| Parser          | Transforms raw JSON into structured Redis records      |
| Analyzer        | Builds incremental per-player aggregate stats          |
| Recovery        | Retries failed messages from the DLQ                   |
| Delay Scheduler | Moves rate-limit-delayed messages into target streams  |
| Discovery       | Promotes discovered players into the pipeline when idle |
| Web UI          | Seed players and view stats at http://localhost:8080   |

Pipeline state lives in Redis. All config is injected via environment variables.

## Prerequisites

- [Python 3.14+](https://www.python.org/downloads/)
- [Podman](https://podman.io/getting-started/installation) (default) or [Docker](https://docs.docker.com/get-docker/)
- [just](https://github.com/casey/just#installation)

## Setup

```bash
just setup          # copies .env.example → .env
# edit .env and set RIOT_API_KEY
just build          # builds all Docker images
```

## Run

Podman is the default runtime. To use Docker instead: `RUNTIME=docker just <cmd>`

```bash
just up                         # setup + build + run in one step
just run                        # start all services (auto-runs via `just seed` too)
just seed "Faker#KR1" kr        # seed a player (auto-starts stack if needed)
just logs fetcher               # tail logs for a service
just streams                    # inspect Redis stream depths
just restart crawler            # restart a single service after code change
just scale fetcher 3            # scale a service to N replicas
just stop                       # pause containers (data preserved)
just down                       # remove containers (data preserved)
just reset                      # remove containers + wipe Redis data
```

## Web UI

```bash
just ui             # prints URL and opens browser
# or navigate to http://localhost:8080
```

### Dashboard
![Dashboard](screenshots/01_dashboard_en.png)

System status, stream depths, player lookup. Language switcher (EN | 中文) + theme switcher.

### Player Stats (中文)
![Player Stats](screenshots/02_stats_zh.png)

Two-column layout with localized UI. Tabbed match detail: Overview | Build | Team Analysis | AI Score | Timeline.

### Champion Tier List
![Champions](screenshots/03_champions_en.png)

Per-patch tier list with win rate, pick rate, ban rate, PBI tier (S/A/B/C/D), and patch-over-patch delta.

### Matchups (中文)
![Matchups](screenshots/04_matchups_zh.png)

Champion matchup lookup with autocomplete datalist, localized role names.

### Players
![Players](screenshots/05_players_en.png)

Player listing sorted by ranked standing. Region filter, rank column.

### Streams (中文)
![Streams](screenshots/06_streams_zh.png)

Pipeline health monitor — stream depths, consumer lag, system status.

### Logs (中文)
![Logs](screenshots/08_logs_zh.png)

Merged service logs with expandable entries, service filter, clear button.

### Mobile
![Mobile](screenshots/09_mobile_en.png)

Responsive layout on mobile devices.

## Admin CLI

```bash
just admin stats "Faker#KR1" --region kr
just admin dlq list
just admin dlq replay --all
just admin system-resume         # clear system:halted after key rotation
just admin reseed "Faker#KR1" --region kr   # force re-crawl bypassing cooldown
```

## Testing

1161 unit tests + 44 contract tests across all services.

```bash
just test                       # run all unit tests (services tested in parallel)
just test-svc crawler           # run unit tests for a single service
just contract                   # run PACT contract tests for all services
just integration                # integration tests (requires Podman/Docker for testcontainers)
just e2e                        # end-to-end test (requires running stack + valid API key)
just test-all                   # unit + contract tests combined
just coverage                   # run all unit tests with coverage report
just lint                       # ruff check + format check on all services
just fix                        # auto-fix lint issues + format all services
just format                     # format all services (ruff format)
just typecheck                  # mypy on all services
just check                      # lint + typecheck
just update-mocks               # refresh Pwnerer#1337 fixtures from live Riot API
```

## Data Management

```bash
just consolidate                # bundle individual match JSON files into JSONL+zstd archives
```

## Code Changes (No Rebuild Needed)

Service source is volume-mounted. Edit `main.py`, then:

```bash
just restart crawler   # picks up changes immediately
```

## Environment Variables

Create `.env` (from `.env.example`) and set at minimum:

| Variable                    | Description                          |
|-----------------------------|--------------------------------------|
| `RIOT_API_KEY`              | Riot Games API key (required)        |
| `REDIS_URL`                 | Redis connection string (no code default; `.env.example` sets `redis://redis:6379/0`) |
| `SEED_COOLDOWN_MINUTES`     | Minutes before a player can be re-seeded (default: `30`) |
| `API_RATE_LIMIT_PER_SECOND` | Riot API per-second cap (default: `20`) |

See `docs/architecture/01-overview.md` for the full variable reference.
