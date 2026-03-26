# TODO — Open Work Items

---

## SEED-7 — Generate anonymized dump.rdb + upload to HF Datasets
**Decisions**: D-1 (reversed), D-7. One-time workflow to create the anonymized Redis snapshot.
**Dependency**: SEED-1 must complete first (local JSONL.ZST files must be anonymized before pipeline processes them).
**Fix**: After SEED-1 completes:
1. Start a fresh Redis with no data: `docker compose down -v && just up --no-seed`
2. Run seed_from_disk.py with anonymized JSONL.ZST → pipeline processes all matches
3. Wait for pipeline to drain (check `LLEN stream:parse == 0`, `LLEN stream:analyze == 0`)
4. Take snapshot: `docker compose exec redis redis-cli BGSAVE && sleep 5`
5. Copy dump: `cp redis-data/dump.rdb /tmp/seed-dump.rdb`
6. Upload to HF: `HfApi().upload_file(path_or_fileobj="/tmp/seed-dump.rdb", path_in_repo="dump.rdb", repo_id=repo_id, repo_type="dataset")`
7. Verify: check HF shows `dump.rdb` with expected size
This is a manual/one-shot operation, not a script. Document steps in `workspace/design-seed-data.md`.
- [ ] **Green:** Execute steps 1-7; HF Datasets contains both JSONL.ZST files + dump.rdb — Human action required
- [ ] **Refactor:** Confirm dump loads correctly: fresh Redis start + `DBSIZE` > 0 after mount — Human action required

---

## WATERFALL-1 — Generic foundation (sources/ subpackage)
**Goal**: Create the source-agnostic infrastructure in lol-pipeline-common.
**Files**:
- `lol-pipeline-common/src/lol_pipeline/sources/__init__.py`
- `lol-pipeline-common/src/lol_pipeline/sources/base.py` — DataType alias, FetchContext, Source protocol, FetchResult, FetchResponse, Extractor protocol
- `lol-pipeline-common/src/lol_pipeline/sources/registry.py` — SourceRegistry, SourceEntry (name regex, primary_for, startup cross-check using (source.name, dt) pairs)
- `lol-pipeline-common/src/lol_pipeline/sources/blob_store.py` — BlobStore (per-blob files, atomic writes, path traversal prevention, find_any with source_names priority order, JSONDecodeError handled as cache miss)
**Design**: See `workspace/design-source-waterfall.md` Sections 3.1–3.4, 3.7
- [x] **Green:** Implement all files above; all coordinator unit tests (Section 10.1) pass with mock sources only
- [x] **Refactor:** No source-specific conditionals anywhere in base.py, registry.py, blob_store.py
- [x] **Commit:** `feat(common): add sources/ generic foundation (base, registry, BlobStore)`

---

## WATERFALL-2 — WaterfallCoordinator + try_token()
**Goal**: Implement the coordinator and the non-blocking rate-limit check.
**Files**:
- `lol-pipeline-common/src/lol_pipeline/sources/coordinator.py` — WaterfallCoordinator (source-agnostic loop, pre-persist can_extract() gate, retry_after_ms propagation, blob_validation_failed immediate DLQ routing, MAX_BLOB_SIZE_BYTES guard)
- `lol-pipeline-common/src/lol_pipeline/rate_limiter_client.py` (or rate_limiter.py) — add `try_token()` non-blocking companion (uses key_prefix not region; returns bool; one Redis round-trip)
**Design**: See `workspace/design-source-waterfall.md` Sections 3.5 (try_token spec), 3.9
- [x] **Green:** WaterfallCoordinator passes all test cases in Section 10.1 table using mock sources only; try_token() unit tested
- [x] **Refactor:** No isinstance checks, no source name comparisons in coordinator loop
- [x] **Commit:** `feat(common): add WaterfallCoordinator and try_token() non-blocking rate check`

---

## WATERFALL-3 — RiotSource
**Goal**: Implement the Riot API source, wrapping the existing RiotClient.
**Files**:
- `lol-pipeline-common/src/lol_pipeline/sources/riot/__init__.py`
- `lol-pipeline-common/src/lol_pipeline/sources/riot/source.py` — RiotSource (uses try_token with key_prefix=f"ratelimit:{context.region}", primary_for=frozenset({MATCH}))
**Design**: See `workspace/design-source-waterfall.md` Section 3.5
- [x] **Green:** RiotSource unit tests pass; correct error mapping (429→THROTTLED, 403→AUTH_ERROR, 404→NOT_FOUND, 5xx→SERVER_ERROR); try_token correctly called with key_prefix
- [x] **Refactor:** RiotClient is wrapped, not modified
- [x] **Commit:** `feat(common): add RiotSource wrapping existing RiotClient`

---

## WATERFALL-4 — Op.gg ETL fix + OpggSource
**Goal**: Fix the gameStartTimestamp gap and implement the op.gg source package.
**Files**:
- `lol-pipeline-common/src/lol_pipeline/sources/opgg/__init__.py`
- `lol-pipeline-common/src/lol_pipeline/sources/opgg/source.py` — OpggSource (returns UNAVAILABLE; no direct match-by-ID)
- `lol-pipeline-common/src/lol_pipeline/sources/opgg/extractors.py` — OpggMatchExtractor (can_extract checks required fields; extract produces Riot-shaped dict)
- `lol-pipeline-common/src/lol_pipeline/sources/opgg/transformers.py` — op.gg → Riot shape (emits both gameCreation and gameStartTimestamp; emits gameVersion="")
**Design**: See `workspace/design-source-waterfall.md` Sections 3.6, 3.8
- [x] **Green:** Extractor tests pass against fixture blobs; transformer emits gameStartTimestamp and gameVersion; ETL gap confirmed fixed
- [x] **Refactor:** Transformer wraps/extends _opgg_etl.py without modifying it
- [x] **Commit:** `feat(common): add OpggSource, OpggMatchExtractor, and op.gg→Riot transformer`

---

## WATERFALL-5 — Fetcher integration
**Goal**: Replace _fetch_match() body with WaterfallCoordinator.
**Files**:
- `lol-pipeline-fetcher/src/lol_fetcher/main.py` — replace _fetch_match() (build FetchContext, call coordinator, handle all WaterfallResult statuses including blob_validation_failed immediate DLQ routing)
**Design**: See `workspace/design-source-waterfall.md` Section 4
- [x] **Green:** Fetcher unit tests pass; with SOURCE_WATERFALL_ORDER=riot (default) behavior is identical to current; integration test IT-WF-01 passes
- [x] **Refactor:** _try_opgg(), per-source RawStore switching, and inline Riot API call removed
- [x] **Commit:** `feat(fetcher): replace _fetch_match() with WaterfallCoordinator`

---

## WATERFALL-6 — Config + Docker
**Goal**: Add config fields and Docker volume for BlobStore.
**Files**:
- `lol-pipeline-common/src/lol_pipeline/config.py` — add source_waterfall_order: str = "riot", blob_data_dir: str = ""
- `docker-compose.yml` — add blob-data volume to fetcher service
- `.env.example` — add SOURCE_WATERFALL_ORDER, BLOB_DATA_DIR
**Design**: See `workspace/design-source-waterfall.md` Sections 7, 8 Phase 5
- [x] **Green:** Config fields load from env correctly; fetcher starts with default config (riot only)
- [x] **Refactor:** No hardcoded source names in config
- [x] **Commit:** `feat(config): add SOURCE_WATERFALL_ORDER and BLOB_DATA_DIR config fields`

---

## WATERFALL-7 — Live op.gg integration tests
**Goal**: Validate the full op.gg blob → extractor → transformer → canonical shape pipeline against the real op.gg API.
**Design**: See `workspace/design-source-waterfall.md` Section 10.6.
**File**: `tests/integration/test_opgg_live.py`
**Gate**: Tests run only when `OPGG_LIVE_TESTS=1` is set. Never run in CI.
**Test cases**:
1. Real API shape — response matches what `OpggMatchExtractor.can_extract()` expects; catches undocumented API changes
2. Real rate limit enforcement — 1 req/s respected, zero 429 responses during normal operation
3. Real blob disk write — `BlobStore.write()` produces a valid JSON file; `BlobStore.read()` round-trips it
4. Real canonical output — transformer produces a dict that passes the match-v5 schema validator (`contracts/schemas/`)
**Run manually**:
```bash
OPGG_LIVE_TESTS=1 pytest tests/integration/test_opgg_live.py -v
```
**Notes**:
- Use `tmp_path` fixture for blob directory (auto-cleanup)
- `@pytest.mark.timeout(60)` per test (overrides project-wide 10s limit for network tests)
- One retry on 429 before failing (extract `Retry-After` header or sleep 2s)
**Dependency**: WATERFALL-4 complete (OpggMatchExtractor and transformer must exist)
- [x] **Green:** `tests/integration/test_opgg_live.py` written (352 lines, ruff clean); runs when `OPGG_LIVE_TESTS=1` — Human must verify with real network
- [ ] **Verify:** Run before any release that touches the op.gg extractor or transformer — Human action required
- [x] **Commit:** included in `fix(waterfall): Phase 6 prod review fixes + integration/live tests`

---

## CLEANUP-1 — Remove Redis-based rate limiter (superseded by HTTP rate limiter service)
**Context**: The pipeline migrated from a Redis-based rate limiter (`rate_limiter.py`, `_rate_limiter_data.py`) to a dedicated HTTP rate limiter service (`lol-pipeline-rate-limiter/`). The Redis-based implementation is no longer used. Once all WATERFALL tasks are complete and the waterfall sources confirm they use `try_token()` from `rate_limiter_client.py` (HTTP-based), the Redis rate limiter can be removed.
**Files to delete/clean up**:
- `lol-pipeline-common/src/lol_pipeline/rate_limiter.py` (if still present — already deleted per git status)
- `lol-pipeline-common/src/lol_pipeline/_rate_limiter_data.py` (if still present — already deleted)
- `lol-pipeline-common/tests/unit/test_rate_limiter.py` (already deleted)
- Any remaining imports of `wait_for_token`, `acquire_token` from the old module
- Integration tests `test_it07_rate_limit.py`, `test_it12_concurrent_rate_limit.py` — update to use HTTP rate limiter service or remove
**Dependency**: Complete after WATERFALL-5 (Fetcher integration) to confirm no code still imports the old rate limiter.
- [x] **Audit:** `grep -r "from lol_pipeline.rate_limiter" .` returns no hits — confirmed 0 stale imports
- [x] **Remove:** No files to remove; old files already deleted, no dead imports found
- [x] **Update:** `test_it07_rate_limit.py` and `test_it12_concurrent_rate_limit.py` already rewritten for HTTP rate limiter service
- [x] **Commit:** Nothing to commit — migration was already clean
