# TODO — Open Work Items

---

## SEED-7 — Generate dump.rdb + upload to HF Datasets
**Decisions**: D-1 (reversed), D-7. One-time workflow to create the Redis snapshot.
**Fix**:
1. Start a fresh Redis with no data: `docker compose down -v && just up --no-seed`
2. Run seed_from_disk.py with JSONL.ZST → pipeline processes all matches
3. Wait for pipeline to drain (check `LLEN stream:parse == 0`, `LLEN stream:analyze == 0`)
4. Take snapshot: `docker compose exec redis redis-cli BGSAVE && sleep 5`
5. Copy dump: `cp redis-data/dump.rdb /tmp/seed-dump.rdb`
6. Upload to HF: `HfApi().upload_file(path_or_fileobj="/tmp/seed-dump.rdb", path_in_repo="dump.rdb", repo_id="CityPopper/LoL-Scraper", repo_type="dataset", private=True)`
7. Verify: check HF shows `dump.rdb` with expected size
This is a manual/one-shot operation, not a script. Document steps in `workspace/design-seed-data.md`.
- [ ] **Green:** Execute steps 1-7; Private HF dataset (CityPopper/LoL-Scraper) contains both JSONL.ZST files + dump.rdb — Human action required
- [ ] **Refactor:** Confirm dump loads correctly: fresh Redis start + `DBSIZE` > 0 after mount — Human action required

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
