# Design: HF Data Storage & Restoration Validation

**Status**: Planning
**Dataset**: `CityPopper/LoL-Scraper` (private)

---

## Context

The upload/download pipeline was simplified — anonymization removed since the dataset is now private. This exposes gaps in test coverage and leaves one broken test file. This document tracks the work needed to validate data storage and restoration end-to-end.

---

## Current State

| File | State |
|------|-------|
| `scripts/anonymize_and_upload.py` | Simplified — anonymization removed, now just uploads JSONL.ZST directly |
| `scripts/download_seed.py` | Unchanged — downloads JSONL.ZST + dump.rdb from HF |
| `tests/scripts/test_anonymize.py` | **Broken** — imports `_anon_puuid`, `_anonymize_record`, `_is_already_anonymized` (all deleted) |
| `tests/scripts/test_download_seed.py` | Covers 4 pure helper functions; `main()` branches untested |
| `tests/integration/test_it_seed.py` | E2E mocked HF + real Redis; upload path tested; download path not tested |

---

## Tasks

### HFV-1 — Delete broken test file
**File**: `tests/scripts/test_anonymize.py`
**Action**: Delete entirely. Every function it tests was removed. Keeping it causes collection-time `ImportError` that blocks CI.
- [ ] **Done:** File deleted

---

### HFV-2 — Upload script unit tests
**File**: `tests/scripts/test_upload.py` (new)

Tests for the three remaining testable functions in `anonymize_and_upload.py`.
No network, no Docker. All use `tmp_path` + `unittest.mock`.

#### `_is_anomalous_date` (pure, 5 tests)
| Test | Input | Expected |
|------|-------|----------|
| year before 2020 | `"1970-01.jsonl.zst"` | `True` |
| boundary year 2020 | `"2020-01.jsonl.zst"` | `False` (boundary is `< 2020`) |
| normal modern year | `"2024-03.jsonl.zst"` | `False` |
| no date prefix | `"matches.jsonl.zst"` | `False` (no exception) |
| year 1999 | `"1999-12.jsonl.zst"` | `True` |

#### `_upload_file` (mock api, 2 tests)
| Test | Asserts |
|------|---------|
| calls api with correct args | `api.upload_file` called once with `path_in_repo="NA1/<filename>"`, correct `repo_id`, `repo_type="dataset"`, `token` |
| NA1/ prefix always prepended | bare filename never passed to HF |

Mock via `unittest.mock.MagicMock()` for `api`. No filesystem needed.

#### `_process_file` (real zst + mock upload, 4 tests)
| Test | Asserts |
|------|---------|
| returns correct record count | 3-line zst → returns `3` |
| skips blank lines | 2 real lines + 1 blank → returns `2` |
| calls upload exactly once | mock `_upload_file` called once with correct path |
| empty file | returns `0`, upload still called once |

Mock via `unittest.mock.patch("anonymize_and_upload._upload_file")`.

- [ ] **Green:** All 11 tests pass; `ruff` clean
- [ ] **Commit:** `test(upload): add unit tests for upload script functions`

---

### HFV-3 — Round-trip integrity test
**File**: `tests/scripts/test_upload.py` (same file as HFV-2)

Validates that a file produced by the upload pipeline passes the download script's integrity check.

**Test: `test_round_trip_zst_integrity`**
1. Build 2-record JSONL payload
2. Compress with `zstd.ZstdCompressor` into `tmp_path / "2024-06.jsonl.zst"`
3. Call `_process_file` with mocked `_upload_file`
4. Call `download_seed._validate_zst(zst_path)`
5. Decompress and parse lines

**Asserts:**
- `_validate_zst` returns `True`
- Decompressed line count equals 2
- Each line parses as valid JSON after splitting on `\t`

Imports both `anonymize_and_upload` and `download_seed` in the same test (both on `sys.path` via `conftest.py`).

- [ ] **Green:** Round-trip test passes
- [ ] **Commit:** included with HFV-2

---

### HFV-4 — Download script `main()` branch coverage
**File**: `tests/scripts/test_download_seed.py` (add 6 tests)

`main()` is currently untested. Key branches:

| Test | Mechanism | Asserts |
|------|-----------|---------|
| `test_main_skips_when_already_downloaded` | monkeypatch `_already_downloaded → True`; patch `snapshot_download` to raise if called | returns `0`; `snapshot_download` never called |
| `test_main_force_flag_bypasses_skip` | `_already_downloaded → True`; `sys.argv = ["...", "--force"]`; mock `snapshot_download` | `snapshot_download` called once |
| `test_main_missing_huggingface_hub_returns_1` | patch `snapshot_download` import to raise `ImportError` | returns `1` |
| `test_main_404_error_returns_1` | `snapshot_download` raises `Exception("404 not found")` | returns `1` |
| `test_main_moves_zst_files_and_validates` | mock `snapshot_download` writes valid .zst to `tmp_dir/NA1/`; patch `DATA_DIR`, `DUMP_PATH` | returns `0`; file at `DATA_DIR`; `_validate_zst` passes |
| `test_main_warns_on_corrupt_zst` | same but write corrupt bytes; capture stdout | returns `0` (no abort); stdout contains `WARNING` and filename |

`snapshot_download` mock: patch via `unittest.mock.patch("huggingface_hub.snapshot_download")` since it is imported inside `main()` at call time.

`test_main_moves_zst_files_and_validates` is the most critical — it verifies the `shutil.move` destination logic.

- [ ] **Green:** All 6 tests pass
- [ ] **Commit:** `test(download): add main() branch coverage`

---

## Out of Scope

- Live network test against real `CityPopper/LoL-Scraper` — needs `HUGGINGFACE_TOKEN`; belongs in a separate `@pytest.mark.e2e` suite, not this plan
- Re-testing `_load_env_token` — already covered in `test_it_seed.py::TestTokenLoading`
- SEED-7 execution (manual workflow, tracked separately in TODO.md)

---

## Execution Order

```
HFV-1 (unblock CI)
  └─► HFV-2 + HFV-3 (new upload tests, single commit)
        └─► HFV-4 (download main() tests)
```
