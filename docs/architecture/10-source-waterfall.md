# Source Waterfall

## Purpose

Riot API rate limits are a hard ceiling on crawl throughput. When the per-second or per-two-minute window is saturated, the fetcher previously stalled until the next slot opened. The source waterfall eliminates that stall by routing match-fetch requests through a priority-ordered sequence of data sources. If the Riot API is throttled, the coordinator immediately tries the next source — for example, a BlobStore cache populated by op.gg data — without sleeping. This decouples pipeline throughput from any single API's availability.

---

## Components

**WaterfallCoordinator** (`lol-pipeline-common/src/lol_pipeline/sources/coordinator.py`) is the source-agnostic orchestrator. It holds no source-specific logic: no `isinstance` checks, no source name comparisons. It operates exclusively through the `Source` and `Extractor` protocols, a `SourceRegistry` for ordered source iteration, and a `BlobStore` for disk-cached intermediate blobs. The coordinator exposes a single method, `fetch_match()`, which returns a `WaterfallResult` describing whether data was found, which source provided it, and how to route failures.

**BlobStore** (`lol-pipeline-common/src/lol_pipeline/sources/blob_store.py`) is a per-blob disk cache for raw source responses. It stores blobs at `{BLOB_DATA_DIR}/{source_name}/{platform}/{match_id}.json` using atomic tmpfile-fsync-os.replace() writes with write-once semantics. It is activated by setting the `BLOB_DATA_DIR` env var; when empty, all operations are no-ops. See `docs/architecture/04-storage.md` (BlobStore section) for path layout, write semantics, and size limits.

**SourceRegistry** (`lol-pipeline-common/src/lol_pipeline/sources/registry.py`) is an ordered, validated collection of `SourceEntry` objects. Each entry carries a `name`, a `Source` implementation, a `priority` integer (lower = higher priority), and a `primary_for` set declaring which data types this source is authoritative for. At construction the registry cross-checks that every `(source_name, data_type)` pair declared by registered sources has a matching extractor — typos are caught at startup rather than silently dropped at runtime. The `sources_for(data_type)` method returns entries in priority order in O(1).

**Source and Extractor protocols** (`lol-pipeline-common/src/lol_pipeline/sources/base.py`) define the contracts all implementations must satisfy. A `Source` fetches raw bytes from a remote API and maps errors to `FetchResult` values (`SUCCESS`, `THROTTLED`, `NOT_FOUND`, `AUTH_ERROR`, `SERVER_ERROR`, `UNAVAILABLE`). An `Extractor` takes a raw blob dict, validates it with `can_extract()`, and transforms it into the canonical pipeline-ready JSON dict via `extract()`. `FetchContext` carries the context fields available to all sources (`match_id`, `puuid`, `region`, and an open `extra` dict). `DataType` is an open string alias — sources declare their supported types; the coordinator never switches on the value.

**RiotSource** (`lol-pipeline-common/src/lol_pipeline/sources/riot/source.py`) wraps the existing `RiotClient`. It calls `try_token(source="riot", endpoint="match")` for a non-blocking rate-limit check before each fetch. On `FetchResult.THROTTLED` the coordinator falls through immediately without sleeping. Riot blobs are already in the canonical shape (`info` + `metadata` top-level keys), so `RiotExtractor.extract()` is a pass-through.

**OpggSource** (`lol-pipeline-common/src/lol_pipeline/sources/opgg/source.py`) always returns `UNAVAILABLE` for direct fetches because op.gg has no match-by-Riot-ID endpoint. Its value in the waterfall comes entirely from the BlobStore cache path: blobs previously written when op.gg data was collected are found by the coordinator in step 2 (blob cache check) before any remote source is contacted.

---

## Algorithm

`WaterfallCoordinator.fetch_match()` executes four steps in order:

1. **RawStore idempotency check.** If `raw_store.exists(match_id)` returns `True`, the match is already in the canonical store. Return `WaterfallResult(status="cached")` immediately — no fetch, no extraction.

2. **BlobStore cache check.** If `BLOB_DATA_DIR` is set, call `blob_store.find_any(match_id, registry.source_names)`. This checks each source's subdirectory in priority order and returns the first valid cached blob. If found, the coordinator runs `can_extract()` and `extract()` on it, writes the result to RawStore, and returns `WaterfallResult(status="cached", source=...)`. Corrupt blobs (JSON parse error) and blobs that fail `can_extract()` are treated as cache misses and iteration continues.

3. **Source iteration.** For each source supporting the requested `DataType` (in priority order): (a) check `required_context_keys`; skip with a warning if any are missing. (b) call `source.fetch(context, data_type)`. On `SUCCESS`: validate blob size (2 MB limit), find extractor, run `can_extract()`, persist blob to BlobStore, run `extract()`, write to RawStore, return `WaterfallResult(status="success")`. On `THROTTLED`, `UNAVAILABLE`, or `SERVER_ERROR`: log and continue to next source. On `NOT_FOUND` or `AUTH_ERROR`: terminal only if the source is `primary_for` the data type; otherwise treated as `UNAVAILABLE`.

4. **All-exhausted routing.** If all sources are tried without success, return `WaterfallResult(status="all_exhausted", retry_after_ms=..., blob_validation_failed=...)`. The `retry_after_ms` field carries the largest hint from any `THROTTLED` response. The `blob_validation_failed` flag is set when any source returned `SUCCESS` but `can_extract()` rejected the blob; the fetcher uses this to route to the DLQ with `max_attempts=1`.

---

## Source Lifecycle

To add a new source to the waterfall:

1. Create a source package under `lol-pipeline-common/src/lol_pipeline/sources/{name}/` containing a `Source` implementation (maps errors to `FetchResult`) and an `Extractor` implementation (validates and transforms blobs).
2. Register the source in the `SourceRegistry` by adding a `SourceEntry` in the fetcher's `main.py` startup path, driven by the `SOURCE_WATERFALL_ORDER` config. The name used in `SourceEntry` must match the `source.name` property exactly.
3. Declare which data types the source supports in `source.supported_data_types`. The registry's startup cross-check will raise `ValueError` at boot if a matching extractor is not registered.
4. Mark the source as `primary_for` the appropriate data types only if `NOT_FOUND` and `AUTH_ERROR` from this source should be terminal. Most supplementary sources should leave `primary_for` empty.

---

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `SOURCE_WATERFALL_ORDER` | `riot` | Comma-separated source names in priority order (first = highest priority). Example: `riot,opgg`. Unknown names cause a startup validation error. |
| `BLOB_DATA_DIR` | `""` (disabled) | Disk directory for raw source blobs. Empty string disables BlobStore entirely. Set to `/blob-data` in Docker (volume mount required). |
| `OPGG_ENABLED` | `false` | Whether to include the op.gg source in the waterfall. Must be paired with a non-empty `SOURCE_WATERFALL_ORDER` that includes `opgg`. |

The fetcher container in `docker-compose.yml` sets `BLOB_DATA_DIR=/blob-data` and `SOURCE_WATERFALL_ORDER=${SOURCE_WATERFALL_ORDER:-riot}`.

---

## Error Semantics

| WaterfallResult status | Fetcher action |
|------------------------|---------------|
| `success` | Publish to `stream:parse`; match proceeds normally |
| `cached` | Match already in RawStore; publish to `stream:parse` as normal |
| `not_found` | Write `match:{match_id}.status = not_found`; no DLQ |
| `auth_error` | Set `system:halted`; re-queue message for retry after manual resolution |
| `all_exhausted` (no `blob_validation_failed`) | Route to DLQ with `retry_after_ms` hint; Recovery will re-dispatch after the delay |
| `all_exhausted` + `blob_validation_failed=True` | Route to DLQ with `max_attempts=1`; exhausted immediately without retry (blob is permanently malformed) |
