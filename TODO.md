# TODO — Open Work Items

---

## Low

### CLI symbols debate (P11-DD-8)

CLI uses `[OK]`/`[ERROR]` text. Design director prefers checkmark/x-mark symbols.
Deferred: ASCII-safe vs Unicode.

---

## Deferred (Phase 14+)

- P14-SEC-2: CSRF protection for `/dlq/replay/{id}` (needs token infrastructure)
- P14-ARC-4: Migrate 5 config values to pydantic `Config`
- P14-FV-1: Analyzer cursor stalls on expired participant data
- P14-FV-4: Analyzer premature priority clear on partial match data
- P14-FV-5: Parser analyze pipeline partial-XADD + raw-blob-expiry compound failure
- P14-FV-8: Recovery 404 discards with no audit trail
- P14-PM-4/PM-6: `cmd_dlq_list` table mode + `dlq clear` preflight scope line
- P14-UX-4/12: DLQ pagination total count + cursor-based pagination
- P14-UX-6: Dashboard double-queries `stream:dlq`
- P14-WD/UX ARIA: nav aria-label, aria-current, role="alert", form label pairing
- P14-RD-*: Responsive CSS improvements
- P14-DD-*: Design system cleanup (rgba tokens, h2/h3 rules, spacing scale)
- P14-GD-*: CLI output formatting (DLQ table borders, stats JSON, progress signals)
- P14-DX-4-13: DevEx improvements (conftest.py, pre-commit mypy, parallel check)
- P14-DOC-4/5/7/8/12-18: Large env var table updates, storage schema, deployment docs
- P14-DBG-6: rate_limiter stored-limit keys not scoped to key_prefix

---

## Fuzzing Targets (Hypothesis property-based tests)

- `MessageEnvelope.from_redis_fields` — random subsets of keys, random value types, round-trip identity
- `DLQEnvelope.from_redis_fields` — random subsets, extra keys, null values, `retry_after_ms` parsing
- `riot_api._raise_for_status` — status codes 100-599, malformed `Retry-After` header
- `_derived` (analyzer) — missing keys, zero/negative values, ZeroDivisionError guard
- `_parse_match` (parser) — random bytes, truncated JSON, missing required fields
- `_format_stat_value` (UI) — `"nan"`, `"inf"`, `""`, very long strings, unicode
- `_badge` (UI) — invalid variants, HTML/JS injection in text
- Redis key construction — unicode, colons, newlines, null bytes in PUUIDs/match_ids
- `_parse_log_line` (UI) — arbitrary strings, nested JSON, binary data
- `_validate` (parser) — deeply nested dicts, missing `info`/`metadata`, non-dict types
- `RawStore._search_bundle_file` — corrupted JSONL bundles, lines with no tab separator

---

## Integration Test Scenarios (not yet implemented)

- **IT-14:** Full pipeline E2E: seed -> crawl -> fetch -> parse -> analyze -> UI displays stats

---

## Feature: Champion Build Recommendations

The single largest feature gap vs OP.GG/U.GG. Pipeline already collects items, runes, skill
order, and summoner spells per participant but never aggregates or displays them.

**Components:**
1. Analyzer: new aggregation keys (`champion:builds:*`, `champion:runes:*`, `champion:skills:*`, `champion:spells:*`)
2. UI: `/champions/{name}` build section with DDragon icons
3. No new streams or envelope changes needed

**Complexity:** Medium (~300 lines). **Risk:** Low (additive, no existing changes).

---

## Security (open items)

- UI `player:name:` cache has no TTL — unbounded memory growth
- UI auto-seed has no rate limiting — unlimited `publish()` calls per anonymous user
- No input validation on `region` parameter in UI
- Admin CLI `_resolve_puuid` prints unsanitized input to stderr (terminal injection)
- Redis ACLs — per-service users with minimal permissions
- TLS reverse proxy docs (Caddy/nginx)
- Redis TLS (`rediss://`) for production

---


## UI/UX (open items)

- Bugfix: switching language/theme should not navigate to a different page afterwards (stay on current page)
- Audit all fallback/default values — replace with explicit errors. No silent fallbacks to magic strings/numbers.
- Wire `lol_pipeline.i18n.label()` into all UI displays of roles, tiers, queues (currently raw English codes)
- README: Player Stats screenshot should show an actual player with sufficient entries to showcase
- Render skip-to-content `<a>` (`.skip-link` CSS exists but no element uses it)
- Wire up gauge/progressbar for stream depths (CSS defined but never rendered)
- Match detail page (click a match row for full participant data)
- Player comparison view (side-by-side stats)
- `/players`: server-side sort controls (name, region, date)
- `/stats`: sparkline for win rate trend
- Toast notifications for seed instead of page reload
- WebSocket for `/logs` and `/streams` (replace polling)
- Dark/light theme toggle
- Export stats as CSV/JSON

---

## Infrastructure (open items)

- `docker-compose.prod.yml` (baked images, `--requirepass`, resource limits, log rotation)
- Redis `maxmemory 4gb` + `noeviction` policy in compose
- Integration test CI job (testcontainers)
- Trivy image scanning in CI
- Prometheus + Redis Exporter + Grafana monitoring stack
- `pip-audit` in CI for dependency scanning
- Kubernetes Helm chart
- GitHub Actions deploy workflow

---

## Performance (open items)

- RawStore `_exists_in_bundles` scans all JSONL files — redundant full-file scan in `set()`
- RawStore: sorted JSONL bundles + binary search (future)
- Discovery batch pipelining when batch_size > 10 (future)
- `pytest-xdist` parallel test execution across all services
