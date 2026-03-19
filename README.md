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
| LCU Collector   | Collects match history from the local League client (all game modes, including rotating queues not in Match-v5) |

Pipeline state lives in Redis. LCU data is stored on disk as JSONL in `lol-pipeline-lcu/lcu-data/`. All config is injected via environment variables.

## Prerequisites

- [Python 3.12+](https://www.python.org/downloads/)
- [Docker](https://docs.docker.com/get-docker/)
- [just](https://github.com/casey/just#installation)

## Setup

```bash
just setup          # copies .env.example → .env
# edit .env and set RIOT_API_KEY
just build          # builds all Docker images
```

## Run

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

Pages: **Stats** (look up player stats — Riot API data and LCU data side by side), **Players** (paginated list of all seeded players), **Streams** (stream depths + system status), **LCU** (LCU match history overview), **Logs** (merged service logs with auto-refresh).

## Admin CLI

```bash
just admin stats "Faker#KR1"
just admin dlq list
just admin dlq replay --all
just admin system-resume         # clear system:halted after key rotation
just admin reseed "Faker#KR1"   # force re-crawl bypassing cooldown
```

## LCU Match History (Unverified)

Collects match history directly from the running League client — includes game modes not exposed by the Riot Match-v5 API (ARAM Mayhem, URF, One for All, etc.). Data is stored locally as JSONL and is never lost between runs.

```bash
# With the League client open:
just lcu                        # collect + append new matches for the logged-in player
just lcu-watch                  # continuously collect, polling every LCU_POLL_INTERVAL_MINUTES (default: 5)
just restart ui                 # reload UI to pick up new data
```

Data is stored in `lol-pipeline-lcu/lcu-data/{puuid}.jsonl`.
The UI `/lcu` page shows an overview; `/stats` shows API + LCU side by side.

## Testing

404 unit tests + 44 contract tests across all services.

```bash
just test                       # run all unit tests (services tested in parallel)
just test-svc crawler           # run unit tests for a single service
just contract                   # run PACT contract tests for all services
just integration                # integration tests (requires Docker for testcontainers)
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
| `REDIS_URL`                 | Redis connection string (default: `redis://redis:6379/0`) |
| `SEED_COOLDOWN_MINUTES`     | Minutes before a player can be re-seeded (default: `30`) |
| `API_RATE_LIMIT_PER_SECOND` | Riot API per-second cap (default: `20`) |
| `LCU_DATA_DIR`              | Path to LCU JSONL data directory (default: `/lcu-data` in Docker) |
| `LEAGUE_INSTALL_PATH`       | LoL install dir for live LCU collection (WSL2 path, e.g. `/mnt/c/Riot Games/League of Legends`) |

See `docs/architecture/01-overview.md` for the full variable reference.
