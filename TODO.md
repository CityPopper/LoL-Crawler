# TODO — Improvement Proposals (All 21 Agents)

Phase 7 "IRONCLAD" and Phase 8 "FACELIFT" are **complete**.
546 unit tests + 61 contract tests. 21 agentic coding agents.

---

## UI/UX Improvements

- (Phase 9) CSS spinner/loading animation for match history lazy-load and streams auto-refresh
- (Phase 9) Global halt banner on ALL pages — currently only /stats and /streams check `system:halted`
- (Phase 9) Render skip-to-content `<a>` — `.skip-link` CSS exists but no element uses it
- (Phase 9) Wire up gauge/progressbar for stream depths — CSS defined but never rendered
- (Phase 9) DLQ page: inline replay button per entry (POST /dlq/replay/{id})
- (Phase 9) DLQ page: pagination — currently hard-capped at 50 entries
- (Phase 9) Home dashboard at `/` — system status cards, recent seeds, stream overview
- (Phase 9) Match detail page — click a match row for full participant data
- (Phase 9) Player comparison view — side-by-side stats
- (Phase 9) /players: server-side sort controls (name, region, date)
- (Phase 9) /stats: sparkline for win rate trend
- (Phase 9) Toast notifications for seed instead of page reload
- (future) Static CSS file with browser caching
- (future) WebSocket for /logs and /streams (replace polling)
- (future) Dark/light theme toggle
- (future) Export stats as CSV/JSON
- (future) Keyboard shortcuts (/ for search, r for refresh)

## Infrastructure

- (Phase 9) `docker-compose.prod.yml` — baked images, `--requirepass`, resource limits, log rotation
- (Phase 9) `MAXLEN ~10000` on XADD in `publish()` — streams grow unbounded
- (Phase 9) Redis `maxmemory 4gb` + `noeviction` policy in compose
- (Phase 9) Integration test CI job for IT-01 through IT-07 (testcontainers)
- (Phase 9) Trivy image scanning in CI
- (Phase 9) Docker build layer caching (`actions/cache`)
- (Phase 9) Prometheus + Redis Exporter + Grafana monitoring stack
- (Phase 9) `pip-audit` in CI for dependency scanning
- (future) Kubernetes Helm chart
- (future) GitHub Actions deploy workflow
- (future) Container registry with tagged releases

## Security

- (Phase 9) Redis ACLs — per-service users with minimal permissions
- (Phase 9) TLS reverse proxy docs (Caddy/nginx config)
- (Phase 9) Content-Security-Policy header with nonce-based `script-src`
- (Phase 9) Rate limiting on UI auto-seed endpoint
- (Phase 9) Redis TLS (`rediss://`) for production
- (Phase 9) Redis `requirepass` in dev compose
- (future) Authentication / API gateway for Web UI
- (future) Audit log for admin operations

## Performance

- (Phase 9) Cap `discover:players` sorted set — ZREMRANGEBYRANK to bound growth
- (Phase 9) Delay Scheduler: atomic XADD+ZREM via Lua
- (Phase 9) Player index sorted set (`players:all`) to replace SCAN on /players
- (Phase 9) Raw blob TTL/eviction — `raw:match:*` dominates memory at scale
- (future) RawStore: sorted JSONL bundles + binary search
- (future) Discovery batch pipelining when batch_size > 10
- (future) Redis connection pool tuning docs

## Testing

- (Phase 9) Integration tests IT-08 through IT-11 (priority queue end-to-end)
- (Phase 9) Shared test fixtures in conftest.py (FakeRedis, Config, envelope factory)
- (Phase 9) Parallel contract test runner
- (Phase 9) UI route integration tests with TestClient + fakeredis
- (Phase 9) E2E smoke test: seed → crawl → fetch → parse → analyze
- (Phase 9) Coverage enforcement `--cov-fail-under=80` in CI
- (future) Playwright browser tests for UI regression
- (future) Load testing with locust

## Documentation

- (Phase 9) Discovery + LCU service READMEs
- (Phase 9) CONTRIBUTING.md
- (Phase 9) CI workflow guide
- (Phase 9) Discovery architecture doc
- (Phase 9) Update design comparison doc (stale claims)
- (Phase 9) CHANGELOG.md
- (future) OpenAPI/Swagger for UI routes
- (future) Architecture Decision Records (ADRs)

## Developer Experience

- (Phase 9) Admin CLI `--json` flag
- (Phase 9) `just dev-ui` recipe with `--reload`
- (Phase 9) `just status` recipe (health + streams + DLQ + halted)
- (Phase 9) Shared `requirements-dev.txt` across services
- (future) VS Code devcontainer
- (future) Hot-module reload for all services

## Architecture

- (Phase 9) Extract stream name constants to `lol_pipeline.constants`
- (Phase 9) Extract `_resolve_puuid` to common (duplicated in seed, admin, UI)
- (Phase 9) Configurable priority TTL (`PRIORITY_TTL_SECONDS`)
- (Phase 9) Document Delay Scheduler single-instance assumption
- (future) Correlation/trace ID through pipeline messages
- (future) Event sourcing for replay/audit
- (future) Circuit breaker for Riot API
- (future) S3 backend for RawStore
