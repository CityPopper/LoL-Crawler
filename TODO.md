# TODO — Improvement Proposals (All 21 Agents)

Phase 7 "IRONCLAD" and Phase 8 "FACELIFT" are **complete**.
546 unit tests + 61 contract tests. 21 agentic coding agents.

---

## UI/UX Improvements
- (Phase 9) Home dashboard page at `/` — system status, recent seeds, stream overview
- (Phase 9) Match detail page — click a match row to see full participant data
- (Phase 9) Player comparison view — side-by-side stats for 2 players
- (Phase 9) Chart.js sparklines for stream depth history on `/streams`
- (Phase 9) Toast notifications for seed success/failure instead of page reload
- (future) Keyboard shortcuts (/ for search, r for refresh)
- (future) Dark/light theme toggle (currently dark-only)
- (future) Export stats as CSV/JSON

## Infrastructure
- (Phase 9) `docker-compose.prod.yml` — baked images, Redis auth, resource limits, log driver
- (Phase 9) Prometheus + Redis Exporter + Grafana monitoring stack
- (Phase 9) Integration test CI job for IT-01 through IT-07 (testcontainers)
- (Phase 9) Docker build layer caching in CI (`actions/cache`)
- (future) Kubernetes Helm chart for multi-node deployment
- (future) GitHub Actions deploy workflow (SSH + docker compose pull)

## Security
- (Phase 9) Redis ACLs — per-service users with minimal key/command permissions
- (Phase 9) Redis TLS (`rediss://` URL) for production
- (Phase 9) TLS reverse proxy (Caddy/nginx) for Web UI
- (Phase 9) Content-Security-Policy header with nonce-based `script-src`
- (Phase 9) `pip-audit` in CI for dependency vulnerability scanning
- (future) Rate limiting on Web UI auto-seed endpoint (prevent API key exhaustion)

## Performance
- (Phase 9) Player index sorted set (`players:all`) to replace SCAN on `/players`
- (Phase 9) Raw blob TTL/eviction — `raw:match:*` keys dominate memory at scale
- (Phase 9) Stream MAXLEN trimming — `XADD ... MAXLEN ~ 10000` to bound growth
- (future) RawStore sorted JSONL bundles + binary search (currently linear scan)
- (future) Discovery batch pipelining (HEXISTS + HSET + XADD + ZREM per member)
- (future) Redis connection pool tuning for scaled workers

## Testing
- (Phase 9) Integration tests IT-08 through IT-11 (priority queue end-to-end)
- (Phase 9) UI route handler tests (show_stats auto-seed path, show_streams, show_dlq)
- (Phase 9) Shared test fixtures in conftest.py (FakeRedis, Config, env overrides)
- (Phase 9) `--cov-fail-under=80` enforcement in CI
- (Phase 9) Parallel contract tests locally (`just contract` uses sequential loop)
- (future) Browser/E2E tests with Playwright for UI regression
- (future) Load testing with locust (rate limiter under sustained pressure)

## Documentation
- (Phase 9) Discovery + LCU service READMEs (all other services have them)
- (Phase 9) CONTRIBUTING.md for external contributors
- (Phase 9) CI workflow guide (how the pipeline works, how to add a new service to CI)
- (Phase 9) CHANGELOG.md for release history
- (Phase 9) Update design comparison doc (09-design-comparison.md) with current architecture
- (future) API documentation for the Web UI routes (OpenAPI/Swagger)

## Developer Experience
- (Phase 9) `just dev-ui` recipe with `--reload` for live CSS iteration
- (Phase 9) Shared `requirements-dev.txt` or common `[dev]` extra across all services
- (Phase 9) `just status` recipe — combined container health + stream depths + DLQ + system:halted
- (Phase 9) Pre-commit mypy hook (currently removed — was too slow)
- (future) VS Code workspace file with per-service Python interpreters
- (future) Hot-module reload for UI without full container restart

## Architecture
- (Phase 9) Extract stream name constants to `lol_pipeline.constants` module
- (Phase 9) Extract `_resolve_puuid` to common library (duplicated in seed, admin, UI)
- (Phase 9) Redis `maxmemory` + `noeviction` policy in compose
- (future) Delay Scheduler atomic dispatch (Lua XADD+ZREM or ZPOPMIN)
- (future) Correlation/trace ID propagating through all pipeline messages
- (future) S3 backend for RawStore (currently Redis + disk only)
