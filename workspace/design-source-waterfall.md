# Design: Source Waterfall

## 1. Problem Statement

The pipeline currently fetches match data exclusively from the Riot API. When the Riot API rate-limits the pipeline (HTTP 429), messages enter the DLQ/retry cycle and progress stalls. Meanwhile, the same match data is often available from third-party sources (op.gg today, u.gg or others in the future) that have independent rate limits.

**What exists today:**

- `OpggClient` (`lol-pipeline-common/src/lol_pipeline/opgg_client.py`) fetches match history and normalizes via `_opgg_etl.py`, but it is used only at the crawler level (match discovery), not as a fallback data source for the Fetcher.
- The Fetcher (`lol-pipeline-fetcher/src/lol_fetcher/main.py:146-213`) has a `_try_opgg` function, but it only checks the RawStore cache for an existing blob -- it never fetches from op.gg on cache miss.
- The match_id payload schema (`lol-pipeline-common/contracts/schemas/payloads/match_id_payload.json`) already has a `source` field with enum `["riot", "opgg"]`, but this controls which RawStore key prefix to use, not a waterfall strategy.
- The op.gg ETL (`_opgg_etl.py`) produces `gameCreation` but the parser (`_helpers.py:37`) requires `gameStartTimestamp` -- a compatibility gap that would cause parse failures for op.gg-sourced data flowing through the full pipeline.

**What needs to change:**

The Fetcher should transparently try alternative sources when the Riot API is throttled, save the full raw response to disk, extract Riot-shaped data from it, and emit it downstream. Upstream (Crawler) and downstream (Parser, Analyzer) services should not change.

**Genericity constraint:** The waterfall mechanism must be fully source-agnostic. Adding a new source (u.gg, mobafire, or any future provider) must require ONLY:
1. A new `Source` implementation (fetcher + extractor(s) + transformer(s)) in `sources/{source_name}/`.
2. A config entry in `SOURCE_WATERFALL_ORDER`.

Nothing else should change -- no Fetcher edits, no coordinator edits, no stream schema changes, no Redis key changes.

---

## 2. Architecture Overview

```
                           Fetcher Service
                    ┌──────────────────────────────────┐
                    │                                  │
  stream:match_id──►│  WaterfallCoordinator             │──► stream:parse
                    │    │                              │
                    │    ├─ 1. Check BlobCache (disk)   │
                    │    │   └─ hit? → extract → done   │
                    │    │                              │
                    │    ├─ 2. Try Source[0]             │
                    │    │   └─ success? → store → done │
                    │    │   └─ 429/unavail → continue  │
                    │    │   └─ auth_error? → halt      │
                    │    │                              │
                    │    ├─ 3. Try Source[1]             │
                    │    │   └─ (same logic as above)   │
                    │    │                              │
                    │    ├─ ...                          │
                    │    │                              │
                    │    └─ N+1. All sources exhausted   │
                    │        └─ nack_to_dlq              │
                    │                                  │
                    │  BlobStore (disk)                  │
                    │    {BLOB_DATA_DIR}/                │
                    │      {source.name}/{platform}/     │
                    │        {match_id}.json             │
                    │                                  │
                    └──────────────────────────────────┘
                                    │
                              (future extension)
                                    │
                                    ▼
                           stream:blob_available
                           (proactive emit of
                            additional data types)
```

The diagram is source-agnostic: the coordinator iterates over a registry of sources by priority order. The number and identity of sources is a runtime configuration concern.

### Data Flow — Happy Path (Source[0] throttled, Source[1] succeeds)

```
1. Fetcher receives {match_id: "NA1_12345", puuid: "abc...", region: "na1"} from stream:match_id
2. WaterfallCoordinator.fetch(FetchContext(match_id="NA1_12345", puuid="abc...", region="na1"), "match")
3.   Check BlobCache: blob exists at {source}/{platform}/{match_id}.json? → No
4.   Try Source[0].fetch(context, DataType MATCH)
5.     try_token() → denied (rate limit saturated)
6.     Source[0] returns THROTTLED immediately (no blocking wait)
7.   Try Source[1].fetch(context, DataType MATCH)
8.     Source[1] rate-limited independently (ratelimit:{source.name}:*)
9.     HTTP GET to source[1]'s API for match details
10.   Validate blob: extractor.can_extract(blob_dict)? → Yes
11.   Save raw response to BlobStore: {source.name}/NA1/{match_id}.json (atomic write)
12.   Run appropriate Extractor on the blob → Riot match-v5 shape
13.   Store extracted data via RawStore (existing key: raw:match:NA1_12345)
14. Publish to stream:parse; ACK stream:match_id
```

This flow is identical regardless of which sources occupy positions 0..N in the waterfall.

---

## 3. Component Designs

### 3.1 DataType (Open String Alias)

An open set of data type identifiers. Sources and extractors declare support using these values.

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/base.py`

```python
# DataType is a plain string alias — an open set.
# New sources can define their own data type constants in their own packages
# without editing this file. The coordinator never switches on DataType values.
DataType = str

# Module-level constants for well-known pipeline data types.
MATCH: DataType = "match"
BUILD: DataType = "build"
# TODO: TIMELINE can be brought into the waterfall in a future phase.
# For now, timeline fetching stays on its existing Riot-only code path.
# When that migration happens, add: TIMELINE: DataType = "timeline"
```

Using a string alias instead of an enum means:
- Adding a new data type requires only defining a new constant in the source's own package (e.g., `RUNES_PAGE: DataType = "runes_page"` in `sources/ugg/constants.py`). No shared `base.py` edits needed.
- The coordinator never switches on DataType values -- it uses them only as opaque keys for registry lookups and extractor indexing.
- IDE autocomplete still works via the module-level constants. Typos are caught by tests, not by enum membership.

### 3.2 Source Protocol

Each source must implement a minimal async protocol. The protocol contains NO source-specific assumptions -- method signatures, return types, and error handling are all source-agnostic.

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/base.py`

```python
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

@dataclass(frozen=True)
class FetchContext:
    """All information available to a source for fetching data.

    Each source uses whatever fields it needs. Riot uses match_id + region.
    Op.gg might use puuid + region. Future sources use extra for anything else.
    The coordinator builds this from the stream envelope.
    """
    match_id: str
    puuid: str
    region: str
    extra: dict = field(default_factory=dict)

class FetchResult(Enum):
    SUCCESS = "success"
    THROTTLED = "throttled"       # rate-limited; try next source
    NOT_FOUND = "not_found"       # permanent; do not try other sources (if primary_for this data_type)
    AUTH_ERROR = "auth_error"     # critical; halt system (if primary_for this data_type)
    SERVER_ERROR = "server_error" # transient; try next source
    UNAVAILABLE = "unavailable"   # source is down; try next source

@dataclass
class FetchResponse:
    result: FetchResult
    raw_blob: bytes | None = None   # full raw response from the source
    data: dict | None = None        # extracted data in canonical (Riot) shape
    retry_after_ms: int | None = None
    available_data_types: frozenset[DataType] = field(default_factory=frozenset)
    # e.g. frozenset({"match", "build"})

@runtime_checkable
class Source(Protocol):
    @property
    def name(self) -> str:
        """Unique identifier for this source (e.g. 'riot', 'opgg', 'ugg').

        Used as the BlobStore subdirectory, the source:stats key suffix,
        and the registry lookup key. Must be stable across deployments.
        Must match ^[a-z0-9_]+$ (validated at SourceEntry construction).
        """
        ...

    @property
    def supported_data_types(self) -> frozenset[DataType]:
        """Data types this source can provide.

        The coordinator uses this to skip sources that cannot provide the
        requested data_type, without making a network call.
        """
        ...

    async def fetch(
        self, context: FetchContext, data_type: DataType
    ) -> FetchResponse:
        """Fetch data for the given context and data type.

        The source is responsible for its own rate limiting, authentication,
        and error mapping. The coordinator only inspects FetchResult.

        The context provides match_id, puuid, region, and extra — each
        source uses whatever fields it needs for its lookup strategy.
        """
        ...

    async def close(self) -> None: ...
```

**Key semantics (source-agnostic):**

- `FetchContext` replaces the bare `match_id: str` parameter. Each source extracts the fields it needs from the context. Riot uses `context.match_id` and `context.region`. A source that indexes by summoner/puuid uses `context.puuid`. A source that needs additional info uses `context.extra`. The coordinator builds `FetchContext` from the stream envelope's payload fields.
- `THROTTLED` means "I am rate-limited; try the next source in the waterfall." This is the trigger for fallback. Any source can return this.
- `AUTH_ERROR` from any source whose `primary_for` set includes the requested data type (see SourceEntry below) triggers `system:halted`. Auth errors on non-primary sources are treated as `UNAVAILABLE` -- logged, not halted.
- `NOT_FOUND` from a source that is primary for the requested data type is terminal (match does not exist). `NOT_FOUND` from a non-primary source is treated as `UNAVAILABLE` (the match might exist but the third-party source does not have it).
- `available_data_types` is populated by the source after a successful fetch. It declares what the blob contains beyond what was requested. This is the hook for proactive emit (Section 5).
- `supported_data_types` is a static declaration. The coordinator checks it before calling `fetch()`, avoiding unnecessary network round-trips to sources that cannot serve the requested data type.

### 3.3 Source Registry

An ordered list of data sources, configurable via environment variable.

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/registry.py`

```python
import re

_SOURCE_NAME_RE = re.compile(r"^[a-z0-9_]+$")

@dataclass(frozen=True)
class SourceEntry:
    name: str                          # "riot", "opgg", "ugg" — must match source.name
    source: Source                     # implements Source protocol
    priority: int                      # lower = tried first
    primary_for: frozenset[DataType] = frozenset()
    # primary_for controls: NOT_FOUND terminal semantics, AUTH_ERROR → halt
    # Example: SourceEntry(source=RiotSource(), primary_for=frozenset({MATCH}))
    # Riot is authoritative for MATCH but not for BUILD.
    # A source with primary_for=frozenset() is never treated as authoritative.

    def __post_init__(self) -> None:
        if not _SOURCE_NAME_RE.match(self.name):
            raise ValueError(
                f"source name {self.name!r} must match ^[a-z0-9_]+$"
            )

class SourceRegistry:
    def __init__(self, entries: list[SourceEntry]) -> None:
        self._entries = sorted(entries, key=lambda e: e.priority)
        self._by_name: dict[str, SourceEntry] = {e.name: e for e in self._entries}

    def sources_for(self, data_type: DataType) -> list[SourceEntry]:
        """Return sources that support data_type, ordered by priority.

        Uses source.supported_data_types to filter — no source-specific
        conditionals.
        """
        return [
            e for e in self._entries
            if data_type in e.source.supported_data_types
        ]

    def get(self, name: str) -> SourceEntry | None:
        return self._by_name.get(name)

    @property
    def all_sources(self) -> list[SourceEntry]:
        return list(self._entries)
```

**Config:** `SOURCE_WATERFALL_ORDER=riot,opgg` (comma-separated, first = highest priority). Default: `riot` only. The value accepts any registered source name -- the registry construction in `main()` validates that every name in the list maps to a known source implementation.

**Design decision:** The registry is a simple ordered list, not a plugin system. Adding a new source requires adding a source package under `sources/{source_name}/` and updating the registry construction in the Fetcher's `main()`. This is appropriate for the 2-5 source scale. No coordinator changes, no stream changes, no Redis key changes.

### 3.4 Extractor Interface

Extractors declare what data types they can produce from a source's blob, and extract them into canonical (Riot-shaped) data.

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/base.py` (protocol definition)

```python
@runtime_checkable
class Extractor(Protocol):
    @property
    def source_name(self) -> str:
        """Which source's blobs this extractor handles.

        Must match the source.name of the corresponding Source implementation.
        """
        ...

    @property
    def data_types(self) -> frozenset[DataType]:
        """Data types this extractor can produce from a blob.

        The coordinator uses this to select the correct extractor for a
        given (source, data_type) pair. An extractor may support multiple
        data types from a single blob (e.g. both MATCH and BUILD from
        the same response).
        """
        ...

    def can_extract(self, blob: dict) -> bool:
        """Return True if this blob contains the data needed for extraction.

        Source-agnostic signature — the blob structure is opaque to the
        coordinator. Each extractor knows its own source's blob format.

        Called by the coordinator BEFORE persisting a blob to BlobStore.
        If False, the blob is NOT persisted and the coordinator routes
        to the next source — preventing poisoned blob loops.
        """
        ...

    def extract(self, blob: dict, match_id: str, region: str) -> dict:
        """Extract data from blob. Returns canonical Riot-shaped dict.

        Raises ExtractionError on failure. The coordinator does not inspect
        the dict — it passes it through to RawStore unchanged.
        """
        ...
```

**Key genericity points:**
- `data_types` is `frozenset[DataType]`, not a single string. This lets the coordinator query what each extractor can provide without inspecting the blob.
- The coordinator selects extractors via `extractor.source_name == source.name and data_type in extractor.data_types`. No source-specific conditionals.
- Concrete extractors live in their source's package directory (e.g., `sources/opgg/extractors.py`), not in a shared `extractors.py`.
- `can_extract()` is used as a pre-persist validation gate (see Section 3.9).

### 3.5 Riot Source (wraps existing RiotClient)

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/riot/source.py`

```python
class RiotSource:
    name = "riot"
    supported_data_types = frozenset({MATCH})

    def __init__(self, riot_client: RiotClient, redis: aioredis.Redis, cfg: Config) -> None:
        self._riot = riot_client
        self._r = redis
        self._cfg = cfg

    async def fetch(self, context: FetchContext, data_type: DataType) -> FetchResponse:
        try:
            # Non-blocking rate limit check. If no token available, return
            # THROTTLED immediately so the coordinator can try the next source
            # without waiting up to 60s.
            granted = await try_token(
                self._r,
                limit_per_second=self._cfg.api_rate_limit_per_second,
                region=context.region,
            )
            if not granted:
                return FetchResponse(result=FetchResult.THROTTLED)

            data = await self._riot.get_match(context.match_id, context.region)
            return FetchResponse(
                result=FetchResult.SUCCESS,
                raw_blob=json.dumps(data).encode(),
                data=data,
                available_data_types=frozenset({MATCH}),
            )
        except RateLimitError as exc:
            return FetchResponse(result=FetchResult.THROTTLED, retry_after_ms=exc.retry_after_ms)
        except NotFoundError:
            return FetchResponse(result=FetchResult.NOT_FOUND)
        except AuthError:
            return FetchResponse(result=FetchResult.AUTH_ERROR)
        except ServerError:
            return FetchResponse(result=FetchResult.SERVER_ERROR)
        except TimeoutError:
            return FetchResponse(result=FetchResult.THROTTLED)
```

This wraps the existing `RiotClient` without modifying it. The `RiotClient`'s circuit breaker, rate-limit header persistence, and error mapping all continue to work as before.

**Rate limiter integration:** `try_token()` is a new non-blocking function added to the rate limiter interface. It calls the same dual-window Lua script as `acquire_token()` but returns `False` immediately if no token is available, instead of sleeping and retrying. This ensures the coordinator can fall through to the next source within milliseconds when Riot's rate limit is saturated, rather than blocking for up to 60 seconds inside `wait_for_token()`.

`wait_for_token()` (blocking) is retained for use by code paths that have no fallback source -- e.g., the existing timeline fetch path, which stays outside the waterfall.

### 3.6 Op.gg Source (wraps existing OpggClient)

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/opgg/source.py`

This section documents the first non-Riot source implementation. It serves as a reference for how future sources (u.gg, mobafire, etc.) should be structured. The coordinator is unaware of any op.gg-specific details -- all specifics are encapsulated within the `sources/opgg/` package.

```python
class OpggSource:
    name = "opgg"
    supported_data_types = frozenset({MATCH, BUILD})

    def __init__(self, opgg_client: OpggClient, cfg: Config) -> None:
        self._opgg = opgg_client
        self._cfg = cfg

    async def fetch(self, context: FetchContext, data_type: DataType) -> FetchResponse:
        # Op.gg cannot look up by Riot match_id directly.
        # It requires a summoner_id and returns a page of matches.
        # This source is only usable when the match_id can be cross-referenced
        # OR when the blob is already cached (see BlobStore check in coordinator).
        #
        # FetchContext provides puuid and region, which could be used for
        # summoner-based lookup in a future enhancement.
        #
        # For now: op.gg source returns UNAVAILABLE for direct match_id lookups.
        # It is primarily useful via the BlobStore cache path (blobs saved when
        # op.gg is used at the crawler level) or via future match-detail endpoints.
        return FetchResponse(result=FetchResult.UNAVAILABLE)
```

**Critical constraint:** Op.gg's internal API fetches match history by summoner_id, not by Riot match_id. There is no known op.gg endpoint that accepts a Riot match_id and returns that single match. This means op.gg cannot serve as a direct fallback fetcher for arbitrary match_ids.

Op.gg is useful in two scenarios:
1. **Blob cache hit:** The crawler fetched match data from op.gg during match discovery (existing flow), saved the blob, and now the Fetcher finds it in the BlobStore.
2. **Future match-detail endpoint:** If op.gg adds a match-by-id endpoint (or if we reverse-engineer one), the `OpggSource.fetch()` implementation changes but nothing else does.

This constraint significantly shapes the design: the primary value of the waterfall for op.gg is the **cache-aware** path (check disk before hitting Riot API), not the fallback-fetch path. Future sources (e.g., u.gg) may not share this limitation.

### 3.7 BlobStore (Raw Blob Disk Cache)

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/blob_store.py`

The BlobStore is distinct from the existing `RawStore`. `RawStore` stores Riot-shaped normalized data. `BlobStore` stores the raw, unprocessed response from each source, keyed by `(source.name, match_id)`.

**Disk layout -- one file per blob:**
```
{BLOB_DATA_DIR}/
  {source.name}/               # directory name comes from source.name property
    {platform}/                # "NA1", "KR", "EUW1"
      {match_id}.json          # single blob file per match
```

Each blob is stored as a standalone JSON file. No JSONL bundles, no line-delimited formats.

The `{source.name}` subdirectory is always derived from the `Source.name` property at runtime -- never hardcoded. A new source automatically gets its own subdirectory when its first blob is stored.

Example paths:
- `blob-data/riot/NA1/NA1_12345.json`
- `blob-data/opgg/NA1/OPGG_NA1_abc123.json`
- `blob-data/ugg/KR/KR_67890.json` (hypothetical future source)

**ID namespace note:** Blobs are keyed by the `match_id` from the stream envelope -- the ID used by whoever discovered the match. For op.gg-originated matches, this is an op.gg ID (e.g., `OPGG_NA1_abc123`). For Riot-originated matches, this is a Riot ID (e.g., `NA1_12345`). Cross-referencing between ID namespaces is explicitly out of scope. Op.gg cache hits only apply to matches originally discovered via op.gg, where the stream envelope carries the op.gg ID. A Riot-originated `match_id` will not find an op.gg blob, and this is by design.

**Atomic writes:** Each blob is written atomically using the tmpfile-fsync-rename pattern:
1. Write to `{platform}/.tmp_{match_id}_{pid}.json`
2. `fsync()` the file descriptor
3. `os.replace(tmp_path, final_path)` -- atomic on POSIX

This eliminates the torn-write risk that JSONL bundles had with concurrent O_APPEND above PIPE_BUF. No file-level locking is needed because `os.replace()` is atomic and each match_id maps to a unique file path.

**Path traversal prevention:**
- `source.name` is validated at `SourceEntry` construction time: must match `^[a-z0-9_]+$` (see Section 3.3). A `ValueError` is raised if the pattern does not match.
- The platform segment extracted from `match_id` is validated before any path construction: must match `^[A-Z0-9]+$` via `re.match`.
- After path construction, `Path.resolve()` is called and the result is asserted to be within `BLOB_DATA_DIR`. If the assertion fails, the write is rejected.

These three checks together prevent directory traversal via crafted source names or match IDs.

**Interface:**
```python
import re

_PLATFORM_RE = re.compile(r"^[A-Z0-9]+$")

class BlobStore:
    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir).resolve() if data_dir else None

    def _validate_platform(self, platform: str) -> None:
        if not _PLATFORM_RE.match(platform):
            raise ValueError(f"invalid platform segment: {platform!r}")

    def _blob_path(self, source_name: str, match_id: str) -> Path:
        platform = match_id.split("_")[0]
        self._validate_platform(platform)
        path = (self._data_dir / source_name / platform / f"{match_id}.json").resolve()
        if not str(path).startswith(str(self._data_dir)):
            raise ValueError(f"path escapes BLOB_DATA_DIR")
        return path

    async def exists(self, source_name: str, match_id: str) -> bool:
        """O(1) stat call — no scanning."""
        return self._blob_path(source_name, match_id).exists()

    async def read(self, source_name: str, match_id: str) -> dict | None:
        """Read and parse a blob. Returns parsed JSON dict or None.

        The coordinator never calls json.loads on BlobStore output —
        this method owns the bytes-to-dict conversion.
        """
        path = self._blob_path(source_name, match_id)
        if not path.exists():
            return None
        data = await asyncio.to_thread(path.read_bytes)
        return json.loads(data)

    async def write(self, source_name: str, match_id: str, data: str) -> None:
        """Atomic write: tmpfile -> fsync -> os.replace(). Write-once semantics."""
        path = self._blob_path(source_name, match_id)
        if path.exists():
            return  # write-once: do not overwrite
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".tmp_{match_id}_{os.getpid()}.json")
        await asyncio.to_thread(self._atomic_write, tmp, path, data)

    @staticmethod
    def _atomic_write(tmp: Path, final: Path, data: str) -> None:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, data.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(final))

    async def find_any(self, match_id: str) -> tuple[str, dict] | None:
        """Check all source subdirectories for a cached blob.

        With per-blob files, this is an O(S) operation where S = number of
        source directories — one stat() call per source. No line-by-line
        scanning, no JSONL parsing. At S=2-5, this completes in microseconds.

        Returns (source_name, parsed_blob_dict) or None.
        """
        if not self._data_dir or not self._data_dir.exists():
            return None
        platform = match_id.split("_")[0]
        try:
            self._validate_platform(platform)
        except ValueError:
            return None
        for source_dir in self._data_dir.iterdir():
            if not source_dir.is_dir():
                continue
            blob_path = source_dir / platform / f"{match_id}.json"
            if blob_path.exists():
                data = await asyncio.to_thread(blob_path.read_bytes)
                return (source_dir.name, json.loads(data))
        return None
```

**Key genericity point:** `find_any()` discovers source directories by scanning the filesystem, not from a hardcoded list. When a new source writes its first blob, `find_any()` automatically includes it on subsequent lookups. The `source_name` parameter in `exists()`, `read()`, and `write()` always comes from `source.name` -- callers never construct source directory names manually.

**Performance benefit of per-blob files:** `find_any()` performs one `stat()` call per source directory -- O(S) where S = number of sources. With 2-5 sources, this completes in microseconds. No line-by-line scanning, no file opening, no decompression. `exists()` and `read()` are O(1) path lookups. This resolves the hot-path disk scan concern from the JSONL bundle design.

**Design decisions:**

- **Disk-only, no Redis caching.** BlobStore blobs are large (15-50 KB per response) and are read only on cache-hit during waterfall lookup. Putting them in Redis would duplicate the existing `RawStore` memory footprint for marginal latency benefit.
- **One-file-per-blob format.** Eliminates concurrent write corruption (no shared files to append to), enables O(1) lookups (no line scanning), and simplifies eviction (delete individual files or entire directories). Each file is self-contained.
- **Write-once semantics.** `write()` checks `path.exists()` before writing. If the file already exists, it returns without overwriting.
- **`read()` returns `dict`.** BlobStore owns the JSON deserialization. The coordinator never calls `json.loads` on BlobStore output -- it receives a ready-to-use dict.
- **No Redis TTL.** Disk blobs do not expire automatically. Eviction is a manual/cron operation (delete old source/platform directories). At 30 KB per blob and 10,000 matches/month, disk usage is ~300 MB/month -- manageable for a VPS.

### 3.8 Transformer (Source-specific to Canonical Shape)

The transformer is invoked by extractors when the source data shape differs from the canonical Riot shape. Each source package contains its own transformers. The coordinator and BlobStore are unaware of transformer internals.

**Pattern for any source:**
```
sources/{source_name}/
  transformers.py    # source-specific → canonical shape conversion
```

**Current gap found during research (op.gg-specific):**

The `_opgg_etl.normalize_game()` function (`_opgg_etl.py:105`) produces `gameCreation` but the parser's `_validate()` function (`_helpers.py:37`) requires `gameStartTimestamp`. This means op.gg-sourced data would fail parsing.

**Fix (inside sources/opgg/transformers.py):** The transformer must emit both `gameCreation` and `gameStartTimestamp` (set to the same value, since op.gg does not distinguish between creation time and start time):

```python
# In the op.gg transformer
"gameCreation": game_creation_ms,
"gameStartTimestamp": game_creation_ms,  # Required by parser._validate()
```

Additionally, the op.gg transformer must emit `gameVersion` (currently missing from op.gg ETL output). The parser uses `gameVersion` for patch normalization (`_normalize_patch`). If op.gg does not provide patch info, the transformer should emit `gameVersion: ""` and the patch will normalize to `""`.

**Other missing fields** that the parser reads from `info` (via `_PARTICIPANT_FIELD_MAP`) but the op.gg ETL does not produce:

| Riot field | Used by parser | Op.gg ETL emits | Impact |
|---|---|---|---|
| `gameStartTimestamp` | Required (KeyError if missing) | Missing | **Breaks parsing** |
| `gameVersion` | Optional (defaults to `""`) | Missing | Patch = `""`, no champion stats aggregation |
| `championName` | Optional (defaults to `""`) | Missing | `champion_name` field empty in Redis |
| `goldEarned` | Optional (defaults to `0`) | Missing | Zero in stats |
| `visionScore` | Optional (defaults to `0`) | Missing | Zero in stats |
| `perks` | Optional (defaults to empty) | Missing | No rune data |
| `wardsPlaced`, etc. | Optional (defaults to `0`) | Missing | Zero in stats |

These are acceptable degradations -- the parser handles defaults via `_PARTICIPANT_FIELD_MAP`. Only `gameStartTimestamp` is a hard blocker.

**Future sources:** Each source will have its own set of field mapping gaps. The transformer for that source is responsible for bridging them. The parser's `_PARTICIPANT_FIELD_MAP` defaults ensure graceful degradation for missing optional fields from any source.

### 3.9 Waterfall Coordinator

The central orchestration logic that tries sources in order. The coordinator is fully source-agnostic -- it contains NO source-specific conditionals (`if source == "opgg"`, etc.). All source-specific behavior is encapsulated in the `Source` and `Extractor` implementations.

**Location:** `lol-pipeline-common/src/lol_pipeline/sources/coordinator.py`

```python
class WaterfallCoordinator:
    def __init__(
        self,
        registry: SourceRegistry,
        blob_store: BlobStore | None,
        raw_store: RawStore,
        extractors: list[Extractor],
    ) -> None:
        self._registry = registry
        self._blob_store = blob_store
        self._raw_store = raw_store
        self._extractors = extractors
        # Index extractors by (source_name, data_type) for O(1) lookup
        self._extractor_index: dict[tuple[str, DataType], Extractor] = {
            (ext.source_name, dt): ext
            for ext in extractors
            for dt in ext.data_types
        }

    def _get_extractor(self, source_name: str, data_type: DataType) -> Extractor | None:
        return self._extractor_index.get((source_name, data_type))

    async def fetch_match(
        self,
        context: FetchContext,
        data_type: DataType = MATCH,
    ) -> WaterfallResult:
        """Try sources in priority order. Returns result with data or failure info.

        Algorithm:
        1. Check raw_store (existing RawStore) — if blob exists, skip fetch entirely
        2. Check blob_store across all sources — if cached, extract and return
        3. For each source in registry.sources_for(data_type):
           a. source.fetch(context, data_type)
           b. On SUCCESS:
              - Coordinator calls json.loads(raw_blob) once → blob_dict
              - Validate: call extractor.can_extract(blob_dict)
                - If False: set blob_validation_failed=True, do NOT persist,
                  continue to next source
              - Save raw_blob to blob_store (keyed by source.name)
              - Run extractor for (source.name, data_type) on blob_dict
              - Store extracted data to raw_store
              - Return success with data and available_data_types
           c. On THROTTLED or UNAVAILABLE or SERVER_ERROR:
              - Collect retry_after_ms hint if present
              - Log, continue to next source
           d. On NOT_FOUND:
              - If data_type in source_entry.primary_for: return not_found (terminal)
              - Else: treat as UNAVAILABLE, continue
           e. On AUTH_ERROR:
              - If data_type in source_entry.primary_for: return auth_error (triggers system:halted)
              - Else: treat as UNAVAILABLE, continue
        4. All sources exhausted → return all_exhausted with
           retry_after_ms = max(all collected hints)

        NO source-specific conditionals exist in this method.
        The primary_for set on SourceEntry controls NOT_FOUND/AUTH_ERROR semantics.
        """
```

**Deserialization responsibility:** The coordinator owns the single `json.loads(raw_blob)` conversion from bytes to dict. This happens once per fetch, and the resulting dict is passed to both `extractor.can_extract()` and `extractor.extract()`. The coordinator never calls `json.loads` twice on the same blob. `BlobStore.read()` also returns a parsed dict, so the cache-hit path similarly avoids double deserialization. This means JSON is the assumed wire format -- an acceptable constraint since all current and foreseeable sources return JSON responses.

**Pre-persist validation (prevents poisoned blob loop):** Before writing a blob to BlobStore, the coordinator calls `extractor.can_extract(blob_dict)`. If this returns False, the blob is NOT persisted. The coordinator sets `blob_validation_failed=True` on the result and continues to the next source. This prevents the scenario where an un-extractable blob is persisted to disk and then shadows all future fetch attempts via `find_any()`, creating an infinite retry loop.

**`retry_after_ms` propagation:** Each source that returns THROTTLED may include a `retry_after_ms` hint (e.g., from a `Retry-After` HTTP header). The coordinator collects all such hints during the waterfall iteration. When all sources are exhausted, `WaterfallResult.retry_after_ms` is set to `max(all_hints)` -- the longest backoff from any source. The Fetcher forwards this value to `nack_to_dlq` unchanged, preserving the precise retry timing that Recovery and the Delay Scheduler depend on.

**Result type:**
```python
@dataclass
class WaterfallResult:
    status: str  # "success", "not_found", "auth_error", "all_exhausted", "cached"
    data: dict | None = None
    source: str = ""                # which source provided the data (source.name)
    retry_after_ms: int | None = None  # max(all source THROTTLED hints) on all_exhausted
    available_data_types: frozenset[DataType] = field(default_factory=frozenset)
    blob_validation_failed: bool = False  # True if any source's blob failed can_extract()
```

**Step 1 (RawStore check)** preserves the existing idempotency behavior: if the Fetcher already processed this match_id, the blob is in `raw:match:{match_id}` and we skip everything.

**Step 2 (BlobStore check)** is new: before making any HTTP calls, check if any source has a cached blob on disk. With per-blob files, this is an O(S) stat-check operation (one `Path.exists()` per source directory) -- no line scanning, no file parsing. If a blob is found, `find_any()` returns the parsed dict directly. The coordinator finds the appropriate extractor via `_get_extractor(source_name, data_type)` and produces Riot-shaped data without any network call. The `source_name` comes from the BlobStore's `find_any()` return value, not from a hardcoded list.

**Step 3 (source waterfall)** tries each source returned by `registry.sources_for(data_type)`. The coordinator treats `THROTTLED`, `UNAVAILABLE`, and `SERVER_ERROR` identically: log and try the next source. It uses `data_type in source_entry.primary_for` (not `source.name == "riot"`) to determine whether `NOT_FOUND` and `AUTH_ERROR` are terminal.

**Genericity invariant:** The coordinator's source iteration loop is:
```python
retry_hints: list[int] = []

for entry in self._registry.sources_for(data_type):
    response = await entry.source.fetch(context, data_type)

    if response.result == FetchResult.SUCCESS:
        blob_dict = json.loads(response.raw_blob)
        extractor = self._get_extractor(entry.name, data_type)
        if extractor and not extractor.can_extract(blob_dict):
            # Bad blob — do NOT persist, try next source
            continue
        # Persist and extract...

    if response.retry_after_ms is not None:
        retry_hints.append(response.retry_after_ms)

    # ... handle NOT_FOUND/AUTH_ERROR based on entry.primary_for ...

# All exhausted
return WaterfallResult(
    status="all_exhausted",
    retry_after_ms=max(retry_hints) if retry_hints else None,
)
```

There are no `isinstance` checks, no source name comparisons, no source-specific branches. Adding a new source to the registry is sufficient -- the coordinator processes it automatically.

---

## 4. Stream Integration

### Where This Hooks In

The waterfall replaces the body of `_fetch_match()` in `lol-pipeline-fetcher/src/lol_fetcher/main.py`.

**Current flow** (main.py:168-246):
```
_fetch_match()
  ├─ is_system_halted? → skip
  ├─ raw_store.exists? → publish_and_ack (idempotent)
  ├─ _try_opgg() → if cached, store_and_publish
  ├─ wait_for_token() → riot.get_match()
  ├─ handle exceptions (429, 403, 404, 5xx)
  └─ _store_and_publish()
```

**Proposed flow:**
```
_fetch_match()
  ├─ is_system_halted? → skip
  ├─ Build FetchContext from envelope (match_id, puuid, region)
  ├─ coordinator.fetch_match(context, MATCH)
  │   ├─ success → _store_and_publish(data)
  │   ├─ cached  → _publish_and_ack() (already stored)
  │   ├─ not_found → set status, ack
  │   ├─ auth_error → set system:halted
  │   └─ all_exhausted → nack_to_dlq(retry_after_ms=result.retry_after_ms)
  └─ (timeline fetch unchanged — Riot API only, uses blocking wait_for_token())
```

### What Does NOT Change

- **stream:match_id payload schema:** No changes. The `source` field already exists but becomes vestigial -- the coordinator decides which source to use at runtime, not the message producer.
- **stream:parse payload:** No changes. `{match_id, region}` is unchanged.
- **RawStore key:** `raw:match:{match_id}` stores Riot-shaped data regardless of which source provided it. The parser does not know or care about the data source.
- **Parser, Analyzer, Recovery, Delay Scheduler:** No changes.
- **Crawler:** No changes. If the crawler uses a third-party source for match discovery (existing flow), those blobs can be saved to BlobStore for later reuse by the Fetcher.
- **Adding a new source requires zero changes** to stream schemas, Redis keys, the Fetcher's `_fetch_match()`, or the coordinator's algorithm.

### Consumer Group

No new consumer groups. The Fetcher continues to use the `fetchers` group on `stream:match_id`.

---

## 5. Proactive Emit Design

When a source returns a blob containing data types beyond what was requested (e.g., build data alongside match data), the system should be able to proactively signal that additional data is available, without waiting for an explicit request.

### Design: `stream:blob_available`

A new stream for "data available on disk" notifications.

**Payload:**
```json
{
  "match_id": "NA1_12345",
  "source": "<source.name>",
  "data_types": ["match", "build"],
  "region": "na1"
}
```

The `source` field is always `source.name` -- never a hardcoded value.

**Producer:** The WaterfallCoordinator, after a successful fetch, checks `available_data_types` on the `FetchResponse`. If the blob contains data types beyond what was requested, it publishes a notification to `stream:blob_available`. This logic is source-agnostic -- any source that populates `available_data_types` triggers the emit.

**Consumer:** A future service (e.g., "BuildProcessor") subscribes to `stream:blob_available`, reads the blob from BlobStore using the `source` field as the subdirectory key, runs the appropriate extractor, and writes the result.

### Why This Is a Natural Extension

The `FetchResponse.available_data_types` field is populated by every source after a successful fetch. Today, only `MATCH` is consumed. Tomorrow, when a BuildProcessor service exists:

1. The coordinator publishes to `stream:blob_available` (a one-line addition in the coordinator's success path).
2. The BuildProcessor consumes `stream:blob_available`, reads the blob from BlobStore, runs the matching `Extractor` (looked up by `source_name` + `data_type`), and writes to Redis.

No changes to existing services. No changes to the coordinator logic. The `available_data_types` field and the BlobStore already exist.

### Deferred Implementation

For the initial implementation:
- `FetchResponse.available_data_types` is populated but not acted upon.
- `stream:blob_available` is not created.
- The coordinator logs `available_data_types` at DEBUG level for future reference.

This keeps the initial scope tight while ensuring the proactive path is a natural one-line extension, not a retrofit.

---

## 6. Redis Key Changes

### New Keys

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `source:stats:{source_name}` | Hash | none | Fetch count, success count, throttle count per source. `source_name` comes from `source.name`. Written by WaterfallCoordinator. Automatically created for any new source. |

### Modified Keys

None. All existing keys are unchanged.

### Keys NOT Needed

- No Redis caching for BlobStore blobs (disk-only).
- No source-specific rate-limit keys beyond what each source manages internally.
- No new stream for the initial implementation (stream:blob_available is deferred).
- No key schema changes when adding a new source. The `source:stats:{source_name}` key is dynamically created using `source.name`.

---

## 7. Config Changes

### New Environment Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SOURCE_WATERFALL_ORDER` | str | `"riot"` | Comma-separated source priority. `"riot,opgg"` enables fallback. Accepts any registered source name (e.g., `"riot,opgg,ugg"`). Unknown names cause a startup validation error. |
| `BLOB_DATA_DIR` | str | `""` | Disk directory for raw source blobs. Empty = disabled. |
| `WATERFALL_LOG_LEVEL` | str | `"INFO"` | Log level for waterfall coordinator (useful for debugging source selection). |

### Modified Config Fields

Add to `Config` class in `lol-pipeline-common/src/lol_pipeline/config.py`:

```python
source_waterfall_order: str = "riot"  # comma-separated
blob_data_dir: str = ""
```

### Adding a New Source Requires Zero Config Schema Changes

Adding u.gg (or any future source) to the waterfall requires only:
1. Create `sources/ugg/` package (source, extractors, transformers).
2. Set `SOURCE_WATERFALL_ORDER=riot,opgg,ugg` in the environment.

No new env vars, no config class modifications, no schema changes. Source-specific configuration (e.g., API keys, rate limits) is read from existing env var patterns by the source's own `__init__`.

### No Changes Needed

- `opgg_enabled`, `opgg_rate_limit_per_second`, `opgg_rate_limit_long`, `opgg_match_data_dir`, `opgg_api_key` -- these already exist and continue to control op.gg client behavior. They are read by `OpggSource.__init__`, not by the coordinator.

---

## 8. Migration Path

### Phase 1: Source Abstraction Layer (generic foundation)

Create the `sources/` subpackage in lol-pipeline-common with all generic infrastructure:

```
lol-pipeline-common/src/lol_pipeline/sources/
  __init__.py
  base.py            # DataType (str alias), FetchContext, Source protocol, FetchResult, FetchResponse, Extractor protocol
  registry.py        # SourceRegistry, SourceEntry (with source.name validation)
  blob_store.py      # BlobStore (disk-only, per-blob files, atomic writes, path traversal prevention)
  coordinator.py     # WaterfallCoordinator (source-agnostic, pre-persist validation, retry_after_ms propagation)
```

**Validation gate:** The coordinator must pass all tests using mock sources (see Section 10) before any real source is integrated.

### Phase 2: Riot Source (first source implementation)

Create the Riot source package:

```
lol-pipeline-common/src/lol_pipeline/sources/riot/
  __init__.py
  source.py          # RiotSource (wraps existing RiotClient, uses try_token() non-blocking check)
```

The Riot source uses `primary_for=frozenset({MATCH})`. It wraps the existing `RiotClient` without modifying it.

**Rate limiter addition:** Implement `try_token()` as a non-blocking companion to `wait_for_token()` in the rate limiter module. Same Lua script, immediate return on denial.

### Phase 3: Op.gg Source (first third-party source)

Create the op.gg source package:

```
lol-pipeline-common/src/lol_pipeline/sources/opgg/
  __init__.py
  source.py          # OpggSource (wraps existing OpggClient)
  extractors.py      # OpggMatchExtractor, OpggBuildExtractor
  transformers.py    # Op.gg → Riot shape conversion (wraps/extends _opgg_etl.py)
```

Prerequisite: fix `_opgg_etl.normalize_game()` to emit `gameStartTimestamp` and `gameVersion` (see Section 3.8). This fix belongs in the op.gg package's transformer, not in shared code.

### Phase 4: Integrate into Fetcher

Replace the body of `_fetch_match()` with `WaterfallCoordinator.fetch_match()`. The old `_try_opgg()`, the per-source RawStore switching logic, and the inline Riot API call are all replaced by the coordinator. The Fetcher builds `FetchContext` from the stream envelope before calling the coordinator.

**Backward compatibility:** With `SOURCE_WATERFALL_ORDER=riot` (default), the coordinator behaves identically to the current code. Existing deployments are unaffected.

### Phase 5: Docker Compose Changes

Add `BLOB_DATA_DIR=/blob-data` to the Fetcher's environment. Add a volume mount:

```yaml
fetcher:
  volumes:
    - ./blob-data:/blob-data      # New: raw source blobs
```

The Parser does NOT need access to BlobStore -- it reads from RawStore (which contains Riot-shaped data regardless of source).

### What Gets Replaced

| Current code | Replaced by |
|---|---|
| `_fetch_match()` inline Riot API call (main.py:215-244) | `RiotSource.fetch()` via coordinator |
| `_try_opgg()` cache check (main.py:146-165) | `BlobStore.find_any()` in coordinator step 2 |
| Per-source RawStore switching (main.py:188-196) | Coordinator always writes to default RawStore |
| `opgg_raw_store` construction (main.py:191) | BlobStore for raw blobs; RawStore for normalized data |

### What Is Preserved

| Current code | Status |
|---|---|
| `RiotClient` (riot_api.py) | Unchanged; wrapped by RiotSource |
| `OpggClient` (opgg_client.py) | Unchanged; wrapped by OpggSource |
| `RawStore` (raw_store.py) | Unchanged; used by coordinator for normalized data |
| `_opgg_etl.normalize_game()` | Extended via op.gg transformer; used by OpggMatchExtractor |
| `wait_for_token()` / rate limiter | Unchanged; used by non-waterfall paths (timeline). `try_token()` added for waterfall. |
| `handle_riot_api_error()` (helpers.py) | Replaced by coordinator result handling in Fetcher |
| Match_id payload `source` field | Vestigial; coordinator decides source at runtime |

### Adding a Future Source (e.g., u.gg)

To add u.gg to the waterfall:

1. Create `lol-pipeline-common/src/lol_pipeline/sources/ugg/`:
   ```
   sources/ugg/
     __init__.py
     source.py          # UggSource (implements Source protocol)
     extractors.py      # UggMatchExtractor (implements Extractor protocol)
     transformers.py    # u.gg → Riot shape conversion
     constants.py       # UGG_RUNES: DataType = "ugg_runes" (source-specific data types, if any)
   ```
2. Register `UggSource` in the Fetcher's `main()` source construction.
3. Set `SOURCE_WATERFALL_ORDER=riot,opgg,ugg`.

**What does NOT change:**
- `coordinator.py` -- zero edits
- `registry.py` -- zero edits
- `blob_store.py` -- zero edits
- `base.py` -- zero edits (source-specific DataType constants live in the source's own package)
- Stream schemas -- zero edits
- Redis keys -- zero edits
- `_fetch_match()` -- zero edits
- Docker Compose -- zero edits (blob-data volume already mounted)
- Parser, Analyzer, Recovery, Delay Scheduler -- zero edits

---

## 9. File Layout Summary

```
lol-pipeline-common/src/lol_pipeline/
  sources/
    __init__.py
    base.py              # DataType (str alias), FetchContext, Source, FetchResult, FetchResponse, Extractor protocols
    registry.py          # SourceRegistry, SourceEntry (with name validation, primary_for)
    blob_store.py        # BlobStore (per-blob files, atomic writes, path traversal prevention)
    coordinator.py       # WaterfallCoordinator (source-agnostic, pre-persist validation, retry propagation)
    riot/
      __init__.py
      source.py          # RiotSource (uses try_token() non-blocking check)
    opgg/
      __init__.py
      source.py          # OpggSource
      extractors.py      # OpggMatchExtractor, OpggBuildExtractor
      transformers.py    # Op.gg → Riot shape (wraps _opgg_etl.py)
    # Future sources follow the same layout:
    # ugg/
    #   __init__.py
    #   source.py        # UggSource
    #   extractors.py    # UggMatchExtractor
    #   transformers.py  # u.gg → Riot shape
    #   constants.py     # Source-specific DataType constants (if any)
  _opgg_etl.py           # Existing; referenced by sources/opgg/transformers.py
  config.py              # Modified: add source_waterfall_order, blob_data_dir
  rate_limiter.py        # Modified: add try_token() non-blocking companion to wait_for_token()

lol-pipeline-fetcher/src/lol_fetcher/
  main.py                # Modified: _fetch_match() builds FetchContext, uses WaterfallCoordinator

docker-compose.yml       # Modified: add blob-data volume to fetcher

.env.example             # Modified: add SOURCE_WATERFALL_ORDER, BLOB_DATA_DIR
```

**Source isolation rule:** Each source package (`sources/{source_name}/`) is self-contained. It may import from `sources/base.py` (protocols) and from shared library code (`_opgg_etl.py`, `RiotClient`, etc.), but it must NEVER import from another source package. Cross-source imports would violate the genericity constraint.

---

## 10. Testing Strategy

### 10.1 Coordinator Tests (source-agnostic, mock-based)

The coordinator MUST be tested exclusively with mock sources -- not op.gg-specific stubs, not Riot-specific stubs. This proves the generic path works regardless of which sources are registered.

**Mock source factory:**
```python
def make_mock_source(
    name: str,
    data_types: frozenset[DataType],
    fetch_result: FetchResult = FetchResult.SUCCESS,
    raw_blob: bytes | None = b'{"mock": true}',
    data: dict | None = None,
    available_data_types: frozenset[DataType] | None = None,
    retry_after_ms: int | None = None,
) -> Source:
    """Create a mock source for coordinator tests.

    No reference to any real source implementation.
    """
    ...
```

**Test cases for the coordinator:**

| Test | Sources | Expected behavior |
|---|---|---|
| Single source, success | `[mock_a(SUCCESS)]` | Returns data from mock_a |
| First throttled, second succeeds | `[mock_a(THROTTLED), mock_b(SUCCESS)]` | Skips mock_a, returns mock_b |
| All throttled | `[mock_a(THROTTLED), mock_b(THROTTLED)]` | Returns all_exhausted |
| Primary NOT_FOUND | `[mock_a(NOT_FOUND, primary=True)]` | Terminal not_found, does NOT try mock_b |
| Non-primary NOT_FOUND | `[mock_a(NOT_FOUND, primary=False), mock_b(SUCCESS)]` | Treats as UNAVAILABLE, tries mock_b |
| Primary AUTH_ERROR | `[mock_a(AUTH_ERROR, primary=True)]` | Returns auth_error |
| Non-primary AUTH_ERROR | `[mock_a(AUTH_ERROR, primary=False), mock_b(SUCCESS)]` | Treats as UNAVAILABLE, tries mock_b |
| BlobStore cache hit | BlobStore has blob + matching extractor | Extracts without network call |
| RawStore idempotency | RawStore has match | Returns cached, skips everything |
| Source filters by data_type | `[mock_a(supports=MATCH), mock_b(supports=BUILD)]` | Only tries mock_a for MATCH |
| Three sources, middle succeeds | `[mock_a(THROTTLED), mock_b(SUCCESS), mock_c(unused)]` | mock_c.fetch never called |
| Zero sources for data_type | Registry has no sources supporting TIMELINE | Returns all_exhausted immediately |

**Key invariant:** No test references "riot", "opgg", or any real source name. All tests use synthetic names like "mock_a", "mock_b", "source_alpha", etc.

### 10.2 Source Implementation Tests (source-specific)

Each source package has its own tests that verify:
- Correct mapping of source-specific errors to `FetchResult` values.
- Correct population of `supported_data_types` and `available_data_types`.
- Correct blob format produced by `raw_blob`.

These tests live in the source's own test directory:
```
tests/unit/sources/riot/test_riot_source.py
tests/unit/sources/opgg/test_opgg_source.py
```

### 10.3 Extractor Tests (source-specific)

Each extractor is tested against sample blobs from its source:
- Given a real (or realistic fixture) blob from source X, does the extractor produce valid Riot-shaped output?
- Does `can_extract()` correctly detect extractable vs non-extractable blobs?
- Does `data_types` accurately declare what the extractor can produce?

```
tests/unit/sources/opgg/test_opgg_extractors.py
tests/unit/sources/opgg/fixtures/sample_match_blob.json
```

### 10.4 Integration Tests

Integration tests verify the full waterfall path end-to-end using testcontainers (Redis) and mock HTTP servers:
- Fetcher receives a message, coordinator tries sources in order, blob is stored, data flows to stream:parse.
- These tests use real Source implementations but mock HTTP endpoints.
- They prove that the Fetcher's `_fetch_match()` correctly delegates to the coordinator and handles all result statuses.

### 10.5 Adding Tests for a New Source

When adding a new source (e.g., u.gg):
1. Add `tests/unit/sources/ugg/test_ugg_source.py` -- tests the source implementation.
2. Add `tests/unit/sources/ugg/test_ugg_extractors.py` -- tests extractors against fixture blobs.
3. Coordinator tests require ZERO changes -- mock-based tests already prove the generic path.


### 10.6 Live Integration Tests

Live integration tests make real HTTP calls to op.gg and validate that the pipeline's extractor and transformer work against the actual API. They are gated behind the `OPGG_LIVE_TESTS=1` environment flag and do NOT run by default.

**What they validate:**

| Test | Assertion |
|---|---|
| Real API shape | The response from op.gg matches what the extractor expects. Catches undocumented API changes (field renames, removed fields, new envelope wrappers) that mocks cannot detect. |
| Real rate limit enforcement | Confirms the 1 req/s rate limiter is respected under actual network conditions. Asserts zero 429 responses during normal operation. |
| Real blob written to disk | Verifies that `BlobStore.write()` produces a file on disk, that the file contains valid JSON, and that `BlobStore.read()` can round-trip it back to the original data. |
| Real canonical output | Asserts that the transformer produces a dict that passes the match-v5 schema validator (`contracts/schemas/`). Proves the full op.gg blob -> canonical Riot shape pipeline end-to-end. |

**How to run:**

```bash
OPGG_LIVE_TESTS=1 pytest tests/integration/test_opgg_live.py -v
```

**Timeout override:** Live tests require network round-trips and rate limit waits. They use `@pytest.mark.timeout(60)` per test, overriding the project-wide 10s limit from `03-testing-standards.md`. This is acceptable because live tests are never part of the fast-feedback unit suite.

**CI policy:** Live tests do NOT run in CI by default. There is no real API key or external network access in CI. They run manually in two situations:
1. Before a release -- to confirm the extractor/transformer still works against the live API.
2. When the op.gg extractor or transformer code changes -- to catch regressions against the real API shape.

**Fixture hygiene:** All live tests use the `tmp_path` pytest fixture for the blob directory. This guarantees automatic cleanup after each test run. No disk blobs leak into the repo or persist between runs.

**Failure tolerance:** If op.gg returns a 429 during a live test, the test retries once after sleeping for the rate limit window (extracted from the `Retry-After` header, or 2 seconds as a default). Only if the retry also returns 429 does the test fail. This prevents flaky failures caused by transient rate limit hits at test boundaries.

---

## 11. Open Questions

### Q1: Op.gg match-by-ID endpoint

Does op.gg expose an endpoint to fetch a single match by Riot match_id (or by op.gg internal ID)? The current `OpggClient` only supports `get_match_history(summoner_id)`, which returns a page of matches. Without a match-by-ID endpoint, op.gg cannot serve as a direct fallback for the Fetcher -- it can only contribute via cached blobs from the crawler.

**Impact:** If no such endpoint exists, the op.gg source in the waterfall is effectively a no-op for direct fetches, and the primary value is the cache-aware path (Section 3.9, Step 2). The architecture still works -- it just means op.gg contributes only when the crawler has already seen the match.

**Decision needed:** Research op.gg API for a match-detail endpoint. If none exists, document this limitation and focus the implementation on the cache path.

### Q2: Match ID cross-referencing

The op.gg ETL produces match IDs in the format `OPGG_{platform}_{opgg_internal_id}` (e.g., `OPGG_NA1_abc123`). Riot match IDs are `{platform}_{riot_id}` (e.g., `NA1_12345`). These cannot be cross-referenced without a mapping table.

If the Fetcher receives `NA1_12345` from stream:match_id, it cannot look up the op.gg blob for that match unless there is a mapping from Riot match_id to op.gg internal_id.

**Options:**
1. **Ignore cross-referencing.** Op.gg blobs are only useful when the crawler produces OPGG-prefixed match_ids, and those flow through a separate pipeline path. Riot-prefixed match_ids always use the Riot API.
2. **Build a mapping table.** When the crawler discovers matches via op.gg, store a mapping `opgg_id_map:{riot_match_id} → opgg_internal_id` in Redis. The BlobStore lookup uses this mapping.
3. **Store op.gg blobs keyed by Riot match_id.** This requires the crawler to resolve the op.gg internal_id to a Riot match_id at crawl time, which may not be possible.

**Recommendation:** Option 1 for now. The waterfall's primary value for op.gg is the cache path for OPGG-prefixed match_ids. Cross-source ID mapping is a separate feature.

### Q3: Blob TTL and eviction

BlobStore blobs are disk-only with no automatic TTL. At 30 KB per blob and 10K matches/month, this is ~300 MB/month. Over a year, that is ~3.6 GB.

**Options:**
1. **No TTL.** Let blobs accumulate. Manual cleanup via cron or admin command.
2. **Monthly rotation.** Delete bundles older than N months via a cron job.
3. **Configurable TTL.** `BLOB_DATA_TTL_DAYS=90` env var; a background task or startup check deletes old bundles.

**Recommendation:** Option 2. Simple, predictable, no runtime overhead. Add an `admin blob-cleanup --older-than 90d` command.

### Q4: Proactive emit stream name

Should the proactive emit stream be `stream:blob_available` or something else? Consider:
- `stream:data_available` (more generic)
- `stream:blob_notify` (emphasizes notification, not data)

**Recommendation:** `stream:blob_available` -- explicit about what is available (a blob on disk) and what the consumer should do (read from BlobStore). Defer naming decision until implementation.

### Q5: Waterfall behavior when Riot returns 429 with Retry-After

Currently, a Riot 429 sends the message to DLQ with `retry_after_ms`. With the waterfall, should a Riot 429 trigger:
- (A) Try next source in the waterfall, or
- (B) Send to DLQ with retry_after_ms (current behavior)?

**Recommendation:** Option A (try next source first). If all sources are exhausted, then send to DLQ with the Riot retry_after_ms. The message should enter the DLQ only when no source can serve it. This maximizes throughput during Riot throttling.

### Q6: `match_id_payload.json` schema — `source` field

The match_id payload schema has `"source": {"enum": ["riot", "opgg"]}`. With the waterfall, the source is determined at fetch time, not at crawl time. Should the `source` field be:
- (A) Removed from the schema (coordinator decides),
- (B) Kept but ignored by the coordinator,
- (C) Used as a source hint (prefer this source first)?

**Recommendation:** Option B. Keep for backward compatibility and audit trail, but the coordinator determines the actual source. This is a no-op change: the coordinator's logic does not read `envelope.payload["source"]`. If future sources are added, the enum in the schema does NOT need to be updated -- it is vestigial.

---

## Revision Notes — Round 1 → Round 2

_Revised: 2026-03-25_

The following changes were applied in response to Round 1 review findings:

| Issue | Section(s) revised | Change summary |
|-------|-------------------|----------------|
| BLOCKER-1: ID namespace mismatch | 3.7 | BlobStore keys use match_id from stream envelope; op.gg cache hits only for op.gg-originated matches |
| BLOCKER-2: Poisoned blob loop | 3.9 | Pre-persist validation via can_extract(); failed blobs route to next source, not persisted |
| BLOCKER-3: retry_after_ms dropped | 3.9 | WaterfallResult.retry_after_ms = max(all source hints); Fetcher forwards to DLQ unchanged |
| BLOCKER-4: Torn JSONL writes | 3.7 | One-file-per-blob: {source}/{platform}/{match_id}.json, atomic via tmpfile→fsync→os.replace() |
| CRITICAL-5: Path traversal | 3.7 | source.name validated ^[a-z0-9_]+$; platform ^[A-Z0-9]+$; Path.resolve() boundary check |
| CRITICAL-6: DataType closed enum | 3.1 | DataType = str alias + module-level constants; open set |
| CRITICAL-7: wait_for_token blocks 60s | 3.5 | Add try_token() non-blocking check; RiotSource returns THROTTLED immediately if denied |
| MAJOR-8: is_primary per-source | 3.3 | primary_for: frozenset[DataType] replaces is_primary: bool |
| MAJOR-9: fetch() takes only match_id | 3.2, 3.5 | FetchContext dataclass: match_id, puuid, region, extra |
| MAJOR-10: TIMELINE contradiction | 3.1 | Remove TIMELINE from initial constants; add TODO for future phase |
| MAJOR-11: find_any() scan cost | 3.7 | Resolved by BLOCKER-4: per-blob file → O(1) Path.exists() |
| MAJOR-12: bytes/dict type gap | 3.9 | Coordinator calls json.loads once; BlobStore.read() → dict |

## Review Round 1 — Security

_Reviewer: security | Date: 2026-03-25_

**Q1. BlobStore path traversal via `source.name` and `match_id`.**
Section 3.7 (lines 351-384) states that BlobStore disk paths are constructed as `{BLOB_DATA_DIR}/{source.name}/{platform}/{YYYY-MM}.jsonl`, where `source.name` is a string property on an object implementing the `Source` protocol, and `platform` is derived from `match_id.split("_")[0]` (the existing `RawStore` pattern at `raw_store.py:56`). Neither value is validated against path traversal sequences. If a future `Source` implementation sets `name = "../../etc"`, or if a crafted `match_id` on `stream:match_id` contains `../` in its platform segment (e.g., `"../../tmp_12345"`), `BlobStore.set()` would write outside the intended directory. The existing `RawStore._platform_dir()` (line 56) has the same latent vulnerability but is harder to exploit because `match_id` comes from Riot API responses. With the waterfall, `match_id` values from third-party sources enter the same path — what sanitization does `BlobStore` apply to both `source_name` and the platform segment before constructing filesystem paths?

**Q2. Untrusted third-party blob content flowing through extractors without schema validation.**
Section 3.4 (lines 222-265) defines the `Extractor.extract()` method as returning a `dict` that the coordinator "passes through to RawStore unchanged" (line 263). Section 3.9 (lines 477-480) confirms the coordinator does not inspect the extracted dict. This means any data an op.gg (or future) source returns — after passing through only the source-specific transformer — is stored in `raw:match:{match_id}` and consumed by the parser as if it were a Riot API response. The existing `_opgg_etl.normalize_game()` (`_opgg_etl.py:69-117`) trusts all values from the op.gg HTTP response (e.g., `puuid`, `summoner_id`, `champion_id`, `stats`). A malicious or compromised third-party API response could inject arbitrary values into Redis hashes that the UI later renders. Since the coordinator explicitly avoids inspecting extractor output, where in the pipeline is the canonical schema enforced on data that originated from a non-Riot source, and what prevents a poisoned blob from propagating to the UI?

**Q3. `find_any()` iterates attacker-controlled directory names.**
Section 3.7 (line 375-381) specifies that `BlobStore.find_any()` "discovers source directories by scanning the filesystem, not from a hardcoded list" (line 384). If an attacker can write a directory under `BLOB_DATA_DIR` (e.g., via a symlink, a shared volume mount, or a container escape), `find_any()` would treat it as a legitimate source, read its contents, and pass the blob to whatever extractor matches. Given that the coordinator selects extractors by `source_name` (line 269) and `find_any()` returns the directory name as the `source_name`, a directory named after a registered source (e.g., `riot/`) with crafted JSONL content would bypass the intended fetch path entirely. What prevents a BlobStore cache poisoning attack where a pre-planted blob for a known `match_id` causes the coordinator to skip all network fetches and serve attacker-controlled data?

**Q4. Op.gg scraping disguised as a browser — response integrity and TLS posture.**
The existing `OpggClient` (`opgg_client.py:25-35`) sends a spoofed `User-Agent` header imitating Chrome 124 and sets `Origin`/`Referer` to `op.gg`. The design (Section 3.6, line 310-343) wraps this client without modification. The `httpx.AsyncClient` at line 75-79 is constructed with `follow_redirects=True` and no certificate pinning or TLS verification override. Since this client is impersonating a browser against an undocumented internal API (`lol-api-summoner.op.gg`), and the design elevates op.gg data to flow through the same pipeline as Riot-authenticated data: (a) what is the impact if op.gg detects the scraping and begins returning manipulated responses (e.g., incorrect stats, fake puuids) rather than blocking — would the pipeline silently ingest poisoned data? And (b) does the `httpx` client's default TLS configuration verify certificates for op.gg's internal API subdomain, or could a MITM on the network path inject responses that flow into the BlobStore and onward to the UI?

**Q5. Rate limit bypass through waterfall multiplication.**
Section 3.9 (lines 463-493) describes the coordinator trying each source sequentially when the previous one returns `THROTTLED`. The Riot source consumes a rate limit token via `wait_for_token()` before making the HTTP call (Section 3.5, line 288), but Section 3.5 line 296-297 shows that a `RateLimitError` returns `THROTTLED` — meaning the token was already consumed and the HTTP call was already made before the 429 was received. With N sources, a single `match_id` can trigger up to N HTTP calls (one per source) plus N rate-limit token acquisitions across independent rate-limit windows. If an attacker floods `stream:match_id` with messages (or replays DLQ entries), each message fans out to N sources before exhausting the waterfall. Since op.gg's rate limits are self-imposed (2 req/s per `opgg_client.py:72-73`) and not enforced server-side at that exact threshold, what prevents a burst of waterfall attempts from exceeding op.gg's actual tolerance and triggering an IP ban that permanently degrades the non-Riot source?

---

## Review Round 1 — Formal Verifier

_Reviewer: formal-verifier | Date: 2026-03-25_

### Q1: Crash between BlobStore disk write and RawStore SET NX — silent data divergence

**Reference:** Section 3.9 (coordinator algorithm, step 3b: "Save raw_blob to blob_store ... Store extracted data to raw_store"), and Section 3.7 (BlobStore write-once semantics).

The coordinator's success path performs three sequential non-atomic operations: (1) `blob_store.set(source.name, match_id, raw_blob)` writes the full source blob to disk, (2) the extractor produces Riot-shaped data, (3) `raw_store.set(match_id, extracted_data)` writes to Redis + disk. If the process crashes after step (1) but before step (3), the BlobStore contains the raw blob but RawStore does not contain the extracted data. On redelivery (via PEL/XAUTOCLAIM), the coordinator re-enters at step 2 (BlobStore cache hit via `find_any()`), finds the cached blob, runs the extractor, and writes to RawStore — so the message is not lost.

But consider the inverse failure: the extractor in step (2) throws `ExtractionError` on the blob that was just fetched and stored to disk. The coordinator treats this as a failure and moves to the next source, or eventually nack_to_dlq. Meanwhile, `blob_store` now contains a blob that is permanently un-extractable. On every future redelivery, `find_any()` will return this poisoned blob, the extractor will fail again, and the coordinator will never reach a working source via the network. **This is an infinite extraction-failure loop that blocks the message from ever being fetched from a different source.**

What mechanism prevents a poisoned blob in the BlobStore from permanently shadowing a working source? Does the coordinator skip blobs whose extraction fails in step 2, or does `find_any()` always return the first blob it finds regardless of extractability?

### Q2: Concurrent Fetcher workers and BlobStore JSONL append — torn writes

**Reference:** Section 3.7 (BlobStore disk layout, JSONL bundle format: `{match_id}\t{raw_json}\n`), and Section 8 Phase 4 (multiple Fetcher workers share the same consumer group on `stream:match_id`).

The existing `RawStore` coordinates concurrent writes via Redis SET NX — only the NX winner writes to disk (see `raw_store.py:160-168`). The BlobStore design says "Write-once semantics. Same as RawStore — no overwrite if the key already exists in the current bundle." But the design does not specify the coordination mechanism.

If two Fetcher workers (in the same consumer group) process different match_ids concurrently, both may call `blob_store.set()` for different matches targeting the same JSONL file (`{source}/{platform}/{YYYY-MM}.jsonl`). The underlying `_write_to_disk` uses `open("a")` (append mode). On POSIX systems, `O_APPEND` guarantees atomic append only if each write is smaller than `PIPE_BUF` (4096 bytes on Linux). Match blobs are 15-50 KB — well above this threshold. Two concurrent appends can interleave, producing a corrupted JSONL file where lines are spliced together.

The current `RawStore` avoids this because SET NX on the Redis key serializes disk writes — only one writer per match_id. But BlobStore has no Redis key gate described in the design. What serialization mechanism prevents torn JSONL appends when multiple Fetcher workers write to the same monthly bundle file concurrently?

### Q3: `all_exhausted` loses Riot `retry_after_ms` — DLQ entry has no delay hint

**Reference:** Section 3.9 (coordinator algorithm, step N+1: "All sources exhausted -> return all_exhausted"), Section 3.2 (`FetchResponse.retry_after_ms`), Open Question Q5 recommendation (Option A: try next source on 429, DLQ only when all exhausted).

Under the Q5 recommendation, when Riot returns 429 with `Retry-After: 5`, the coordinator stores `retry_after_ms=5000` in the `FetchResponse`, then proceeds to try Source[1]. If Source[1] also fails (THROTTLED/UNAVAILABLE), the coordinator returns `WaterfallResult(status="all_exhausted")`. The `WaterfallResult` dataclass has a `retry_after_ms` field, but the design does not specify which source's `retry_after_ms` populates it when multiple sources returned different THROTTLED responses with different delays.

In the current Fetcher (`main.py:234-244`), a Riot 429 calls `handle_riot_api_error` which passes `exc.retry_after_ms` directly to `nack_to_dlq`, which writes it into the DLQ envelope, which Recovery uses to compute the ZADD score for `delayed:messages`. If the coordinator discards the Riot `retry_after_ms` when falling through to Source[1], and Source[1] returns THROTTLED with no `retry_after_ms` (or a different value), and the coordinator's `all_exhausted` path does not propagate any `retry_after_ms` to the Fetcher's `nack_to_dlq` call, then the DLQ entry will have `retry_after_ms=null`. Recovery will then use the default exponential backoff instead of the Riot-specified delay, potentially retrying before the Riot rate limit window expires — causing another 429 and another DLQ cycle.

How does the coordinator propagate `retry_after_ms` from the primary source's THROTTLED response through the `all_exhausted` result to the Fetcher's `nack_to_dlq` call?

### Q4: BlobStore `find_any()` filesystem scan is not deterministic across source additions

**Reference:** Section 3.7 (`find_any()`: "Iterates over `{data_dir}/*/` subdirectories dynamically"), Section 3.9 (coordinator step 2: "Check blob_store across all sources — if cached, extract and return").

`find_any()` scans the filesystem to discover source subdirectories. The iteration order of `Path.iterdir()` (or `glob("*/")`) is filesystem-dependent and not guaranteed to be stable. If two sources (`riot/` and `opgg/`) both have a cached blob for the same match_id (possible if the Crawler stored an op.gg blob and a previous Fetcher run stored a Riot blob), `find_any()` could return either one depending on inode order, filesystem type, or OS. The extracted data shape depends on which source's blob is returned (Riot blobs have full fields; op.gg blobs have degraded fields per Section 3.8's field gap table).

This means the same `find_any()` call on the same data can produce different extraction results depending on which blob it encounters first, and the result may silently degrade data quality (op.gg blob found first means empty `championName`, zero `goldEarned`, etc.) even though a higher-fidelity Riot blob exists on disk.

Should `find_any()` respect the source priority order from the `SourceRegistry`, and should the coordinator prefer blobs from the primary source when multiple are available? If not, what prevents non-deterministic data quality degradation?

### Q5: RawStore idempotency check in coordinator step 1 has a TTL-induced gap

**Reference:** Section 3.9 (coordinator step 1: "Check raw_store — if blob exists, skip fetch entirely"), and `raw_store.py:160` (SET NX with `ex=_TTL_SECONDS`, default 24h).

The coordinator's first step checks `raw_store.exists(match_id)` as an idempotency gate. But `RawStore` keys have a 24-hour TTL (`_TTL_SECONDS`). If a match was fetched more than 24 hours ago, the Redis key has expired. The disk bundle still has the data, and `raw_store.exists()` falls through to a disk scan (`_exists_in_bundles`), so this case is handled. However, `_exists_in_bundles` scans all `.jsonl` and `.jsonl.zst` files in the platform directory. As the dataset grows (months of data, hundreds of thousands of matches), this linear scan becomes O(total matches on disk) per idempotency check.

With the waterfall introducing BlobStore as an additional disk store, and with multiple sources each producing their own JSONL files, the combined disk I/O for the idempotency check (RawStore scan + BlobStore `find_any()` scan across all source subdirectories) could become a latency bottleneck. The current system avoids this because `match:status:fetched` in Redis (set by `_write_seen_match` with its own TTL) serves as a fast-path deduplication check at the Crawler level, preventing most duplicate match_ids from reaching the Fetcher. But for messages that arrive via the DLQ retry path or XAUTOCLAIM redelivery (which bypass the Crawler), every redelivered message pays the full disk scan cost.

Has the design considered the cumulative disk I/O cost of the RawStore bundle scan plus BlobStore `find_any()` scan on the redelivery path, and is there a Redis-side fast-path that short-circuits the disk scan for redelivered messages (analogous to `match:status:fetched` but with a TTL longer than the DLQ retry window)?

---

## Review Round 1 — AI Specialist

_Reviewer: ai-specialist | Date: 2026-03-25_

**1. The `Source.fetch()` signature assumes every source can look up data by Riot `match_id` -- but the design's own op.gg section proves this assumption is false. How does the protocol survive a second source that also cannot query by Riot match_id?**

Section 3.6, lines 326-334: `OpggSource.fetch()` returns `UNAVAILABLE` unconditionally because op.gg cannot look up by Riot match_id. The design acknowledges this at line 337 ("no known op.gg endpoint that accepts a Riot match_id") but still defines the `Source` protocol with `match_id: str` as the sole lookup key (Section 3.2, line 158). If u.gg (or any scrape-based source) similarly indexes by its own internal ID or by summoner+timestamp rather than Riot match_id, then the protocol forces every non-Riot source into the same `UNAVAILABLE` stub pattern. At that point the waterfall degenerates: position 0 (Riot) either succeeds or fails, and positions 1..N are all no-ops except for the BlobStore cache path. Is the `match_id`-keyed protocol actually extensible, or does it silently encode a "Riot is the only real fetcher" assumption that makes the waterfall a cache-lookup wrapper with extra abstraction?

**2. The `BlobStore.find_any()` cross-source discovery relies on a filesystem scan, but blobs are keyed by `(source_name, match_id)` -- and Section 3.6 / Q2 (lines 912-921) acknowledge that op.gg match IDs are not Riot match IDs. How does `find_any()` locate a blob when the match_id namespaces diverge?**

Section 3.7, lines 375-381: `find_any(match_id)` iterates all `{data_dir}/*/` subdirectories and searches for `match_id`. But op.gg blobs are keyed as `OPGG_NA1_abc123` (Q2, line 912), while the incoming lookup key from `stream:match_id` is `NA1_12345`. Without a cross-reference mapping, `find_any("NA1_12345")` will never find an op.gg blob stored under `OPGG_NA1_abc123`. The design recommends Option 1 ("ignore cross-referencing") at line 921, which means the cache path -- the *only* path op.gg contributes value through -- is also broken for Riot-originated match_ids. When a second source introduces yet another ID namespace (e.g., u.gg's internal match IDs), the problem compounds. What is the concrete mechanism by which `find_any()` resolves an incoming Riot match_id to a blob stored under a different source's ID scheme, and does deferring this question make the entire op.gg integration effectively dead code?

**3. The `DataType` enum is described as a "closed set" (Section 3.1, line 94) that lives in shared `base.py`, but the design also claims adding a new source requires "zero edits" to `base.py` (Section 8, line 768). What happens when a new source provides a data type that does not exist in the enum?**

Section 3.1, lines 101-106: `DataType` contains `MATCH`, `TIMELINE`, `BUILD`. Section 8 (line 768) claims adding u.gg requires "zero edits" to `base.py`. But if u.gg provides a data type the pipeline has never seen (e.g., `LIVE_GAME`, `RUNES_PAGE`, `TIER_LIST`), the `DataType` enum must be extended in shared code -- violating the zero-edit promise. The enum is in `lol-pipeline-common`, which is a shared dependency. Any enum addition triggers a version bump, a reinstall across all services, and potentially new `Extractor` and consumer registrations. How does the design reconcile the "closed set" DataType enum with the "zero coordinator/base changes" extensibility claim, and at what number of sources does enum churn become the maintenance bottleneck the design was supposed to prevent?

**4. The proactive emit design (Section 5) assumes `available_data_types` on `FetchResponse` is a reliable signal, but this field is populated by the source *after* a fetch for a *specific* `data_type`. How does the coordinator know whether the "extra" data types in the blob are complete and parseable, vs. partial fragments?**

Section 5, lines 569-609: The design says the coordinator checks `available_data_types` after a successful fetch and (in the future) publishes to `stream:blob_available`. But `available_data_types` is a self-declaration by the source (Section 3.2, line 135) with no validation. Consider: u.gg's match endpoint returns match data plus a partial build stub (items but no runes). The u.gg source declares `available_data_types={MATCH, BUILD}`. The future BuildProcessor reads the blob, runs the extractor, and gets incomplete data. There is no contract or schema validation between what `available_data_types` *claims* and what the blob *actually contains* in extractable form. The `Extractor.can_extract()` method (Section 3.4, line 250) exists but is never called in the proactive emit path as described. When a second source with different completeness characteristics is added, does the proactive emit silently produce degraded data, and is there a planned validation gate between the `available_data_types` declaration and the actual extraction?

**5. The coordinator's `is_primary` flag (Section 3.3, line 191) governs terminal semantics for `NOT_FOUND` and `AUTH_ERROR`, but the design assumes exactly one primary source. What happens when the Riot API is decommissioned or a source's "primary" status needs to be context-dependent (e.g., primary for MATCH but not for BUILD)?**

Section 3.9, lines 482-488: `NOT_FOUND` from the primary source is terminal -- the coordinator stops and does not try other sources. `AUTH_ERROR` from the primary source triggers `system:halted`. But `is_primary` is a boolean on `SourceEntry` (line 191), not per-DataType. If Riot is primary but does not support `BUILD` (it does not -- Riot's match-v5 endpoint returns match data, not build recommendations), and op.gg or u.gg is the only source for `BUILD`, then no source is "primary" for that data type. A `NOT_FOUND` from op.gg for a BUILD request would be treated as `UNAVAILABLE` (non-primary), the coordinator would try all remaining sources, exhaust them, and send the message to DLQ -- even though the "not found" was authoritative. Conversely, if a future source becomes more authoritative than Riot for certain data types, the boolean `is_primary` cannot express "primary for BUILD but not for MATCH." Does the single-boolean `is_primary` model break down as soon as data types diverge across sources, and was this considered in the "zero coordinator changes" claim?

---

## Review Round 1 — Developer

_Reviewer: developer | Date: 2026-03-25_

**Q1. The only concrete non-Riot source returns `UNAVAILABLE` unconditionally and its cache path is broken by the match_id namespace mismatch -- what real-world scenario does the waterfall improve today?**

Section 3.6 (lines 326-334) documents that `OpggSource.fetch()` returns `UNAVAILABLE` for every call because op.gg has no match-by-ID endpoint. The design then positions the BlobStore `find_any()` cache hit (Section 3.9, Step 2, lines 472-478) as the primary value op.gg contributes. But Open Question Q2 (lines 910-921) acknowledges that op.gg match IDs use the format `OPGG_{platform}_{opgg_internal_id}` while Riot match IDs use `{platform}_{riot_id}`, and the recommended resolution is Option 1: "ignore cross-referencing." If `find_any("NA1_12345")` scans the `opgg/NA1/` directory for a line starting with `NA1_12345\t`, it will never match a line starting with `OPGG_NA1_abc123\t`. This means both the direct-fetch path AND the cache path are non-functional for op.gg when the incoming match_id is Riot-originated, which is the normal pipeline flow. The design introduces 7 new files (Section 9, lines 779-810), 4 protocol types, a new disk storage layer, and a coordinator -- replacing the existing 20-line `_try_opgg` + inline Riot call in `main.py:146-247`. If neither op.gg path actually works for the standard `stream:match_id` flow, what concrete production scenario improves on day one versus the status quo?

**Q2. The `all_exhausted` result drops the Riot `retry_after_ms` value that currently drives precise DLQ retry timing -- what is the regression in recovery latency?**

Currently (`main.py:234-244` plus `_helpers.py:157-172`), a Riot 429 calls `handle_riot_api_error`, which passes `exc.retry_after_ms` directly from the Riot response header to `nack_to_dlq`. The Recovery service uses this value to compute the exact ZADD score in `delayed:messages`, and the Delay Scheduler re-delivers the message at precisely the right moment. With the waterfall (Q5 recommendation, line 978: Option A), a Riot 429 returns `FetchResponse(result=THROTTLED, retry_after_ms=5000)` at Section 3.5 line 297, but the coordinator then proceeds to try Source[1]. If Source[1] returns `UNAVAILABLE` (the op.gg case, Section 3.6 line 334), the coordinator returns `WaterfallResult(status="all_exhausted")`. The `WaterfallResult` dataclass (lines 498-504) has a `retry_after_ms` field, but the design never specifies which source's value populates it when multiple sources returned different results. If it is `None` because the last-tried source had no retry hint, `nack_to_dlq` receives `retry_after_ms=None` and Recovery falls back to exponential backoff. This is strictly worse than the current behavior where Riot's precise `Retry-After: 5` header drives a 5-second retry. Has the coordinator been designed to capture and propagate the *primary source's* `retry_after_ms` through the exhaustion path, or is this an unintentional regression?

**Q3. `BlobStore.find_any()` performs an unbounded filesystem scan on every new match -- has the I/O cost been evaluated against the existing hot path?**

Section 3.7 (lines 375-384) specifies `find_any()` as iterating all `{data_dir}/*/` subdirectories and scanning JSONL bundles line-by-line. Section 3.9 Step 2 (lines 472-478) runs this on every message where `raw_store.exists()` returns False, which during normal operation is every new match. With N sources, M months of retained data, and ~10K matches per source per month (line 391), `find_any()` opens up to `N * M` files and performs up to `N * M * 10K` string comparisons. The existing `RawStore.exists()` already falls through to `_exists_in_bundles` (raw_store.py:140-144) via `asyncio.to_thread`, meaning the hot path now has TWO sequential disk-scan stages: RawStore bundles then BlobStore `find_any()`. The design says "disk-only, no Redis caching" (line 388), so there is no fast negative-cache. For a Fetcher processing 500 new matches/day across 3 sources and 6 months of data, that is potentially 500 * (18 RawStore scans + 18 BlobStore scans) per day. Since `find_any()` will *almost always miss* for new matches (the blobs do not exist yet), every call pays the maximum scan cost. Was this evaluated against the current Fetcher's latency per message, and is a Redis SET or Bloom filter for known blob match_ids needed to short-circuit the scan?

**Q4. The Extractor protocol takes `blob: dict` but `FetchResponse.raw_blob` is `bytes | None` -- where does deserialization happen, and does it violate source-agnosticism?**

Section 3.4 (lines 250-264) defines `Extractor.can_extract(self, blob: dict)` and `Extractor.extract(self, blob: dict, ...)` accepting `dict`. Section 3.2 (line 132) defines `FetchResponse.raw_blob: bytes | None`. The coordinator (Section 3.9, lines 477-478) needs to bridge this gap: it must turn `bytes` into `dict` before calling the extractor. If the coordinator does `json.loads(raw_blob)`, it embeds a JSON-format assumption in supposedly source-agnostic code, violating the genericity constraint (Section 1, line 18). If each source is expected to return already-deserialized data via `FetchResponse.data`, then `raw_blob` is redundant for the extraction path and exists only for BlobStore persistence -- but then what format does BlobStore store and return, and who re-deserializes on cache hit? The type boundary between `bytes` (wire format) and `dict` (extracted format) is undefined, and every layer that touches it will make an implicit serialization assumption. Which component owns the `bytes -> dict` conversion, and does that component know the source's wire format?

**Q5. The existing `_fetch_timeline_if_needed` is explicitly kept as "Riot API only" (Section 4, line 551), but `RiotSource.fetch()` for `DataType.TIMELINE` is listed as a supported data type (Section 3.5, line 279). If timeline fetching stays outside the waterfall, what is the purpose of the TIMELINE data type in the Source protocol, and does this create a parallel code path that the waterfall was supposed to eliminate?**

Section 3.5 (line 279) declares `RiotSource.supported_data_types = frozenset({DataType.MATCH, DataType.TIMELINE})`. Section 4 (line 551) says "timeline fetch unchanged -- Riot API only." Section 3.1 (lines 101-106) defines `DataType.TIMELINE` as a first-class data type. But the Fetcher's proposed flow (lines 543-551) does not route timeline fetches through the coordinator -- it keeps the existing `_fetch_timeline_if_needed` function that calls `riot.get_match_timeline()` directly, bypassing the waterfall entirely. This means TIMELINE has a Source protocol declaration that is never exercised, a `DataType` enum entry that the coordinator never sees on the fetch path, and a dedicated non-waterfall code path that duplicates the rate-limiting and error-handling logic the coordinator was designed to centralize. If a future source provides timelines (e.g., u.gg timeline data), would it plug into the waterfall or into the parallel `_fetch_timeline_if_needed` path? Does the TIMELINE data type exist to support a future migration of timeline fetching into the waterfall, and if so, should the design document that migration rather than leaving two coexisting fetch paths?

---

## Review Round 1 — Optimizer

_Reviewer: optimizer | Date: 2026-03-25_

**Q1: BlobStore.find_any() inserts an O(S * B * L) linear disk scan into the Fetcher's hot path on every new match -- what is the expected p99 latency, and does the design account for the fact that new matches (the dominant case) will always pay maximum scan cost?**

Section 3.7 (lines 375-384) specifies `find_any()` as iterating all `{data_dir}/*/` subdirectories dynamically and scanning JSONL bundles line by line. The coordinator runs this in Step 2 (Section 3.9, line 472) before trying any HTTP source. The existing `RawStore._search_bundles` (`/Users/abhiregmi/projects/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/raw_store.py:74-97`) demonstrates the inherited cost: it globs all `.jsonl` and `.jsonl.zst` files in the platform directory, then streams each file line by line looking for a `match_id\t` prefix. BlobStore replicates this across S source subdirectories. At scale: S=3 sources, B=6 monthly bundles per source, L=10K lines per bundle yields up to 180K line comparisons per miss. With compressed `.jsonl.zst` bundles (raw_store.py:116-121), each miss also triggers zstd decompression context setup. This runs on every new match because Step 1 (RawStore check) passes (new match is not in RawStore) and Step 2 must exhaustively prove no blob exists before Step 3 tries a network fetch. At the Fetcher's message rate of ~20/s, that is 20 full disk scans per second, all dispatched via `asyncio.to_thread` to the default thread pool. The design explicitly rejects Redis caching for BlobStore (line 388: "disk-only, no Redis caching"). Without a negative-cache mechanism (Redis SET, Bloom filter, or in-memory LRU of recently-missed match_ids), how does the design prevent `find_any()` from becoming the latency bottleneck that dominates every fetch, and has the p99 latency been estimated at 6-12 months of accumulated data?

**Q2: RiotSource.fetch() delegates to wait_for_token() which blocks for up to 60 seconds before the coordinator sees any signal -- under sustained Riot throttling, does the waterfall provide fallback or just delayed failure?**

Section 3.5 (lines 286-306) shows `RiotSource.fetch()` calling `wait_for_token(self._r, limit_per_second=..., region=region)` as its first operation. The existing `wait_for_token()` (`/Users/abhiregmi/projects/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/rate_limiter.py:61-106`) enters a polling loop: it calls `acquire_token()` via Lua EVAL, and if denied, sleeps for the hint duration plus jitter, repeating until granted or until `max_wait_s` (default 60s) expires. Only after a token is granted does the HTTP call happen. The waterfall's value proposition (Section 2, lines 67-86) is that a Riot 429 triggers fallback to the next source. But there are two distinct throttling scenarios: (a) Riot returns HTTP 429 *after* a successful token acquisition -- this produces `RateLimitError` and maps to `FetchResult.THROTTLED` (line 296-297); (b) the local rate limiter denies tokens because 20 req/s or 100 req/2min is saturated -- `wait_for_token()` blocks internally for up to 60s, never makes an HTTP call, and either eventually succeeds (no THROTTLED signal at all) or raises `TimeoutError` after 60s (line 305 maps to THROTTLED). Scenario (b) is the dominant throttling mode during sustained load. In that scenario, the Fetcher blocks for 60 seconds per message inside RiotSource before the coordinator falls through to Source[1]. With 20 in-flight messages, that is 20 coroutines each blocked for up to 60s, burning event loop slots and producing no throughput. How does the design ensure that waterfall fallback triggers within a reasonable latency bound (e.g., 1-2 seconds) when Riot's local rate limiter is saturated, rather than after the full 60-second timeout?

**Q3: The success path performs two sequential disk-write-with-dedup-scan operations per fetch via asyncio.to_thread -- what is the thread pool contention under burst and could it stall the event loop?**

On a successful source fetch, the coordinator performs: (1) `blob_store.set(source.name, match_id, raw_blob)` -- disk write, and (2) `raw_store.set(match_id, extracted_data)` -- Redis SET NX + conditional disk write (`/Users/abhiregmi/projects/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/raw_store.py:158-178`). `RawStore.set()` calls `_exists_in_current_bundle(match_id)` (raw_store.py:123-134) before writing -- a synchronous line-by-line scan of the current month's JSONL file. BlobStore presumably follows the same write-once pattern. Each disk operation is dispatched via `asyncio.to_thread`, which uses the default `ThreadPoolExecutor` (CPython default: `min(32, os.cpu_count() + 4)` workers). A single fetch's write path therefore consumes: thread slot 1 for BlobStore dedup scan + append, then thread slot 2 for RawStore dedup scan + append. Under burst conditions -- 20 rate-limit tokens granted simultaneously after a 1-second window opens -- 20 fetches complete near-simultaneously, each requesting 2 thread-pool dispatches. That is 40 thread tasks competing for ~8-36 pool workers, with each task doing synchronous file I/O on potentially the same JSONL file (concurrent appends from different match_ids to the same `{YYYY-MM}.jsonl`). POSIX `O_APPEND` is only atomic below `PIPE_BUF` (4096 bytes); match blobs are 15-50 KB. What prevents torn writes when two threads append to the same bundle concurrently, and what is the expected queuing delay when the thread pool is saturated?

**Q4: SourceRegistry.sources_for() performs a fresh O(S) list comprehension and allocation on every fetch_match() call for an immutable result -- should this be pre-computed?**

`SourceRegistry.sources_for(data_type)` (Section 3.3, lines 199-208) iterates all entries and filters by `data_type in e.source.supported_data_types` on every invocation. The coordinator calls this at the top of `fetch_match()` (Section 3.9, line 515), which runs at ~20/s. Both `SourceEntry` (frozen dataclass, line 186) and `supported_data_types` (frozenset, line 150) are immutable after construction. The result for any given `DataType` is deterministic and never changes. At S=2-5 sources this is not a performance concern per se, but the pattern allocates a new `list` object per call (20 allocations/s) for a value that could be computed once. Given that the `_extractor_index` dict (line 453-458) already demonstrates the design's awareness of pre-computation for hot-path lookups, why is `sources_for()` not similarly pre-computed into a `dict[DataType, list[SourceEntry]]` at `SourceRegistry.__init__` time?

**Q5: The Extractor protocol mandates blob: dict, forcing the coordinator to fully deserialize every blob before extraction -- does this double transient memory for a component that may only need a few fields?**

The `Extractor.extract()` signature (Section 3.4, line 258) requires `blob: dict`. On the BlobStore cache-hit path (Section 3.9, Step 2), `find_any()` returns the raw JSON as a string. The coordinator must `json.loads()` this into a dict before calling `extract()`, which returns a second dict. Both dicts coexist in memory until the source dict leaves scope. A Riot match-v5 JSON blob is 50-80 KB; as a CPython dict with per-object overhead per key-value pair (each int/str/list/dict node is a separate heap object), this expands to roughly 150-250 KB. Two dicts simultaneously: 300-500 KB per in-flight fetch. At 20 concurrent fetches: 6-10 MB of transient heap. More importantly, the mandatory full deserialization means the coordinator -- which the design describes as "source-agnostic" and which "does not inspect the dict" (line 262) -- is the component paying the deserialization cost for data it never reads. If the extractor accepted `blob: str | bytes`, it could deserialize only what it needs (e.g., `orjson.loads` for speed, or partial parsing for extractors that only read a few top-level fields). Has the tradeoff between a clean `dict` interface and the unnecessary full-deserialization cost in the coordinator been evaluated?
