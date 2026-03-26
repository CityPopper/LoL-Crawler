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

    @property
    def required_context_keys(self) -> frozenset[str]:
        """Keys that must be present in FetchContext.extra for this source to operate.

        Default: frozenset() (no extra keys required — the core fields
        match_id, puuid, region are always available).  The coordinator
        checks this before calling fetch(): if any required key is missing
        from context.extra, the source is skipped with a warning rather
        than called and left to fail silently.
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

- `FetchContext` replaces the bare `match_id: str` parameter. Each source extracts the fields it needs from the context. Riot uses `context.match_id` and `context.region`. A source that indexes by summoner/puuid uses `context.puuid`. A source that needs additional info uses `context.extra`. The coordinator builds `FetchContext` from the stream envelope's payload fields. The `extra` dict is populated by dumping all non-standard envelope payload fields as-is. Sources must tolerate missing keys gracefully (return UNAVAILABLE when a required key is absent). The `required_context_keys` property on the Source protocol enables startup validation: the coordinator logs a warning if a source declares required keys that are not available in the envelope schema, preventing silent configuration errors.
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
    def __init__(self, entries: list[SourceEntry], extractor_index: dict[tuple[str, DataType], "Extractor"] | None = None) -> None:
        self._entries = sorted(entries, key=lambda e: e.priority)
        self._by_name: dict[str, SourceEntry] = {e.name: e for e in self._entries}
        # Startup cross-check: every source's supported_data_types must
        # appear as a value in the registered extractor index.  Catches
        # typos like frozenset({"mtach"}) at startup instead of silently
        # dropping traffic at runtime.
        if extractor_index is not None:
            # Validate that each (source_name, data_type) pair has a registered extractor.
            for entry in self._entries:
                for dt in entry.source.supported_data_types:
                    if (entry.name, dt) not in extractor_index:
                        raise ValueError(
                            f"Source {entry.name!r} declares support for data type "
                            f"{dt!r}, but no extractor is registered for "
                            f"({entry.name!r}, {dt!r}). Check for typos."
                        )

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
                key_prefix=f"ratelimit:{context.region}",
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

**`try_token()` specification:**

```python
async def try_token(
    r: aioredis.Redis,
    *,
    key_prefix: str = "ratelimit",
    limit_per_second: int = 20,
    limit_long: int = 100,
) -> bool:
    """Non-blocking rate limit check. One Redis round-trip max.

    Calls the same dual-window Lua script as acquire_token().
    Returns True if a token was acquired, False if the rate limit
    is saturated.  Never sleeps, never retries.

    Parameters mirror acquire_token() -- not wait_for_token().
    The 'region' parameter on wait_for_token() is a legacy compat
    shim and is NOT replicated here.
    """
    result = await acquire_token(r, key_prefix=key_prefix,
                                 limit_per_second=limit_per_second,
                                 limit_long=limit_long)
    return result == 1  # 1 = granted, negative = wait hint (denied)
```

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
        if not path.is_relative_to(self._data_dir):
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

    async def write(self, source_name: str, match_id: str, data: bytes | str) -> None:
        """Atomic write: tmpfile -> fsync -> os.replace(). Write-once semantics.

        Accepts both bytes (from FetchResponse.raw_blob) and str.
        If bytes, decodes as UTF-8 before writing.
        """
        path = self._blob_path(source_name, match_id)
        if path.exists():
            return  # write-once: do not overwrite
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".tmp_{match_id}_{os.getpid()}_{uuid4().hex}.json")
        await asyncio.to_thread(self._atomic_write, tmp, path, data)

    @staticmethod
    def _atomic_write(tmp: Path, final: Path, data: bytes | str) -> None:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            raw = data.encode("utf-8") if isinstance(data, str) else data
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(final))

    async def find_any(self, match_id: str, source_names: list[str]) -> tuple[str, dict] | None:
        """Check source subdirectories for a cached blob, in priority order.

        source_names is the registry priority order (highest-priority first).
        Iterates in that order instead of filesystem iterdir() order,
        ensuring the highest-fidelity blob is always preferred when
        multiple sources have cached data for the same match_id.

        With per-blob files, this is an O(S) operation where S = number of
        source names — one stat() call per source. No line-by-line
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
        for name in source_names:
            # name comes from the trusted source_names list (validated at
            # SourceEntry construction via _SOURCE_NAME_RE); no re-validation needed.
            blob_path = self._data_dir / name / platform / f"{match_id}.json"
            if blob_path.exists():
                data = await asyncio.to_thread(blob_path.read_bytes)
                try:
                    return (name, json.loads(data))
                except json.JSONDecodeError:
                    # Corrupt blob (truncated write, disk error) — treat as
                    # cache miss and fall through to the next source / network fetch.
                    continue
        return None
```

**Key genericity point:** `find_any()` iterates source directories in the registry priority order passed via `source_names`, ensuring the highest-priority (most complete) blob is always preferred when multiple sources have cached data for the same match_id. The `source_name` parameter in `exists()`, `read()`, and `write()` always comes from `source.name` -- callers never construct source directory names manually. When a new source is added to the registry, it is automatically included in `find_any()` lookups.

**Performance benefit of per-blob files:** `find_any()` performs one `stat()` call per source name in the priority list -- O(S) where S = number of sources. With 2-5 sources, this completes in microseconds. No line-by-line scanning, no file opening, no decompression. `exists()` and `read()` are O(1) path lookups. This resolves the hot-path disk scan concern from the JSONL bundle design.

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

**Blob size limit:** A `MAX_BLOB_SIZE_BYTES: int = 2 * 1024 * 1024` (2 MB) constant is checked in the coordinator immediately after receiving `response.raw_blob`. If `len(response.raw_blob) > MAX_BLOB_SIZE_BYTES`, the response is treated as a fetch failure (logged as a warning, counted as `UNAVAILABLE`) and the coordinator continues to the next source. This is NOT treated as `blob_validation_failed` because the blob was never parsed or validated -- it is an oversized response from a misbehaving or compromised source.

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

**Step 2 (BlobStore check)** is new: before making any HTTP calls, check if any source has a cached blob on disk. The coordinator passes the registry's priority-ordered source name list to `find_any(match_id, source_names)`, ensuring the highest-priority blob is always preferred. With per-blob files, this is an O(S) stat-check operation (one `Path.exists()` per source) -- no line scanning, no file parsing.

If a blob is found, `find_any()` returns `(source_name, blob_dict)`. The coordinator then:
1. Calls `_get_extractor(source_name, data_type)`. If this returns `None` (unregistered source_name found on disk, or source package removed between deployments), log a warning and skip this cache hit -- fall through to Step 3.
2. Calls `extractor.can_extract(blob_dict)`. If this returns `False` (blob passed validation at write time but the extractor's criteria have since tightened), log a warning, skip this cache hit, and fall through to Step 3. This prevents the poisoned-blob-via-cache-hit loop.
3. Only if both checks pass, calls `extractor.extract(blob_dict, ...)` and returns the result.

This makes the cache-hit path consistent with the fresh-fetch path: no blob is used without passing both `_get_extractor() is not None` and `can_extract()`.

**Step 3 (source waterfall)** tries each source returned by `registry.sources_for(data_type)`. The coordinator treats `THROTTLED`, `UNAVAILABLE`, and `SERVER_ERROR` identically: log and try the next source. It uses `data_type in source_entry.primary_for` (not `source.name == "riot"`) to determine whether `NOT_FOUND` and `AUTH_ERROR` are terminal.

**Genericity invariant:** The coordinator's source iteration loop is:
```python
retry_hints: list[int] = []
any_blob_validation_failed = False

for entry in self._registry.sources_for(data_type):
    # Check required_context_keys before calling fetch()
    missing_keys = entry.source.required_context_keys - set(context.extra.keys())
    if missing_keys:
        log.warning("source %s requires context keys %s, skipping", entry.name, missing_keys)
        continue

    response = await entry.source.fetch(context, data_type)

    if response.result == FetchResult.SUCCESS:
        # Blob size guard — reject oversized responses before parsing
        if response.raw_blob and len(response.raw_blob) > MAX_BLOB_SIZE_BYTES:
            log.warning("source %s returned oversized blob (%d bytes), skipping",
                        entry.name, len(response.raw_blob))
            continue  # treated as UNAVAILABLE, not blob_validation_failed

        blob_dict = json.loads(response.raw_blob)
        extractor = self._get_extractor(entry.name, data_type)
        if extractor is None:
            # No extractor registered for this (source, data_type) pair
            log.warning("no extractor for (%s, %s), skipping", entry.name, data_type)
            continue
        if not extractor.can_extract(blob_dict):
            # Bad blob — do NOT persist, try next source
            any_blob_validation_failed = True
            continue
        # Persist and extract...

    if response.retry_after_ms is not None:
        retry_hints.append(response.retry_after_ms)

    # ... handle NOT_FOUND/AUTH_ERROR based on entry.primary_for ...

# All exhausted
return WaterfallResult(
    status="all_exhausted",
    retry_after_ms=max(retry_hints) if retry_hints else None,
    blob_validation_failed=any_blob_validation_failed,
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
  │   ├─ all_exhausted + blob_validation_failed → nack_to_dlq immediately
  │   │     (skip retry loop — the blob is structurally bad, retrying
  │   │      will hit the same un-extractable data from the same source)
  │   └─ all_exhausted (no blob_validation) → nack_to_dlq(retry_after_ms=result.retry_after_ms)
  └─ (timeline fetch unchanged — Riot API only, uses blocking wait_for_token())
```

**`blob_validation_failed` immediate DLQ routing:** When `WaterfallResult.blob_validation_failed` is True and `status` is `all_exhausted`, the Fetcher routes the message directly to DLQ with `max_attempts=1` (or equivalently, sets `attempts=max_attempts` on the envelope). This prevents the message from burning all `max_attempts` retry cycles against the same structurally bad blob. The DLQ archive entry preserves the `blob_validation_failed` flag for operational debugging.

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

## Revision Notes — Round 3 → Final

_Revised: 2026-03-25_

All five reviewers returned APPROVED or APPROVED WITH MINORS in Round 3. The following targeted fixes address the remaining issues before implementation begins.

| Issue ID | Section(s) changed | Change summary |
|----------|-------------------|----------------|
| MAJOR (developer + ai-specialist) — try_token() parameter mismatch | 3.5 | Fixed `RiotSource.fetch()` call site: changed `region=context.region` to `key_prefix=f"ratelimit:{context.region}"` to match the `try_token()` function signature. Per-region rate limiting preserved via key prefix. |
| MINOR (optimizer NIT-1 + ai-specialist MINOR-1) — Registry cross-check scope too broad | 3.3 | Changed validation from checking `dt not in known_data_types` (any source has extractor for dt) to `(entry.name, dt) not in extractor_index` (this specific source has extractor for dt). Prevents Source A passing validation because Source B has the extractor. |
| MINOR (formal-verifier FV-R3-2) — JSONDecodeError in find_any() | 3.7 | Added `try/except json.JSONDecodeError` around `json.loads(data)` in `find_any()`. Corrupt blobs (truncated write, disk error) are treated as cache misses — fall through to next source or network fetch. |
| MINOR (security MINOR-1) — find_any() path bypasses _blob_path() | 3.7 | Added inline comment noting that `name` in `find_any()` comes from the trusted `source_names` list (validated at `SourceEntry` construction via `_SOURCE_NAME_RE`); no re-validation needed. |
| Deferred to implementation | — | Security MINOR-2 (blob size check in find_any), Developer NEW-2 (Source example required_context_keys), Developer NEW-3 (docstring inversion), FV-R3-1 (stale blob cleanup), FV-R3-3, FV-R3-4 (documented tradeoffs). |

---

## Revision Notes — Round 2 → Round 3

_Revised: 2026-03-25_

The following changes were applied in response to Round 2 review findings across all five reviewers (developer, ai-specialist, optimizer, security, formal-verifier):

| Issue ID | Section(s) changed | Change summary |
|----------|-------------------|----------------|
| CRITICAL (developer ISSUE-1) — cache-hit bypasses can_extract() | 3.9 (Step 2) | After `find_any()` returns a hit, coordinator now calls `extractor.can_extract(blob_dict)` before using it. If False, skips cache hit and falls through to network fetch path. |
| MAJOR (5 reviewers) — find_any() non-deterministic order | 3.7 | Changed `find_any(match_id)` to `find_any(match_id, source_names: list[str])`. Iterates in registry priority order instead of `iterdir()`. Updated genericity and performance doc paragraphs. |
| MAJOR (developer ISSUE-2) — _get_extractor() returning None unhandled | 3.9 (Step 2, pseudocode) | Added explicit guard: if `_get_extractor()` returns None, log warning and skip. Applied to both cache-hit path (Step 2) and fresh-fetch path (pseudocode). |
| MAJOR (developer ISSUE-3) — BlobStore.write(data: str) vs raw_blob: bytes | 3.7 | Changed `BlobStore.write(data: str)` to `write(data: bytes | str)`. Implementation calls `data.encode("utf-8")` if str, passes bytes directly. |
| MAJOR (developer ISSUE-5) — try_token() never specced | 3.5 | Added full `try_token()` function signature, docstring, and implementation. Parameters mirror `acquire_token()`, not `wait_for_token()`. Returns `bool`, one Redis round-trip. |
| MAJOR (formal-verifier FV-R2-1) — BlobStore tmp path TOCTOU race | 3.7 | Changed tmp file naming to `{match_id}.{os.getpid()}.{uuid4().hex}.tmp` to prevent same-PID coroutine collisions via XAUTOCLAIM. |
| MAJOR (formal-verifier FV-R2-8) — blob_validation_failed burns max_attempts | 3.9, 4 | When `blob_validation_failed=True` in WaterfallResult, Fetcher routes immediately to DLQ (skips retry loop). Documented in Section 4 Fetcher flow. |
| MAJOR (ai-specialist MAJOR-1) — FetchContext.extra untyped footgun | 3.2 | Added `required_context_keys: frozenset[str]` to Source protocol. Coordinator validates before calling `fetch()`: missing keys cause skip with warning. |
| MAJOR (ai-specialist MAJOR-2) — DataType=str has no typo safety | 3.3 | Added startup cross-check in `SourceRegistry.__init__()`: each source's `supported_data_types` must appear in the extractor index. Raises `ValueError` on mismatch. |
| MINOR (security MINOR-1) — _blob_path() startswith prefix collision | 3.7 | Replaced `str(path).startswith(str(self._data_dir))` with `path.is_relative_to(self._data_dir)`. |
| MINOR (security MINOR-3) — /player/refresh missing region validation | — | Out of scope for design doc — tracked as code fix in the UI service. |
| MINOR (security MINOR-4) — no blob size bound | 3.9 | Added `MAX_BLOB_SIZE_BYTES = 2 * 1024 * 1024` (2 MB) check after receiving `response.raw_blob`. Oversized responses treated as UNAVAILABLE. |

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

---

## Review Round 2 — Optimizer

**Reviewer**: optimizer
**Round**: 2
**Date**: 2026-03-25

### Round 1 Issue Disposition

All five Round 1 optimizer issues have been reviewed against the revised design.

**Q1 (find_any() O(S*B*L) disk scan on hot path)** -- RESOLVED. The move from JSONL bundles to per-blob files (Section 3.7, revised) eliminates the line-by-line scan entirely. `find_any()` now performs one `stat()` call per source directory via `Path.exists()` on a deterministic path `{source_dir}/{platform}/{match_id}.json`. Complexity drops from O(S * B * L) to O(S) where S = number of source directories. At S=2-5, this completes in single-digit microseconds. The design explicitly documents this at Section 3.7 line 505: "one stat() call per source directory -- O(S) where S = number of sources." No further action needed.

**Q2 (wait_for_token() blocks 60s before waterfall fallback)** -- RESOLVED. Section 3.5 introduces `try_token()` as a non-blocking companion to `wait_for_token()`. `RiotSource.fetch()` calls `try_token()` and returns `FetchResult.THROTTLED` immediately if no token is available (Section 3.5, lines 333-339). The coordinator sees THROTTLED within a single Redis round-trip (~0.5ms) and proceeds to the next source. The 60-second `wait_for_token()` is retained only for non-waterfall paths (timeline fetch). No further action needed.

**Q3 (Thread pool contention with two sequential disk writes)** -- MOSTLY RESOLVED. The torn-write risk for BlobStore is eliminated by per-blob atomic writes (tmpfile, fsync, os.replace). BlobStore writes target unique file paths per match_id, so no concurrent-append interleaving is possible. However, the RawStore write path (`raw_store.py:158-178`) still appends to shared JSONL bundles via `open("a")` above PIPE_BUF. This is a pre-existing issue not introduced by the waterfall design, and is mitigated by Redis SET NX serialization. The thread pool saturation concern (40 tasks for ~8-36 workers under burst) remains theoretically possible but is bounded by the Riot API rate limit: at most 20 tokens can be granted per second, and the waterfall does not multiply disk writes beyond one BlobStore write + one RawStore write per successful fetch. No regression from the waterfall design.

**Q4 (sources_for() fresh list comprehension per call)** -- NOT ADDRESSED but acceptable. `SourceRegistry.sources_for()` (Section 3.3, lines 236-245) still performs an O(S) filter and allocates a new list on every `fetch_match()` call. At S=2-5 and 20 calls/s, this is 20 list allocations of 2-5 elements per second -- negligible in both CPU and GC pressure. The design already pre-computes the extractor index (Section 3.9, `_extractor_index` dict), which handles the higher-fanout lookup. Pre-computing `sources_for()` into a `dict[DataType, list[SourceEntry]]` would save ~100ns per call; at 20/s this is 2 microseconds per second of wall time. Not worth adding complexity. See MINOR-1 below.

**Q5 (Full dict deserialization doubles transient memory)** -- RESOLVED. Section 3.9 clarifies the deserialization strategy: the coordinator calls `json.loads(raw_blob)` exactly once per fetch, producing a single dict that is passed to both `can_extract()` and `extract()`. `BlobStore.read()` returns a parsed dict directly (Section 3.7, line 479), so the cache-hit path also avoids double deserialization. The two dicts in memory (source blob dict + extracted Riot-shaped dict) are inherent to the extraction process -- the extractor must produce a new dict from the source dict. At 20 concurrent fetches with 300-500 KB per pair, transient heap is 6-10 MB. This is well within acceptable bounds for a Python process. The design correctly chose interface clarity (dict) over marginal memory savings (bytes with partial parsing).

### Issues Found

**MINOR-1: SourceRegistry.sources_for() allocates per call on an immutable result**

- **File**: Section 3.3, `SourceRegistry.sources_for()` (lines 236-245)
- **Current complexity**: O(S) filter + list allocation per call, S = number of registered sources
- **Scale impact**: At S=5, 20 calls/s: 100 frozenset membership checks and 20 list allocations per second. Negligible.
- **Recommendation**: Pre-compute `dict[DataType, list[SourceEntry]]` at `__init__` time, keyed by all unique DataType values across all registered sources. This is consistent with the `_extractor_index` pattern already used in `WaterfallCoordinator.__init__()` (Section 3.9, lines 598-602). One-line change: `self._by_type: dict[DataType, list[SourceEntry]] = ...` computed once.
- **Priority**: MINOR. The current implementation is correct and the constant factor is negligible at S=2-5. This is a code consistency suggestion (match the `_extractor_index` pattern), not a performance fix.

**MINOR-2: find_any() iterdir() order is non-deterministic -- no performance impact but merits documentation**

- **File**: Section 3.7, `BlobStore.find_any()` (lines 516-523)
- **Current complexity**: O(S) stat calls, one per source directory. Correct.
- **Concern**: `Path.iterdir()` order is filesystem-dependent. If two sources both have a blob for the same match_id, `find_any()` returns whichever it encounters first. The formal verifier (Round 1, Q4) raised this as a data quality concern; from a performance perspective, there is no issue -- both paths cost one `stat()` + one `read_bytes()`. However, the design could short-circuit after the first hit with higher-fidelity data by checking source directories in registry priority order rather than iterdir() order.
- **Recommendation**: Replace `self._data_dir.iterdir()` with iteration over known source names from the registry, checked in priority order. This changes the method signature to accept a `source_names: list[str]` parameter (ordered by priority). `find_any()` then checks `{data_dir}/{source_name}/{platform}/{match_id}.json` for each name in order, and returns the first hit. Same O(S) cost, deterministic order, and the highest-priority source's blob is always preferred.
- **Priority**: MINOR. Not a performance issue. Deterministic behavior is a correctness improvement that the formal verifier already flagged.

**MINOR-3: FetchContext.extra dict is created via default_factory on every FetchContext instantiation even when unused**

- **File**: Section 3.2, `FetchContext` (lines 128-138)
- **Current complexity**: O(1) per instantiation. `field(default_factory=dict)` allocates an empty dict (~64 bytes on CPython) for every FetchContext, even when no source uses `extra`.
- **Scale impact**: At 20 messages/s: 20 empty dict allocations per second. This is approximately 1.3 KB/s of short-lived heap objects. Negligible.
- **Note**: The frozen=True dataclass means FetchContext instances are not mutated after creation, so there is no risk of dict sharing or copying. The coordinator builds one FetchContext per message and passes it by reference to each source. No copying occurs.
- **Priority**: MINOR. No fix needed. The `extra: dict` pattern is standard Python and the allocation cost is trivial.

### Performance Assessment of Revised Design

The revised design addresses all five Round 1 optimizer concerns. The key performance characteristics are sound:

1. **BlobStore hot path**: O(S) stat calls per `find_any()`, S=2-5. Per-blob files eliminate all line scanning, JSONL parsing, and zstd decompression from the fetch path. This is a substantial improvement over the Round 1 JSONL bundle design.

2. **Coordinator loop**: O(S) sources checked per fetch, with O(1) extractor lookup via pre-computed `_extractor_index`. The `json.loads()` call happens at most once per fetch. No redundant deserialization.

3. **try_token() non-blocking path**: Single Redis round-trip (~0.5ms) for rate limit check. THROTTLED result triggers immediate fallback. No 60-second blocking.

4. **Atomic disk writes**: tmpfile + fsync + os.replace eliminates torn-write risk. Write-once semantics (check exists before write) prevent redundant I/O.

5. **Memory**: Two dicts per in-flight fetch (source blob + extracted data), 300-500 KB per pair, 6-10 MB at 20 concurrent fetches. Acceptable for a Python service.

The bottleneck remains the Riot API rate limit (20 req/s, 100 req/2min), not any computation or I/O in the waterfall. The design correctly optimizes for the second-order concerns (disk I/O patterns, thread pool usage, Redis round-trips) without over-engineering for a scale the pipeline will not reach.

### Verdict

APPROVED

---

## Review Round 2 — AI Specialist

**Reviewer**: ai-specialist  
**Round**: 2  
**Date**: 2026-03-25  

### Round 1 Issue Disposition

All five Round 1 AI Specialist issues were addressed in the Round 2 revision:

1. **`Source.fetch()` signature assumes Riot match_id lookup** — RESOLVED. `FetchContext` dataclass (Section 3.2) replaces bare `match_id: str`. Each source picks the fields it needs (`match_id`, `puuid`, `region`, or `extra`). The protocol no longer encodes "Riot is the only real fetcher." A future source that indexes by summoner+timestamp uses `context.puuid` + `context.region` and fetches normally instead of returning `UNAVAILABLE`.

2. **`find_any()` cross-source ID namespace divergence** — RESOLVED. Section 3.7 now documents that blobs are keyed by the `match_id` from the stream envelope, and op.gg cache hits only apply to OPGG-prefixed match_ids that entered the pipeline via the crawler. Riot-originated match_ids do not match op.gg blobs by design. The ID namespace boundary is explicit rather than a latent bug.

3. **`DataType` closed enum vs. zero-edit extensibility** — RESOLVED. `DataType = str` alias (Section 3.1) with module-level constants. New sources define their own constants in their own packages without editing `base.py`. The coordinator treats DataType as an opaque key. This is the correct tradeoff.

4. **`available_data_types` reliability for proactive emit** — PARTIALLY RESOLVED. The proactive emit is deferred (Section 5 "Deferred Implementation"). This sidesteps the immediate concern but does not address the validation gap for when it is activated. See MAJOR-3 below.

5. **`is_primary` boolean vs. per-DataType authority** — RESOLVED. `primary_for: frozenset[DataType]` (Section 3.3) replaces the boolean. Riot can be primary for MATCH but not BUILD. A source with `primary_for=frozenset()` is never authoritative. This correctly models divergent data-type authority across sources.

### Issues Found

**MAJOR-1: `FetchContext.extra: dict` is an untyped escape hatch that will accumulate source-specific coupling over time.**

Section 3.2 defines `extra: dict = field(default_factory=dict)` as the extensibility mechanism for FetchContext. The coordinator builds FetchContext from the stream envelope, and sources read whatever fields they need from `extra`. The problem is that `extra` is completely untyped. There is no contract for what keys any source expects to find there. When three sources each require different keys in `extra` (e.g., u.gg needs `summoner_level`, another source needs `game_name`), the coordinator must know which fields to populate from the envelope — but it is supposed to be source-agnostic. The coordinator either (a) dumps all available envelope fields into `extra` and hopes each source finds what it needs, or (b) consults source-specific requirements to populate `extra` selectively, which breaks genericity.

Recommendation: Document that `extra` is populated by dumping all non-standard envelope payload fields as-is. Sources must tolerate missing keys (return UNAVAILABLE when required fields are absent). Add a `required_context_keys: frozenset[str]` property to the Source protocol so the coordinator can log a warning at startup when a source's required keys are not available in the envelope schema — without making the coordinator source-specific. This is not a blocker because the current two sources (Riot and op.gg) both work with the core `match_id`/`puuid`/`region` fields, but it will become a friction point at the third source.

**MAJOR-2: `DataType = str` alias provides zero static safety against typo bugs at source registration time.**

Section 3.1 states "Typos are caught by tests, not by enum membership." This is correct as a description but insufficient as a mitigation strategy. Consider: a source declares `supported_data_types = frozenset({"mtach"})` (typo). The coordinator calls `registry.sources_for("match")`, the typo'd source is filtered out, and it silently never receives traffic. No error, no warning, no test failure unless there is a specific test that asserts this source appears in the `sources_for("match")` result. The design's testing strategy (Section 10.1) uses mock sources with correct strings — it does not test real source registrations against real DataType constants.

Recommendation: Add a startup validation step in `SourceRegistry.__init__` that cross-checks each source's `supported_data_types` against the extractor index keys. If a source declares support for a data type but no extractor exists for `(source.name, data_type)`, raise a configuration error. This catches typos and mismatches at startup rather than silently dropping traffic. The str alias is the right extensibility choice; the missing piece is a registration-time validation gate.

**MAJOR-3: `find_any()` is non-deterministic across sources and does not respect priority order.**

This was raised in the Formal Verifier's Round 1 Q4 but not addressed in the revision notes. `find_any()` (Section 3.7, line 516) iterates `self._data_dir.iterdir()` — filesystem iteration order. If both `riot/NA1/NA1_12345.json` and `opgg/NA1/NA1_12345.json` exist on disk, `find_any()` returns whichever the filesystem yields first. The op.gg blob has degraded fields (empty `championName`, zero `goldEarned`, no `perks` — Section 3.8). Returning the op.gg blob when a full Riot blob exists silently degrades data quality.

Recommendation: Pass the `SourceRegistry` (or just the priority-ordered source name list) to `BlobStore` at construction time. `find_any()` should iterate source directories in priority order rather than filesystem order. This is a straightforward change: replace `self._data_dir.iterdir()` with iteration over `[self._data_dir / name for name in self._priority_order]`.

**MAJOR-4: `try_token()` non-blocking pattern has a starvation risk under sustained multi-source load.**

Section 3.5 introduces `try_token()` as a non-blocking companion to `wait_for_token()`. When Riot's rate limit is saturated, `try_token()` returns False immediately, the coordinator falls through to the next source, and that message is served by op.gg (or whichever source succeeds). This is correct for the happy path. The starvation risk: if the waterfall is configured as `riot,opgg` and Riot is always at capacity (sustained 20 req/s from other consumers), every `try_token()` call returns False. All match fetches fall through to op.gg. But op.gg returns UNAVAILABLE for direct fetches (Section 3.6), so all_exhausted is returned, and the message goes to DLQ. Riot tokens are being consumed by other code paths (timeline fetch uses blocking `wait_for_token()`, non-waterfall paths), leaving zero capacity for the waterfall's non-blocking path. The waterfall never gets a Riot token because it never waits.

This is not a bug in the `try_token()` pattern itself — the pattern is correct for cooperative fallthrough. But the design should document this operational constraint: `try_token()` only works if the Riot rate limit is not permanently saturated by blocking callers. If the timeline fetch path or other blocking consumers exhaust all tokens, the non-blocking waterfall path sees permanent denial. The mitigation is either (a) token reservation (allocate a fraction of Riot tokens exclusively for the waterfall path) or (b) a configurable `try_token_max_wait_ms` that allows a brief wait (e.g., 500ms) before falling through — short enough for responsiveness but long enough to catch token availability during normal rate-limit cycling.

Severity: MAJOR, not CRITICAL, because at current scale (20 req/s limit with 1-10 workers), the timeline path consumes at most 1 req per match fetch, leaving 19/s for the waterfall. Starvation requires sustained full-capacity utilization, which is unlikely at dev-API scale. But this should be documented as a known limitation for production-key scale.

**MINOR-1: `WaterfallResult.blob_validation_failed` is a diagnostic signal with no consumer.**

Section 3.9 sets `blob_validation_failed=True` on the WaterfallResult when `extractor.can_extract()` returns False. The coordinator then continues to the next source. The Fetcher receives the WaterfallResult and handles the `status` field (`success`, `not_found`, `auth_error`, `all_exhausted`, `cached`). The `blob_validation_failed` flag is never checked by any consumer described in the design. It does not affect the Fetcher's control flow — the Fetcher routes on `status`, not on `blob_validation_failed`.

This flag is fine as a diagnostic/logging aid. But the design should clarify: (a) is `blob_validation_failed=True` combined with `status="all_exhausted"` a distinct failure mode that the Fetcher should log differently (e.g., "all sources exhausted and at least one produced an un-extractable blob")? And (b) should the Fetcher emit a metric for blob validation failures so operators can detect a source returning garbage? Without this, a source silently producing invalid blobs on every request would be indistinguishable from a source that is simply unavailable.

**MINOR-2: Proactive emit to `stream:blob_available` needs `can_extract()` validation for the declared-but-not-requested data types.**

Section 5 says the coordinator publishes to `stream:blob_available` when `available_data_types` contains types beyond what was requested. But `can_extract()` is only called for the *requested* data type during the fetch path (Section 3.9). For the additional data types declared in `available_data_types`, no extraction validation occurs before the notification is published. A future BuildProcessor consuming `stream:blob_available` would read the blob, attempt extraction, and potentially fail — with no pre-validation gate. Since this is deferred, the fix is simple: when the proactive emit is activated, the coordinator should call `can_extract()` for each additional data type before including it in the `stream:blob_available` payload. Document this as a requirement for the future implementation.

**MINOR-3: `SourceRegistry` ordering uses `priority: int` with no uniqueness constraint.**

Section 3.3 sorts entries by `priority` (lower = tried first). If two sources share the same priority value, Python's `sorted()` preserves insertion order (stable sort), but the design does not document this. More importantly, equal-priority sources create ambiguity about which is tried first, and the behavior depends on the order entries appear in the constructor's list — which depends on how `SOURCE_WATERFALL_ORDER` is parsed. At 2-5 sources this is unlikely to cause issues, but adding a uniqueness check (`assert len(set(e.priority for e in entries)) == len(entries)`) in `SourceRegistry.__init__` would make ordering fully deterministic and prevent accidental equal-priority registration.

### Verdict

APPROVED WITH MINORS

The Round 2 revision successfully addresses all Round 1 blockers and critical issues. The architecture is sound: the waterfall coordination pattern is correct, `FetchContext` is a good extensibility mechanism, `DataType = str` is the right tradeoff for openness, `try_token()` is the correct async pattern for cooperative fallthrough, and the `primary_for` model correctly handles per-DataType authority.

The three MAJOR issues (untyped `extra` dict, missing registration-time DataType validation, non-deterministic `find_any()` ordering) are design refinements that should be addressed in Round 3 but do not represent fundamental flaws. The MAJOR-4 starvation concern is an operational documentation gap, not a design bug. The three MINOR issues are quality-of-life improvements that can be deferred.

---

## Review Round 2 — Formal Verifier

**Reviewer**: formal-verifier  
**Round**: 2  
**Date**: 2026-03-25  

### Round 1 Issue Resolution Assessment

All five Round 1 formal-verifier issues have been addressed. Assessment of each:

**R1-Q1 (Poisoned blob loop).** Resolved. The pre-persist `can_extract()` gate (Section 3.9) prevents un-extractable blobs from reaching BlobStore. The coordinator calls `can_extract(blob_dict)` before `blob_store.write()`, and on failure skips persistence and continues to the next source. This eliminates the infinite extraction-failure loop. The fix is correct: the invariant "every blob in BlobStore is extractable by at least one registered extractor" now holds by construction.

**R1-Q2 (Torn JSONL writes).** Resolved. The JSONL bundle format is replaced by one-file-per-blob with atomic tmpfile-fsync-rename writes (Section 3.7). `os.replace()` is atomic on POSIX. Concurrent Fetcher workers writing different match_ids target different file paths, so no coordination is needed. Write-once semantics are enforced by the `path.exists()` check before writing. This eliminates the torn-write race entirely.

**R1-Q3 (retry_after_ms dropped on all_exhausted).** Resolved. The coordinator collects `retry_after_ms` hints from every source that returns THROTTLED into a `retry_hints` list and propagates `max(retry_hints)` through `WaterfallResult.retry_after_ms` (Section 3.9, lines 684-693). The Fetcher forwards this to `nack_to_dlq` unchanged. The DLQ/Recovery/Delay Scheduler chain is preserved.

**R1-Q4 (find_any() non-deterministic source order).** Partially resolved. The per-blob file format (Section 3.7) reduces `find_any()` to O(S) stat calls, eliminating the performance concern. However, the non-deterministic iteration order of `Path.iterdir()` remains. See FV-R2-3 below.

**R1-Q5 (RawStore TTL-induced scan cost).** Resolved by the format change. BlobStore `find_any()` is now O(S) stat calls (S = number of source directories), not a linear scan. RawStore's own bundle scan remains unchanged but is outside the scope of this design.

### Issues Found

**FV-R2-1 [MAJOR]: BlobStore write-once check is non-atomic — concurrent writers can both pass `path.exists()` and race on `os.replace()`**

Section 3.7, `BlobStore.write()` (lines 481-488):

```python
async def write(self, source_name: str, match_id: str, data: str) -> None:
    path = self._blob_path(source_name, match_id)
    if path.exists():
        return  # write-once: do not overwrite
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp_{match_id}_{os.getpid()}.json")
    await asyncio.to_thread(self._atomic_write, tmp, path, data)
```

The `path.exists()` check and the subsequent `_atomic_write` are not atomic. Two Fetcher workers processing the same match_id (possible via XAUTOCLAIM redelivery or DLQ replay) can both observe `path.exists() == False`, both create tmp files, and both call `os.replace()`. The second `os.replace()` silently overwrites the first writer's file.

This is a TOCTOU race. However, the safety impact is limited: both writers hold valid blobs for the same match_id (since both passed `can_extract()`), and the content should be identical (same source, same match). The race produces a correct final state — one writer's file persists.

The tmp file naming uses `os.getpid()`, so two workers in the same process would use the same tmp path. If the Fetcher runs multiple async tasks within a single process (it does — `run_consumer` dispatches sequentially per batch, but XAUTOCLAIM can cause the same match_id to be delivered to different consumer names), two coroutines in the same PID would collide on the tmp file path. The `O_EXCL` flag in `_atomic_write` prevents this: the second coroutine's `os.open(..., O_CREAT | O_EXCL)` will raise `FileExistsError`, which propagates as an unhandled exception.

Concrete execution trace:
1. Worker A (PID 100) calls `write("riot", "NA1_123", data)`. `path.exists()` returns False.
2. Worker B (PID 100, different coroutine via XAUTOCLAIM) calls `write("riot", "NA1_123", data)`. `path.exists()` returns False.
3. Worker A creates `.tmp_NA1_123_100.json` with `O_EXCL` — succeeds.
4. Worker B tries to create `.tmp_NA1_123_100.json` with `O_EXCL` — `FileExistsError`.
5. Worker B's `write()` throws an unhandled exception, propagating to the handler and triggering `_handle_failure` in `service.py`.

Fix: Catch `FileExistsError` (and `OSError` with `errno.EEXIST`) in `write()` and treat it as a successful no-op, identical to the `path.exists()` early return. Alternatively, include a UUID or coroutine ID in the tmp file name to avoid the collision entirely.

**FV-R2-2 [MAJOR]: Crash between `blob_store.write()` and `publish(stream:parse)` — blob is persisted but message is not published; recovery path is correct but undocumented**

Section 3.9 describes the success path:
1. `can_extract(blob_dict)` — passes
2. `blob_store.write(source.name, match_id, raw_blob)` — blob on disk
3. `extractor.extract(blob_dict)` — produces Riot-shaped dict
4. `raw_store.set(match_id, extracted_data)` — Redis SET NX + disk
5. `publish(stream:parse, ...)` — downstream notification
6. `ack(stream:match_id, msg_id)` — remove from PEL

If the Fetcher crashes after step 2 but before step 6, the message remains in the PEL (not ACKed). On restart or XAUTOCLAIM, the coordinator re-enters at step 1 (RawStore check). If step 4 completed, `raw_store.exists()` returns True and the coordinator takes the idempotent "cached" path — correct. If the crash was between steps 2 and 4, `raw_store.exists()` returns False. The coordinator then calls `blob_store.find_any()`, finds the blob from step 2, extracts it, writes to RawStore, and proceeds — also correct.

However, if the crash is between steps 4 and 5 (RawStore written, but `publish` not called), the message is redelivered, `raw_store.exists()` returns True, and the coordinator returns `WaterfallResult(status="cached")`. The Fetcher then calls `_publish_and_ack()` — correct.

Verdict: The recovery path is sound for all crash points. The at-least-once guarantee is preserved. No message loss occurs. This is not a bug, but the design document should explicitly enumerate these crash-recovery scenarios to prevent implementation mistakes.

**FV-R2-3 [MINOR]: `find_any()` iteration order remains non-deterministic when multiple sources have cached blobs for the same match_id**

Section 3.7 (lines 516-523): `find_any()` iterates `self._data_dir.iterdir()` which returns entries in filesystem-dependent order. If two source directories (`riot/` and `opgg/`) both contain a blob for the same match_id, the returned source depends on inode ordering. The extractor selected depends on which source is returned, and different extractors may produce different data quality (Riot: full fields; op.gg: degraded fields per Section 3.8).

The R2 revision (BLOCKER-1 fix) narrows the scope: op.gg blobs are keyed by op.gg IDs (`OPGG_NA1_...`) and Riot blobs by Riot IDs (`NA1_...`). Since the match_id namespaces do not overlap, a lookup by `NA1_12345` will never find an op.gg blob. This makes the non-determinism a theoretical concern only for the current two-source configuration.

However, if a future source uses the same Riot match_id namespace (e.g., a source that fetches Riot data via a third-party proxy), the non-determinism becomes a real data quality issue. The fix is straightforward: `find_any()` should accept the `SourceRegistry`'s priority order and iterate source directories in that order, or the coordinator should pass the priority-sorted source name list to `find_any()`.

**FV-R2-4 [MINOR]: `can_extract()` validation before persist does not re-validate on the cache-hit path**

Section 3.9 establishes the invariant: "every blob in BlobStore has passed `can_extract()`." This holds for newly fetched blobs (the coordinator validates before `blob_store.write()`). However, the BlobStore cache-hit path (Step 2, `find_any()`) reads a blob from disk and passes it directly to `extractor.extract()` without calling `can_extract()` first.

If the extractor's `can_extract()` logic changes between deployments (e.g., a code update tightens validation), a blob that passed the old `can_extract()` may fail the new `extract()`. The result: `ExtractionError` on the cache-hit path. The coordinator would need to catch this, skip the cached blob, and fall through to the network fetch path.

The design's coordinator pseudocode (Section 3.9) does not show an `ExtractionError` handler on the cache-hit path. If `extract()` throws on a cached blob, the exception propagates to `_handle_with_retry` in `service.py`, which increments the retry counter and eventually sends the message to DLQ — even though a network fetch from a working source would succeed.

Fix: On the cache-hit path, either (a) call `can_extract()` before `extract()` and fall through to the network path on failure, or (b) catch `ExtractionError` from `extract()` on the cache-hit path and fall through. Option (b) is simpler and handles both validation changes and corrupt-on-disk scenarios.

**FV-R2-5 [MINOR]: `max(collected hints)` for `retry_after_ms` is the correct choice**

The task asks whether `max` or `min` is correct. Analysis:

`retry_after_ms` controls when the Delay Scheduler re-delivers the message to the original stream. After all sources are exhausted, the message will re-enter the waterfall and try all sources again from the top. If Source A said "retry after 5s" and Source B said "retry after 30s", using `min(5s)` would re-deliver after 5 seconds, but Source B is still unavailable for another 25 seconds. The message would exhaust Source A (possibly throttled again) and hit Source B prematurely, resulting in another all-exhausted cycle. Using `max(30s)` ensures that when the message re-enters the waterfall, all sources have had time to recover.

`max` is correct. It maximizes the probability that at least one source is available on retry, reducing wasted DLQ cycles. The only downside is increased latency for the individual message, but this is the correct tradeoff for a rate-limited pipeline.

**FV-R2-6 [MINOR]: `try_token()` false-negative fallthrough to op.gg is safe but warrants documentation**

The task asks whether a transient false-negative from `try_token()` (token available but `try_token()` returns `False` due to a race with another worker consuming the last token) can cause the coordinator to fall through to op.gg unnecessarily.

Analysis: `try_token()` calls the same atomic Lua script as `acquire_token()`. The Lua script atomically checks both sliding windows and either adds the token or denies. There is no false-negative: if the Lua script returns denial, the rate limit truly was saturated at the instant of the EVAL. Between the EVAL and the coordinator's next action, another worker might complete and free a slot, but the coordinator has already moved on.

This is safe. The worst case is: Riot had 1 token available, Worker A consumed it via `try_token()`, Worker B's `try_token()` was denied, Worker B falls through to op.gg (which returns UNAVAILABLE), and Worker B's message goes to DLQ. On retry, Worker B gets a Riot token. No data loss, no correctness violation. The fallthrough is a performance optimization miss, not a safety issue.

However, if op.gg (or a future source) returns SUCCESS on the fallthrough, the coordinator persists a lower-fidelity blob when a Riot blob would have been available milliseconds later. This is an acceptable tradeoff: the pipeline values progress over perfection, and the blob is still valid (passed `can_extract()`).

**FV-R2-7 [MINOR]: `BlobStore.find_any()` cannot return a partial/empty blob under concurrent writes**

The task asks whether concurrent writes can cause `find_any()` to read a partial blob.

Analysis: BlobStore writes use the tmpfile-fsync-rename pattern (Section 3.7, `_atomic_write()`). The blob file at the final path either does not exist (pre-rename) or is complete (post-rename). `os.replace()` is atomic on POSIX — there is no intermediate state where the final path exists but contains partial data.

`find_any()` performs `blob_path.exists()` followed by `blob_path.read_bytes()`. There is a TOCTOU gap: the file could be deleted between `exists()` and `read_bytes()`. However, BlobStore is write-once with no deletion path (eviction is manual/cron, per Section 3.7). Under the current design, once a file exists at the final path, it is never modified or removed during normal operation.

Therefore, `find_any()` cannot return a partial or empty blob. It either returns a complete, valid blob or None.

**FV-R2-8 [MAJOR]: When all sources are exhausted with `blob_validation_failed=True`, the message enters DLQ with no mechanism to skip the bad source on retry**

The coordinator's pseudocode (Section 3.9, lines 670-694) shows:

```python
if response.result == FetchResult.SUCCESS:
    blob_dict = json.loads(response.raw_blob)
    extractor = self._get_extractor(entry.name, data_type)
    if extractor and not extractor.can_extract(blob_dict):
        continue  # try next source
```

If Source A returns SUCCESS with a blob that fails `can_extract()`, the coordinator sets `blob_validation_failed=True` and continues to Source B. If Source B is THROTTLED, and no other sources exist, the coordinator returns `all_exhausted` with `blob_validation_failed=True`.

The message enters DLQ. On retry (via Recovery/Delay Scheduler), the message re-enters the waterfall. Source A may return the same bad blob again (the upstream data has not changed). Source B may be throttled again. This creates a retry loop where the message oscillates between DLQ and the waterfall until either (a) Source A's data changes (unlikely — the data is static match history), (b) Source B becomes available, or (c) `max_attempts` is exhausted and the message is archived.

Path (c) is the termination guarantee. The `MessageEnvelope.attempts` field is incremented by Recovery on each replay (`streams.py:replay_from_dlq`), and `max_attempts` (default 5 per the existing config) bounds the total replays. After `max_attempts`, Recovery archives the message to `stream:dlq:archive`.

Verdict: The retry loop is bounded by `max_attempts`. There is no infinite loop. However, the design should document that `blob_validation_failed` messages will consume all `max_attempts` retry cycles against the same bad source before being archived. If the primary source is the one returning bad blobs, this means up to 5 DLQ round-trips (each with a delay) before the message is permanently failed. Consider: when `blob_validation_failed=True` AND the failing source is `primary_for` the requested data_type, the coordinator could return a terminal failure immediately rather than `all_exhausted`, since the primary source's data is unlikely to change.

### Summary Table

| ID | Severity | Title |
|----|----------|-------|
| FV-R2-1 | MAJOR | BlobStore write-once TOCTOU: same-PID tmp file collision raises unhandled `FileExistsError` |
| FV-R2-2 | (informational) | Crash recovery between `blob_store.write` and `publish` is correct but undocumented |
| FV-R2-3 | MINOR | `find_any()` non-deterministic source order with same-namespace match_ids |
| FV-R2-4 | MINOR | Cache-hit path skips `can_extract()` — stale blobs can cause `ExtractionError` |
| FV-R2-5 | (informational) | `max(hints)` for `retry_after_ms` is confirmed correct |
| FV-R2-6 | (informational) | `try_token()` false-negative fallthrough is safe |
| FV-R2-7 | (informational) | `find_any()` cannot return partial blob under concurrent writes |
| FV-R2-8 | MAJOR | `blob_validation_failed` messages exhaust all retries against unchanging bad source |

### Verdict

**APPROVED WITH MINORS**

The R2 design correctly addresses all five Round 1 formal-verifier issues. The at-least-once delivery guarantee is preserved across all crash points. The pre-persist validation gate eliminates the poisoned blob loop. The `retry_after_ms` propagation via `max(hints)` is correct. The per-blob atomic write eliminates torn writes.

Three issues require attention before implementation:

1. **FV-R2-1** (MAJOR): Handle `FileExistsError` in `BlobStore.write()` or use a unique tmp file name per coroutine. Without this, same-PID XAUTOCLAIM redeliveries will throw unhandled exceptions.
2. **FV-R2-8** (MAJOR): Document the retry-exhaustion behavior for `blob_validation_failed` messages. Optionally, treat `blob_validation_failed` from the primary source as terminal to avoid wasting retry budget.
3. **FV-R2-4** (MINOR): Add an `ExtractionError` catch on the cache-hit path to fall through to the network fetch when a cached blob is no longer extractable.

None of these are blockers. All have straightforward fixes that do not require architectural changes. The core correctness properties — no message loss, idempotent writes, bounded retries, atomic blob persistence — hold.

---

## Review Round 2 — Security

**Reviewer**: security
**Round**: 2
**Date**: 2026-03-25

### Round 1 Issue Disposition

All five Round 1 security questions have been reviewed against the revised design.

**Q1 (BlobStore path traversal via source.name and match_id)** -- RESOLVED. Section 3.7 now implements three-layer path traversal prevention: (1) `source.name` validated against `^[a-z0-9_]+$` at `SourceEntry` construction (Section 3.3, line 226), (2) platform segment validated against `^[A-Z0-9]+$` before path construction (line 454), (3) `Path.resolve()` + startswith boundary check against `BLOB_DATA_DIR` (lines 460-462). The regex allowlists are strict enough to prevent `../`, null bytes, and Unicode normalization attacks -- `[a-z0-9_]` and `[A-Z0-9]` only match ASCII literals. The `Path.resolve()` check is the defense-in-depth backstop. See MINOR-1 below for remaining edge case.

**Q2 (Untrusted third-party blob content flowing through extractors without schema validation)** -- PARTIALLY RESOLVED. The `can_extract()` pre-persist validation gate (Section 3.9, line 621) prevents un-extractable blobs from being stored and causing loops. However, the deeper concern -- that extractor output is not schema-validated before reaching RawStore and the parser -- remains. The design explicitly states "the coordinator does not inspect the extracted dict" (Section 3.4, line 303). The mitigation is that the parser already applies its own field validation via `_PARTICIPANT_FIELD_MAP` defaults and `_validate()` (which raises on missing required fields like `gameStartTimestamp`). Data that passes parser validation enters Redis hashes where the UI reads it. The UI HTML-escapes all rendered values (`html.escape()` is used throughout `rendering.py`). The risk is low: op.gg data degradation (missing fields) is handled by parser defaults, and the UI does not render raw blob content. See MINOR-2 below.

**Q3 (find_any() iterates attacker-controlled directory names)** -- RESOLVED. The move from JSONL bundles to per-blob files eliminates the line-scanning attack surface. `find_any()` (Section 3.7, lines 500-523) now performs `Path.exists()` checks on deterministic paths `{source_dir}/{platform}/{match_id}.json`. An attacker-planted directory is only dangerous if it contains a file at the exact expected path. The `_validate_platform()` check (line 454) ensures the platform segment is `^[A-Z0-9]+$`, preventing traversal via the platform component. The residual risk is a pre-planted blob file at a valid path -- but this requires write access to `BLOB_DATA_DIR`, which implies container compromise (a higher-severity threat that subsumes BlobStore poisoning). The formal verifier's MINOR-2 recommendation (iterate by registry priority order instead of iterdir()) would further harden this by ensuring only known source directories are checked.

**Q4 (Op.gg scraping -- response integrity and TLS posture)** -- UNCHANGED, acceptable risk. The `OpggClient` (`opgg_client.py:75-78`) uses `httpx.AsyncClient` with default TLS settings, which verifies certificates via the system CA bundle. No `verify=False` is set. MITM on a TLS-verified connection requires a compromised CA, which is outside the threat model for a personal data pipeline. The risk of op.gg returning manipulated data (intentional poisoning vs. blocking) is inherent to scraping an undocumented API and is accepted by design. The `can_extract()` gate prevents structurally invalid responses from being persisted.

**Q5 (Rate limit bypass through waterfall multiplication)** -- RESOLVED. The `try_token()` non-blocking approach (Section 3.5, lines 333-339) means that when the Riot rate limiter is saturated, `RiotSource` returns `THROTTLED` without consuming a token or making an HTTP call. The coordinator then tries the next source (op.gg), which has its own independent rate limiter (`ratelimit:opgg:*` keys, 2 req/s per `opgg_client.py:72-73`). Each source's rate limiter is self-contained. A message that exhausts all sources enters the DLQ with `retry_after_ms = max(all hints)` -- it does not re-enter the waterfall until the delay expires. Flooding `stream:match_id` is bounded by consumer group semantics: each message is delivered to exactly one consumer, and unACKed messages are not re-delivered until `XAUTOCLAIM` after the idle timeout. The multiplication factor is exactly N HTTP calls across N sources (at most), each governed by its own rate limiter. This is the intended behavior, not a bypass.

### Issues Found

**MINOR-1: BlobStore._blob_path() startswith check is vulnerable to prefix collision**

- **Section**: 3.7, `_blob_path()` (lines 457-463)
- **Code**: `if not str(path).startswith(str(self._data_dir)):`
- **Issue**: String prefix matching on resolved paths is subtly incorrect when `BLOB_DATA_DIR` is a prefix of another directory. Example: if `BLOB_DATA_DIR = /data/blob`, a crafted path resolving to `/data/blob-escape/evil.json` would pass the `startswith("/data/blob")` check. In practice, this is unexploitable because (a) `source.name` is validated to `^[a-z0-9_]+$` which cannot produce `-escape`, and (b) `platform` is validated to `^[A-Z0-9]+$`. The regex allowlists make the startswith check redundant rather than vulnerable. However, defense-in-depth demands the check be correct on its own.
- **Fix**: Append a path separator to the prefix: `str(path).startswith(str(self._data_dir) + os.sep)`. Alternatively, use `path.is_relative_to(self._data_dir)` (Python 3.9+), which handles this correctly.
- **Impact**: None in practice due to the upstream regex validation. This is a code correctness suggestion.

**MINOR-2: find_any() reads blobs from filesystem-discovered directories without validating source_name**

- **Section**: 3.7, `find_any()` (lines 500-523)
- **Code**: `for source_dir in self._data_dir.iterdir(): ... return (source_dir.name, json.loads(data))`
- **Issue**: `find_any()` returns `source_dir.name` as the `source_name`, which the coordinator uses to look up an extractor via `_get_extractor(source_name, data_type)`. If a directory under `BLOB_DATA_DIR` has a name that does not match any registered source, `_get_extractor()` returns `None`, and the coordinator should skip extraction (the code at Section 3.9 line 678-681 shows `if extractor and not extractor.can_extract(blob_dict)`). However, the design does not explicitly document what happens when `_get_extractor()` returns `None` for a `find_any()` result. If the coordinator proceeds with `None` extractor, it would either crash (AttributeError) or silently skip extraction.
- **Recommendation**: Document explicitly that when `find_any()` returns a source_name with no matching extractor, the coordinator skips the cached blob and falls through to the network fetch path. Alternatively, adopt the optimizer's MINOR-2 recommendation: iterate over known source names from the registry rather than using `iterdir()`, which eliminates this class of issue entirely.
- **Impact**: Low. Requires write access to `BLOB_DATA_DIR` to create a rogue directory. The coordinator's extractor lookup already gates this path. This is a robustness suggestion.

**MINOR-3: /player/refresh endpoint does not validate region parameter**

- **Section**: Outside the design document. Located at `/Users/abhiregmi/projects/LoL-Crawler/lol-pipeline-ui/src/lol_ui/routes/stats.py:453-489`.
- **Code**: The `region` value from the POST body (line 458: `region: str = body.get("region", "na1")`) is included directly in the `MessageEnvelope.payload` (line 482) and published to `stream:puuid` without validation against `_REGIONS_SET`.
- **Contrast**: The `show_stats` endpoint at line 415 validates `if region not in _REGIONS_SET` and returns 400. The `player_refresh` endpoint skips this check.
- **Impact**: An attacker can publish a message to `stream:puuid` with an arbitrary `region` string (e.g., `"../../etc"` or a 10 MB string). The downstream Crawler service uses this region value for Riot API routing via `PLATFORM_TO_REGION` lookup -- an unknown region would cause a `KeyError` or fall through to a default, not a security breach. The Crawler does not use the region for filesystem paths. The risk is service disruption (Crawler error on invalid region), not data compromise.
- **Fix**: Add `if region not in _REGIONS_SET: return JSONResponse({"error": "invalid region"}, status_code=400)` before the PUUID lookup, matching the `show_stats` pattern.

**MINOR-4: Blob size not bounded before persistence or extraction**

- **Section**: 3.9 (WaterfallCoordinator success path), 3.7 (BlobStore.write)
- **Issue**: When a source returns `FetchResponse.raw_blob`, the coordinator calls `json.loads(raw_blob)` and passes the resulting dict to `can_extract()` and `extract()`, then persists the raw blob to disk via `BlobStore.write()`. Neither the coordinator nor the BlobStore imposes a maximum blob size. A compromised or misbehaving third-party source could return a 100 MB+ response that passes `can_extract()` (which only checks for the presence of specific keys) but causes excessive memory allocation during `json.loads()` and excessive disk usage on write.
- **Context**: Riot match-v5 responses are 15-50 KB. Op.gg responses are similarly sized. A response exceeding 1 MB is anomalous.
- **Fix**: Add a `MAX_BLOB_SIZE_BYTES` constant (e.g., 2 MB) and check `len(raw_blob) > MAX_BLOB_SIZE_BYTES` before `json.loads()`. Log a warning and treat as `UNAVAILABLE` if exceeded. This prevents both OOM and disk abuse from a single oversized response.
- **Impact**: Low. Requires a compromised source or MITM (which is mitigated by TLS). The 512 MB container `mem_limit` in `docker-compose.yml` (line 7) is the backstop -- a 100 MB blob would not OOM the container, but multiple concurrent oversized blobs could.

### Items NOT Raised (Proportionality Assessment)

The following theoretical concerns were considered and deliberately not raised because they are disproportionate to the threat model of a single-developer personal data pipeline:

- **Symlink attacks on BLOB_DATA_DIR**: Requires container escape or shared volume write access. The `Path.resolve()` call follows symlinks, so a symlink at `{BLOB_DATA_DIR}/riot` pointing to `/etc` would be caught by the startswith boundary check. The regex allowlists on source.name and platform further constrain this. Not a realistic threat.
- **Unicode normalization in source.name or match_id**: The `^[a-z0-9_]+$` and `^[A-Z0-9]+$` regexes match only ASCII. Unicode characters would fail the regex and be rejected. Python's `re.match` does not normalize Unicode before matching by default. No issue.
- **Null bytes in match_id**: Python 3's `Path` operations raise `ValueError` on embedded null bytes. The platform regex `^[A-Z0-9]+$` also rejects null bytes. No issue.
- **Redis key injection via FetchContext.extra**: The `extra` dict is not used in any file path or Redis key construction in the current design. It is passed to `Source.fetch()` only, where each source implementation decides how to use it. Future sources that use `extra` fields in Redis keys or file paths should apply the same sanitization patterns established for `source.name` and `match_id`. This is a documentation note, not a current vulnerability.
- **TLS certificate pinning for op.gg**: Certificate pinning is fragile (breaks on certificate rotation) and disproportionate for a scraping client against a third-party site. The default CA bundle verification is sufficient.

### Verdict

APPROVED WITH MINORS

All four issues are MINOR severity. None are blockers or security vulnerabilities requiring immediate remediation. MINOR-3 (region validation on `/player/refresh`) is the most actionable -- it is a one-line fix in existing code that aligns the endpoint with the validation pattern already established in `show_stats`. MINOR-1 and MINOR-2 are defense-in-depth improvements to BlobStore path handling. MINOR-4 is a hardening measure for resilience against oversized blobs.

Round 1 security concerns (path traversal, poisoned blob loops, rate limit bypass) have been adequately addressed in the revised design. The three-layer path validation (regex allowlist + regex allowlist + resolve boundary check), `can_extract()` pre-persist gate, and `try_token()` non-blocking rate limit check are sound mitigations.

---

## Review Round 2 — Developer

**Reviewer**: developer
**Round**: 2
**Date**: 2026-03-25

### Round 1 Resolution Check

All 5 developer issues from Round 1 (Q1-Q5) were addressed in the R1-to-R2 revision:

- **Q1 (op.gg no-op + cache path broken)**: BLOCKER-1 resolved. Section 3.7 now explicitly scopes op.gg blob cache hits to op.gg-originated match_ids only. The design is honest that op.gg contributes nothing for Riot-originated match_ids and documents this as an accepted limitation, not a hidden gap. Satisfactory.
- **Q2 (retry_after_ms dropped)**: BLOCKER-3 resolved. Section 3.9 now specifies `retry_after_ms = max(all collected hints)` on the `all_exhausted` path, and the coordinator pseudocode (lines 684-692) shows `retry_hints` being collected from every source that returns a hint. Satisfactory.
- **Q3 (find_any() unbounded scan)**: BLOCKER-4 + MAJOR-11 resolved. The JSONL bundle format was replaced with per-blob files. `find_any()` is now O(S) stat calls (one per source directory), not O(S * M * L) line scans. Satisfactory.
- **Q4 (bytes/dict type boundary)**: MAJOR-12 resolved. Section 3.9 now states the coordinator owns the single `json.loads(raw_blob)` call, and `BlobStore.read()` returns a parsed `dict`. The deserialization ownership is explicit. Satisfactory.
- **Q5 (TIMELINE contradiction)**: MAJOR-10 resolved. `TIMELINE` removed from initial DataType constants. A TODO comment documents the future migration. `RiotSource.supported_data_types` is now `frozenset({MATCH})` only (Section 3.5, line 321). Satisfactory.

### Issues Found

**ISSUE-1 (CRITICAL): `find_any()` cache-hit path skips `can_extract()` validation -- reintroduces the poisoned blob loop that BLOCKER-2 was supposed to fix.**

Section 3.9 Step 2 (line 665) says: "If a blob is found, `find_any()` returns the parsed dict directly. The coordinator finds the appropriate extractor via `_get_extractor(source_name, data_type)` and produces Riot-shaped data without any network call."

Section 3.9 Step 3b (lines 621-623) applies `can_extract()` validation before persisting a freshly-fetched blob, which prevents poisoned blobs from being written to disk. But Step 2 reads an already-persisted blob from disk and passes it straight to the extractor without calling `can_extract()`. Consider the scenario:

1. Source A fetches a blob, `can_extract()` returns True, blob is persisted. (Normal.)
2. The extractor is updated (code deploy) such that `can_extract()` would now return False for that blob shape.
3. On redelivery, `find_any()` returns the stale blob. The coordinator calls `extract()` without `can_extract()`, the extractor throws `ExtractionError`, the message fails, re-enters DLQ, re-delivers, hits the same cached blob, throws again -- infinite loop.

This is the same failure mode as BLOCKER-2 (Formal Verifier Q1) but via the cache-hit path instead of the fresh-fetch path. The fix from BLOCKER-2 only gates the fresh-fetch write; it does not gate the cache-hit read.

Fix: In Step 2, after `find_any()` returns `(source_name, blob_dict)`, call `extractor.can_extract(blob_dict)`. If it returns False, delete the stale blob from disk (or skip it and log a warning) and fall through to Step 3. This makes the cache path consistent with the fresh-fetch path.

---

**ISSUE-2 (MAJOR): Missing extractor for a cached blob's `(source_name, data_type)` pair is unhandled -- `_get_extractor()` returns `None` and the coordinator has no specified behavior.**

Section 3.9, Step 2 (line 665): the coordinator calls `_get_extractor(source_name, data_type)` using the `source_name` returned by `find_any()`. `_get_extractor()` returns `Extractor | None` (line 604). If the returned source_name has no registered extractor for the requested data_type (possible if a source package was removed between deployments, or if the blob was written by a source that supported a data_type its extractor does not cover), the coordinator receives `None`.

In the Step 3b pseudocode (lines 678-681), the coordinator checks `if extractor and not extractor.can_extract(blob_dict)` -- but this silently passes when `extractor is None` (the `if extractor` guard means a None extractor skips validation entirely and falls through to "Persist and extract" with no extractor to actually run the extraction).

The design does not specify what happens when `_get_extractor()` returns `None` in either the cache-hit path (Step 2) or the fresh-fetch success path (Step 3b). Both paths need an explicit "no extractor found" branch.

Fix: Specify that `_get_extractor() is None` is treated the same as `can_extract()` returning False -- log a warning, do not persist (Step 3) or skip the cached blob (Step 2), and continue to the next source.

---

**ISSUE-3 (MAJOR): `BlobStore.write()` accepts `data: str` but the coordinator has `raw_blob: bytes` -- type mismatch requires an undocumented conversion.**

Section 3.7 (line 481): `async def write(self, source_name: str, match_id: str, data: str) -> None` -- the parameter type is `str`.

Section 3.2 (line 151): `FetchResponse.raw_blob: bytes | None` -- the field type is `bytes`.

Section 3.9 Step 3b (line 624): "Save raw_blob to blob_store" -- the coordinator passes `raw_blob` to `blob_store.write()`.

The coordinator must convert `bytes` to `str` before calling `write()`. This conversion is not specified. If the coordinator does `raw_blob.decode("utf-8")`, this embeds a UTF-8 assumption. If `write()` accepted `bytes` instead of `str`, the coordinator could pass `raw_blob` unchanged and `_atomic_write` could call `os.write(fd, data)` directly without the `.encode()` call at line 495.

Alternatively, since the coordinator does `json.loads(raw_blob)` to get `blob_dict`, it could re-serialize with `json.dumps(blob_dict)` and pass a `str` -- but that round-trips through deserialization and reserialization, which is wasteful and may alter JSON formatting (key order, whitespace).

Fix: Either (a) change `BlobStore.write()` to accept `data: bytes` (matching `FetchResponse.raw_blob`), or (b) document explicitly that the coordinator passes `response.raw_blob.decode("utf-8")` and that UTF-8 is the assumed encoding for all source responses.

---

**ISSUE-4 (MAJOR): `find_any()` does not respect source priority order -- non-deterministic data quality when multiple blobs exist for the same match_id.**

This was raised by the Formal Verifier (R1 Q4) and the AI Specialist (R2 MAJOR-3) but is not listed in the R1-to-R2 revision table and was not addressed. Section 3.7 (line 516): `find_any()` calls `self._data_dir.iterdir()` and returns the first blob it finds. `Path.iterdir()` order is filesystem-dependent (inode order on ext4, creation order on APFS, undefined by POSIX).

If both `riot/NA1/NA1_12345.json` and `opgg/NA1/NA1_12345.json` exist (possible for op.gg-originated matches where the Riot fetcher also ran), `find_any()` could return the op.gg blob (degraded fields per Section 3.8) instead of the Riot blob (full fields). The data quality outcome is non-deterministic based on filesystem iteration order.

Fix: Pass a priority-ordered list of source names (from `SourceRegistry`) to `BlobStore` at construction time or to `find_any()` as a parameter. Iterate source directories in registry priority order instead of filesystem order. This ensures the highest-priority blob is always preferred.

---

**ISSUE-5 (MAJOR): `try_token()` function referenced in Section 3.5 is not defined -- neither signature nor implementation is specified.**

Section 3.5 (line 333): `RiotSource.fetch()` calls `try_token(self._r, limit_per_second=..., region=...)`. Section 3.5 (line 362) describes it: "a new non-blocking function added to the rate limiter interface. It calls the same dual-window Lua script as `acquire_token()` but returns `False` immediately if no token is available."

The current `rate_limiter.py` exports `acquire_token()` (returns 1 on success, negative wait hint on denial) and `wait_for_token()` (blocks until granted). The design references `try_token()` but does not provide its signature, return type, or implementation. The call site in Section 3.5 uses `try_token(self._r, limit_per_second=..., region=...)` where the `region` parameter does not exist on `acquire_token()` (which takes `key_prefix` instead). The `region` parameter on `wait_for_token()` is documented as "kept for API compat, not used" (rate_limiter.py:67).

Without a concrete signature, an implementer could reasonably create several incompatible versions.

Fix: Add a concrete function signature for `try_token()` in Section 3.5 or in a dedicated subsection. Specify: parameters (should mirror `acquire_token`'s `key_prefix`/`limit_per_second`/`limit_long`, not `wait_for_token`'s unused `region`), return type (`bool`), and the implementation (`return await acquire_token(...) == 1`). Clarify that `region` is not a parameter.

---

**ISSUE-6 (MINOR): `WaterfallResult.status` is a plain string with 5 possible values -- no type safety against typos in coordinator and Fetcher branches.**

Section 3.9 (line 655): `status: str` with values `"success"`, `"not_found"`, `"auth_error"`, `"all_exhausted"`, `"cached"`. The Fetcher (Section 4, lines 719-728) must branch on these strings. A typo like `"all_exausted"` would silently fall through.

Given that `FetchResult` is already an Enum (Section 3.2, line 140), applying the same pattern to `WaterfallResult.status` would be consistent. Alternatively, a `Literal["success", "not_found", "auth_error", "all_exhausted", "cached"]` type annotation would catch typos at mypy time.

Fix: Change `status: str` to `status: Literal["success", "not_found", "auth_error", "all_exhausted", "cached"]` or define a `WaterfallStatus` enum.

---

**ISSUE-7 (MINOR): `BlobStore._blob_path()` assumes match_id always contains an underscore -- `match_id.split("_")[0]` on an ID without underscores returns the entire string as platform.**

Section 3.7 (line 458): `platform = match_id.split("_")[0]`. If a future source produces match IDs without underscores (e.g., a numeric-only ID), `split("_")[0]` returns the full match_id as the "platform" string. This would pass the `^[A-Z0-9]+$` validation (if the ID is all uppercase alphanumeric) and create a misnamed directory, or fail validation with an unclear error message.

This is low-risk because all current sources (Riot, op.gg) use underscore-separated IDs. But the design's genericity claim ("any future provider") makes this worth documenting.

Fix: Add a validation check that `match_id` contains at least one underscore, or document the `{PLATFORM}_{ID}` format as a contract that all match_id values entering the pipeline must follow.

### Verdict

NEEDS REVISION

One CRITICAL issue and four MAJOR issues require resolution before implementation. The CRITICAL issue (ISSUE-1: poisoned blob loop via cache-hit path) is a correctness bug that would produce infinite DLQ cycling in production after any extractor code change. The four MAJOR issues are implementability gaps -- an implementer encountering them would be forced to make undocumented design decisions (type conversions, None handling, iteration order, function signatures) that could diverge from the architect's intent. The two MINOR issues can be deferred to Round 3 or resolved during implementation.

---

## Review Round 3 — Developer

**Reviewer**: developer
**Round**: 3
**Date**: 2026-03-25

### Round 2 Issue Disposition

**ISSUE-1 (CRITICAL): Cache-hit path skips `can_extract()` validation** — RESOLVED.

Section 3.9 Step 2 (lines 726-734) now explicitly enumerates three checks on the cache-hit path:
1. `_get_extractor(source_name, data_type)` — skip if `None`.
2. `extractor.can_extract(blob_dict)` — skip if False, fall through to Step 3.
3. Only if both pass, call `extractor.extract()`.

The text at line 731 states: "Calls `extractor.can_extract(blob_dict)`. If this returns `False` (blob passed validation at write time but the extractor's criteria have since tightened), log a warning, skip this cache hit, and fall through to Step 3." This directly addresses the stale-blob-after-code-deploy scenario from my R2 write-up. The cache-hit path is now consistent with the fresh-fetch path.

---

**ISSUE-2 (MAJOR): `_get_extractor()` returning `None` unhandled** — RESOLVED.

Both paths now have explicit guards:

- **Cache-hit path** (Section 3.9, line 730): "If this returns `None` (unregistered source_name found on disk, or source package removed between deployments), log a warning and skip this cache hit — fall through to Step 3."
- **Fresh-fetch path** (Section 3.9, pseudocode lines 760-763): `if extractor is None: log.warning(...); continue`.

The `None` case is no longer ambiguous. Both paths skip and continue.

---

**ISSUE-3 (MAJOR): `BlobStore.write(data: str)` vs `raw_blob: bytes` type mismatch** — RESOLVED.

Section 3.7 (line 533) now reads: `async def write(self, source_name: str, match_id: str, data: bytes | str) -> None:`. The docstring (lines 536-537) explains: "Accepts both bytes (from FetchResponse.raw_blob) and str. If bytes, decodes as UTF-8 before writing." The `_atomic_write` static method (line 550) implements: `raw = data.encode("utf-8") if isinstance(data, str) else data`.

The coordinator can now pass `response.raw_blob` (bytes) directly to `blob_store.write()` without an undocumented conversion.

---

**ISSUE-4 (MAJOR): `find_any()` does not respect source priority order** — RESOLVED.

Section 3.7 (line 557) changes the signature to `find_any(self, match_id: str, source_names: list[str])`. The implementation (lines 578-582) iterates `for name in source_names:` and checks `self._data_dir / name / platform / f"{match_id}.json"` for each. The `source_names` parameter is populated from the registry's priority order.

The accompanying documentation (lines 558-566) explains the design rationale: "Iterates in that order instead of filesystem iterdir() order, ensuring the highest-fidelity blob is always preferred when multiple sources have cached data for the same match_id."

This also resolves the security reviewer's MINOR-2 concern (filesystem-discovered directories) since `find_any()` no longer calls `iterdir()` at all.

---

**ISSUE-5 (MAJOR): `try_token()` function never specced** — RESOLVED.

Section 3.5 (lines 390-413) provides the full specification: function signature with keyword-only parameters (`key_prefix`, `limit_per_second`, `limit_long`), docstring, and one-line implementation (`return await acquire_token(...) == 1`). The docstring explicitly notes: "Parameters mirror `acquire_token()` — not `wait_for_token()`. The 'region' parameter on `wait_for_token()` is a legacy compat shim and is NOT replicated here."

However, see NEW-1 below for a consistency issue between the spec and the call site.

---

**ISSUE-6 (MINOR): `WaterfallResult.status` is a plain string** — NOT ADDRESSED (acceptable).

Section 3.9 (line 717) still defines `status: str`. This was a MINOR issue and was not expected to be addressed in R2-to-R3. It can be resolved during implementation by using `Literal[...]` or a small enum. No blocker.

---

**ISSUE-7 (MINOR): `_blob_path()` assumes match_id contains underscore** — NOT ADDRESSED (acceptable).

Section 3.7 (line 510) still uses `match_id.split("_")[0]` without a guard for underscore-less IDs. This was a MINOR issue. The `_validate_platform` regex check at line 506 would catch most malformed results (e.g., a full numeric ID would pass `^[A-Z0-9]+$` only if all uppercase, and a mixed-case ID would fail). Can be resolved during implementation with an explicit check for the underscore separator.

---

### Issues Found

**NEW-1 (MAJOR): `RiotSource.fetch()` call site passes `region=context.region` to `try_token()`, but the `try_token()` specification does not accept a `region` parameter.**

Section 3.5, `RiotSource.fetch()` (lines 359-362):
```python
granted = await try_token(
    self._r,
    limit_per_second=self._cfg.api_rate_limit_per_second,
    region=context.region,
)
```

Section 3.5, `try_token()` specification (lines 393-398):
```python
async def try_token(
    r: aioredis.Redis,
    *,
    key_prefix: str = "ratelimit",
    limit_per_second: int = 20,
    limit_long: int = 100,
) -> bool:
```

The spec explicitly says (lines 406-408): "The 'region' parameter on wait_for_token() is a legacy compat shim and is NOT replicated here."

The call site would raise `TypeError: try_token() got an unexpected keyword argument 'region'` at runtime. This is a direct contradiction introduced when the `try_token()` spec was added (ISSUE-5 fix) without updating the `RiotSource.fetch()` call site to match.

Fix: Either (a) remove `region=context.region` from the call site, or (b) add `region` to `try_token()` if per-region rate limiting is needed for the waterfall path. Given that the existing rate limiter uses `key_prefix` to namespace keys (not `region`), the RiotSource should likely construct the appropriate `key_prefix` from `context.region` and pass that instead. For example: `key_prefix=f"ratelimit:{context.region}"`.

---

**NEW-2 (MINOR): `RiotSource` and `OpggSource` example implementations omit the `required_context_keys` property declared by the Source protocol.**

Section 3.2 (lines 179-188) defines `required_context_keys: frozenset[str]` as a property on the Source protocol. The coordinator checks it before calling `fetch()` (lines 744-747). However:

- `RiotSource` (lines 345-383) does not define `required_context_keys`.
- `OpggSource` (lines 425-446) does not define `required_context_keys`.

Since `Source` is a `@runtime_checkable` Protocol, any `isinstance(source, Source)` check at runtime would pass even without this property (Protocol runtime checks only verify method/attribute existence, and `required_context_keys` as a property would need to be present). More importantly, an implementer following the `RiotSource` example as a template for a new source would not know they need to define this property.

Fix: Add `required_context_keys = frozenset()` as a class attribute on both `RiotSource` and `OpggSource` example implementations. This also serves as documentation for future source authors.

---

**NEW-3 (MINOR): `BlobStore.write()` encodes bytes to UTF-8 before writing — the comment says the opposite.**

Section 3.7, `write()` docstring (lines 536-537): "Accepts both bytes (from FetchResponse.raw_blob) and str. If bytes, decodes as UTF-8 before writing."

Section 3.7, `_atomic_write` implementation (line 550): `raw = data.encode("utf-8") if isinstance(data, str) else data`

The implementation is correct: if `data` is `str`, encode to bytes; if already `bytes`, use directly. But the docstring says "If bytes, decodes as UTF-8" — this is inverted. Bytes are passed through unchanged; strings are encoded. The docstring should read: "If str, encodes as UTF-8 before writing. If bytes, writes directly."

This is a documentation-only issue; the code logic is correct.

---

### Cross-Section Consistency Check

The following cross-references were verified and are consistent:

- Section 3.3 `SourceRegistry.__init__` accepts `extractor_index` and cross-checks `supported_data_types` (addresses AI-specialist MAJOR-2). The coordinator constructs `_extractor_index` in `__init__` (Section 3.9, lines 658-662) and could pass it to the registry.
- Section 3.7 `_blob_path()` uses `path.is_relative_to(self._data_dir)` (line 513), not `startswith()` (addresses security MINOR-1).
- Section 3.7 `write()` tmp naming includes `uuid4().hex` (line 543), preventing same-PID coroutine collisions (addresses FV-R2-1).
- Section 3.9 `MAX_BLOB_SIZE_BYTES` check (lines 753-757) is in the pseudocode, consistent with the prose at line 711 (addresses security MINOR-4).
- Section 4 documents `blob_validation_failed` immediate DLQ routing (lines 815-822), consistent with Section 3.9's `WaterfallResult.blob_validation_failed` field (addresses FV-R2-8).
- Section 3.9 Step 2 cache-hit path (lines 726-734) calls both `_get_extractor()` and `can_extract()`, consistent with the fresh-fetch path pseudocode (lines 760-769).

### Verdict

**APPROVED WITH MINORS**

All seven R2 developer issues are addressed. The five CRITICAL/MAJOR issues (ISSUE-1 through ISSUE-5) are fully resolved in the design text. The two MINOR issues (ISSUE-6, ISSUE-7) were not addressed, which is acceptable — they can be resolved during implementation.

One new MAJOR issue (NEW-1) was introduced by the R2-to-R3 changes: the `RiotSource.fetch()` call site passes a `region` parameter that `try_token()` does not accept. This is a straightforward fix (update the call site to match the spec) and does not require architectural changes. The two new MINOR issues (NEW-2, NEW-3) are documentation gaps in the example implementations.

None of these remaining issues are blockers. The design is ready for implementation once NEW-1 is resolved — either by updating the call site or by a note in the design specifying how `region` maps to `key_prefix`.

---

## Review Round 3 — Formal Verifier

**Reviewer**: formal-verifier
**Round**: 3
**Date**: 2026-03-25

---

### Round 2 Issue Disposition

**FV-R2-1 [MAJOR]: BlobStore write-once TOCTOU — same-PID tmp file collision raises unhandled `FileExistsError`**

**Status: RESOLVED**

The revision notes state: "Changed tmp file naming to `{match_id}.{os.getpid()}.{uuid4().hex}.tmp` to prevent same-PID coroutine collisions via XAUTOCLAIM."

Verified in Section 3.7 (line 543):
```python
tmp = path.with_name(f".tmp_{match_id}_{os.getpid()}_{uuid4().hex}.json")
```

The `uuid4().hex` component is a 32-character random hex string generated per call. Two concurrent coroutines in the same PID will produce distinct tmp file names because each invocation of `uuid4()` returns a cryptographically random UUID (CPython delegates to `os.urandom` via the `uuid` module). The `O_CREAT | O_EXCL` flag in `_atomic_write` (line 548) remains as defense-in-depth: even if two UUID4 values collided (probability ~2^-122), the second `os.open` would fail with `EEXIST` rather than silently corrupting.

Correctness argument for the fix:
- **Uniqueness guarantee**: `uuid4().hex` is unique per call with negligible collision probability. Two coroutines in the same PID, same process, same event loop, processing the same `match_id` (via XAUTOCLAIM redelivery) will produce distinct tmp paths.
- **TOCTOU on `path.exists()`**: The `path.exists()` check at line 540 is still non-atomic with respect to the subsequent `_atomic_write`. Two coroutines can both observe `path.exists() == False` and both proceed to create tmp files. This is now safe: each creates a uniquely-named tmp file, both call `os.replace(tmp, final)`. The second `os.replace` atomically overwrites the first writer's file. Since both writers hold valid blobs for the same `(source, match_id)` (both passed `can_extract()`), the final state is correct regardless of which writer "wins."
- **No unhandled exception**: The `FileExistsError` scenario from R2 is eliminated because tmp file names no longer collide.

The fix is both necessary (eliminates the concrete failure trace from my R2 report) and sufficient (the remaining TOCTOU on `path.exists()` produces a correct final state).

---

**FV-R2-2 [informational]: Crash recovery between `blob_store.write` and `publish` is correct but undocumented**

**Status: RESOLVED (implicitly)**

The R2-to-R3 revision notes do not list this as a separate change, which is expected since it was informational. The crash recovery analysis from R2 remains valid and is confirmed by the updated Section 3.9 text describing the coordinator's step sequence. The design now explicitly enumerates the cache-hit recovery path (Step 2 text at lines 729-734), which implicitly documents the crash-between-blob-write-and-publish recovery.

No action required beyond what was done.

---

**FV-R2-3 [MINOR]: `find_any()` non-deterministic source order with same-namespace match_ids**

**Status: RESOLVED**

The revision notes state: "Changed `find_any(match_id)` to `find_any(match_id, source_names: list[str])`. Iterates in registry priority order instead of `iterdir()`."

Verified in Section 3.7 (lines 557-583):
```python
async def find_any(self, match_id: str, source_names: list[str]) -> tuple[str, dict] | None:
```

The method now accepts `source_names: list[str]` and iterates in the order provided (line 578: `for name in source_names:`). The coordinator passes the registry's priority-ordered list. This eliminates the filesystem-dependent iteration order.

Correctness verification: The priority ordering is preserved end-to-end:
1. `SourceRegistry.__init__` sorts entries by `priority` (line 245: `self._entries = sorted(entries, key=lambda e: e.priority)`).
2. The coordinator must extract source names in sorted order from the registry and pass them to `find_any()`.
3. `find_any()` iterates `source_names` in the given order.

One observation: The design does not show the exact coordinator code that extracts source names and passes them to `find_any()`. The Step 2 text (line 727) says "the coordinator passes the registry's priority-ordered source name list to `find_any(match_id, source_names)`." This is specified at the design level but the pseudocode for Step 2 is prose, not code. The implementer must extract `[e.name for e in self._registry.all_sources]` (or `self._registry.sources_for(data_type)` names) and pass it. Since `all_sources` returns the sorted list (line 278: `return list(self._entries)`), the ordering is guaranteed if the implementer uses `all_sources`. If the implementer accidentally uses `sources_for(data_type)`, the ordering is still correct (the filter preserves the sorted order from `_entries`). Either path is correct.

Verdict: Resolved. The fix is correct and the priority ordering is preserved.

---

**FV-R2-4 [MINOR]: Cache-hit path skips `can_extract()` — stale blobs can cause `ExtractionError`**

**Status: RESOLVED**

The revision notes state (CRITICAL, developer ISSUE-1): "After `find_any()` returns a hit, coordinator now calls `extractor.can_extract(blob_dict)` before using it. If False, skips cache hit and falls through to network fetch path."

Verified in Section 3.9, Step 2 (lines 729-734):

> If a blob is found, `find_any()` returns `(source_name, blob_dict)`. The coordinator then:
> 1. Calls `_get_extractor(source_name, data_type)`. If this returns `None` [...], log a warning and skip this cache hit -- fall through to Step 3.
> 2. Calls `extractor.can_extract(blob_dict)`. If this returns `False` [...], log a warning, skip this cache hit, and fall through to Step 3.
> 3. Only if both checks pass, calls `extractor.extract(blob_dict, ...)` and returns the result.

This is the correct fix. The cache-hit path is now consistent with the fresh-fetch path: both require `_get_extractor() is not None` AND `can_extract() == True` before extraction proceeds.

New failure mode analysis for the cache-hit `can_extract()` addition:

The cache-hit path now has a code path where `can_extract()` returns False for a blob that was previously persisted (because the extractor's validation logic tightened between deployments). In this case, the coordinator skips the cache hit and falls through to Step 3 (network fetch). Step 3 will try to fetch the same match from the same source. If the source returns the same blob shape, `can_extract()` will again return False, and the coordinator will set `blob_validation_failed=True` and continue to the next source.

The question is: does the stale blob on disk get cleaned up? The design says "log a warning, skip this cache hit, and fall through to Step 3." It does NOT say "delete the stale blob." This means on every future redelivery of this match_id, the coordinator will:
1. Find the stale blob via `find_any()`.
2. Call `can_extract()` which returns False.
3. Skip it and fall through to the network fetch.
4. The network fetch may succeed from a different source, or may also fail.

This is not an infinite loop (the stale blob is skipped, not retried), so it is functionally correct. However, the stale blob permanently occupies disk space and adds a constant overhead of one `read_bytes()` + one `json.loads()` + one `can_extract()` call on every cache-hit check for this match_id. Since BlobStore has no automatic eviction, the stale blob persists indefinitely. This is a performance concern, not a correctness concern. See MINOR-1 below.

---

**FV-R2-5 [informational]: `max(hints)` for `retry_after_ms` is confirmed correct**

**Status: No action required.** Informational finding confirmed in R2, no change needed.

---

**FV-R2-6 [informational]: `try_token()` false-negative fallthrough is safe**

**Status: No action required.** Informational finding confirmed in R2, no change needed.

---

**FV-R2-7 [informational]: `find_any()` cannot return partial blob under concurrent writes**

**Status: No action required.** Informational finding confirmed in R2, no change needed.

---

**FV-R2-8 [MAJOR]: `blob_validation_failed` messages exhaust all retries against unchanging bad source**

**Status: RESOLVED**

The revision notes state: "When `blob_validation_failed=True` in WaterfallResult, Fetcher routes immediately to DLQ (skips retry loop). Documented in Section 4 Fetcher flow."

Verified in Section 4 (lines 815-822):

> ```
> _fetch_match()
>   [...]
>   │   ├─ all_exhausted + blob_validation_failed → nack_to_dlq immediately
>   │   │     (skip retry loop — the blob is structurally bad, retrying
>   │   │      will hit the same un-extractable data from the same source)
>   │   └─ all_exhausted (no blob_validation) → nack_to_dlq(retry_after_ms=result.retry_after_ms)
> ```

And in the supplementary text (line 822):

> When `WaterfallResult.blob_validation_failed` is True and `status` is `all_exhausted`, the Fetcher routes the message directly to DLQ with `max_attempts=1` (or equivalently, sets `attempts=max_attempts` on the envelope). This prevents the message from burning all `max_attempts` retry cycles against the same structurally bad blob.

This is the correct fix. Analysis of the specified behavior:

1. The Fetcher receives `WaterfallResult(status="all_exhausted", blob_validation_failed=True)`.
2. The Fetcher calls `nack_to_dlq` with `max_attempts=1` (or sets `attempts=max_attempts`).
3. Recovery picks up the DLQ entry and checks `attempts >= max_attempts`.
4. Since the condition is met immediately, Recovery archives the message to `stream:dlq:archive` without replaying it.

This eliminates the retry-exhaustion waste from my R2 report. The message goes directly to archive after a single DLQ entry, rather than cycling through 5 retry rounds against the same bad blob.

One subtlety: the design says "with `max_attempts=1` (or equivalently, sets `attempts=max_attempts` on the envelope)." These are not quite equivalent in implementation. Setting `max_attempts=1` on the `nack_to_dlq` call means the DLQ envelope declares a lower bound. Setting `attempts=max_attempts` on the envelope means artificially inflating the attempt counter. The second approach is cleaner because it uses the existing `max_attempts` from the config without introducing a new parameter to `nack_to_dlq`. However, both produce the same behavior: Recovery archives on first encounter. The "or equivalently" phrasing leaves implementation flexibility, which is acceptable at the design level.

Residual concern: The `blob_validation_failed` flag is set when ANY source's blob fails `can_extract()`, even if other sources were merely THROTTLED (not producing bad blobs). Consider:
- Source A returns SUCCESS with a blob that fails `can_extract()`. `any_blob_validation_failed = True`.
- Source B returns THROTTLED (no blob, no validation).
- Coordinator returns `all_exhausted` with `blob_validation_failed=True`.
- Fetcher routes immediately to DLQ archive.

But Source B was only temporarily throttled. On retry, Source B might succeed with a valid blob. The immediate-archive behavior prevents this recovery path.

This is a design tradeoff, not a bug. The rationale in the design text ("the blob is structurally bad, retrying will hit the same un-extractable data from the same source") assumes the bad blob will be encountered again on retry. This is true for the fresh-fetch path (Source A will likely return the same bad blob), but the retry might succeed via Source B. The design accepts this tradeoff: it prioritizes avoiding wasted retry cycles over the possibility of a different source succeeding on retry.

Severity: This residual concern is MINOR — it sacrifices one recovery opportunity (Source B succeeding on retry) to avoid burning 5 retry cycles. The message is archived with a clear `blob_validation_failed` flag for operator debugging. If Source B was the only viable path, the operator can manually requeue the archived message after investigating why Source A's blob is bad.

---

### Issues Found

**MINOR-1: Stale cache blob causes permanent per-request overhead when `can_extract()` validation tightens between deployments**

Section 3.9 Step 2 (lines 729-734) specifies that when a cached blob fails the newly-added `can_extract()` check, the coordinator logs a warning, skips the cache hit, and falls through to Step 3. The stale blob is NOT deleted from disk.

On every subsequent delivery of this match_id (including redeliveries via DLQ/XAUTOCLAIM), the coordinator will:
1. Call `find_any()` which reads the stale blob from disk (one `read_bytes()` + `json.loads()`).
2. Call `can_extract()` which returns False.
3. Skip and fall through to Step 3.

This is functionally correct — the stale blob does not block progress. However, it introduces unnecessary I/O on every access for this match_id. For a single stale blob this is negligible. If an extractor update invalidates a large batch of previously-valid blobs (e.g., hundreds), the cumulative overhead on reprocessing would be noticeable but still bounded.

The fix is straightforward: when `can_extract()` returns False on the cache-hit path, delete the stale blob file (`blob_path.unlink(missing_ok=True)`). This is safe because the blob has been confirmed to be non-extractable by the current extractor version.

Severity: MINOR. The current behavior is correct and the overhead is bounded. Deletion of stale blobs is an optimization.

---

**MINOR-2: `find_any()` performs `json.loads()` on a disk blob before `can_extract()` — a malformed JSON file causes an unhandled exception**

Section 3.7, `find_any()` (lines 580-582):
```python
if blob_path.exists():
    data = await asyncio.to_thread(blob_path.read_bytes)
    return (name, json.loads(data))
```

`json.loads(data)` is called inside `find_any()` before the coordinator has a chance to call `can_extract()`. If the blob file on disk contains malformed JSON (due to a partial write from a previous crash — theoretically impossible given the tmpfile-fsync-rename pattern, but possible if the file was manually edited or corrupted by a disk error), `json.loads` raises `json.JSONDecodeError`. This exception propagates from `find_any()` through the coordinator to `_handle_with_retry` in `service.py`, which increments the retry counter and eventually sends the message to DLQ.

The coordinator never reaches Step 3 (network fetch) because the exception occurs in Step 2. A valid network fetch that would succeed is prevented by a corrupt cache file.

The fix is to catch `json.JSONDecodeError` inside `find_any()` (or in the coordinator's Step 2 handler) and treat it as a cache miss — log a warning and continue to the next source in the `source_names` list, or fall through to Step 3.

Severity: MINOR. The tmpfile-fsync-rename pattern makes this scenario extremely unlikely during normal operation. The risk comes from manual intervention or disk corruption. However, defensive coding here is cheap.

---

**MINOR-3: `retry_after_ms` from a SUCCESS-but-failed-`can_extract()` source is not collected**

Section 3.9 pseudocode (lines 752-772):
```python
if response.result == FetchResult.SUCCESS:
    # ... blob size guard, json.loads, extractor lookup, can_extract ...
    if not extractor.can_extract(blob_dict):
        any_blob_validation_failed = True
        continue  # <-- jumps back to for loop, skipping retry_hints collection

if response.retry_after_ms is not None:
    retry_hints.append(response.retry_after_ms)
```

When a source returns `FetchResult.SUCCESS` and the blob fails `can_extract()`, the `continue` statement at line 768 jumps back to the top of the for loop. The `retry_after_ms` collection at line 771-772 is skipped. This is correct behavior for SUCCESS responses — a successful fetch has no retry-after hint. However, the `FetchResponse` from a SUCCESS might still carry `retry_after_ms` from rate-limit headers (e.g., "this request succeeded, but your next one should wait 2s"). If a source returns SUCCESS with `retry_after_ms=2000` and a bad blob, the hint is lost.

In practice, sources that return SUCCESS typically set `retry_after_ms=None`. The `retry_after_ms` field is primarily useful for THROTTLED responses. This is a theoretical gap, not a practical one.

Severity: MINOR. No fix needed unless a source implementation is added that returns SUCCESS with a non-None `retry_after_ms`.

---

**MINOR-4: `blob_validation_failed` immediate DLQ routing interacts subtly with the `can_extract()` cache-hit skip**

Consider this execution trace:
1. Match M is first processed. Source A returns SUCCESS, blob passes `can_extract()`, blob is persisted. Extraction succeeds. All is well.
2. Extractor code is updated (new deployment). The blob shape from Source A no longer passes `can_extract()`.
3. Match M is redelivered (via DLQ retry for an unrelated reason, or XAUTOCLAIM after a crash).
4. Step 1 (RawStore check): If RawStore still has the data (within TTL), returns "cached" — no problem. But if TTL has expired, the coordinator proceeds.
5. Step 2 (BlobStore check): `find_any()` returns the cached blob. `can_extract()` returns False. Coordinator skips the cache hit, falls through to Step 3.
6. Step 3 (network fetch): Source A returns SUCCESS with the same blob shape. `can_extract()` returns False. `any_blob_validation_failed = True`. Source B is THROTTLED. All exhausted.
7. Fetcher sees `all_exhausted + blob_validation_failed=True`. Routes immediately to DLQ archive.

In this scenario, the match was previously successfully processed (step 1), but after an extractor update and RawStore TTL expiry, the match is archived as a blob-validation failure. This is an edge case where a code update retroactively "breaks" a match that was already processed.

The mitigation is that Step 1 (RawStore check) serves as the primary idempotency gate. If the match was fully processed (RawStore written, stream:parse published, ACKed), it will not be redelivered unless something unusual happens (manual DLQ replay, XAUTOCLAIM after very long idle). And if RawStore TTL has not expired, Step 1 catches it.

Severity: MINOR. This requires a specific sequence: extractor update + RawStore TTL expiry + redelivery of a previously-processed match. Unlikely in practice.

---

### Cross-cutting Verification: Crash Recovery at Every Step

I re-verified the crash recovery properties for all crash points in the revised R3 algorithm, incorporating the new `can_extract()` cache-hit check and `blob_validation_failed` immediate DLQ routing.

**State machine for a single message through the coordinator:**

```
States:
  S0: Message received from stream:match_id (in PEL, not ACKed)
  S1: RawStore check — exists?
  S2: BlobStore find_any() — cache hit?
  S2a: can_extract() on cached blob
  S3: Source waterfall iteration (per-source)
  S3a: source.fetch() called
  S3b: can_extract() on fresh blob
  S3c: blob_store.write() completed
  S3d: extractor.extract() completed
  S3e: raw_store.set() completed
  S4: publish(stream:parse) completed
  S5: ack(stream:match_id) completed (terminal: success)
  DLQ: nack_to_dlq completed (terminal: DLQ)
  ARCHIVE: DLQ archive (terminal: archived)
```

**Crash at S0**: Message is in PEL. XAUTOCLAIM will redeliver. No state change. **Invariants preserved.**

**Crash at S1**: No writes performed. Redeliver repeats S1. **Invariants preserved.**

**Crash at S2/S2a**: `find_any()` is read-only (disk stat + read_bytes). No state change. Redeliver repeats S2. **Invariants preserved.**

**Crash at S3a**: Source may or may not have received the HTTP request. If the source consumed a rate-limit token before the crash, the token is spent but the response is lost. On redeliver, a new token is consumed. This is at-most-one-wasted-token per crash, bounded by the retry counter. **Invariants preserved** (rate limit tokens are consumed but not over-counted; the Lua script is atomic).

**Crash at S3b** (`can_extract()` on fresh blob): No blob persisted yet (persistence happens after validation). Redeliver repeats the fetch. **Invariants preserved.**

**Crash at S3c** (`blob_store.write()` during `_atomic_write`):
- If crash occurs during `os.write()` or `os.fsync()`: tmp file may exist with partial data. Final path does not exist. On redeliver, `path.exists()` returns False, a new tmp file (with a different UUID4) is created, and `os.replace()` succeeds. The orphaned partial tmp file remains on disk until manual cleanup.
- If crash occurs after `os.replace()`: blob is fully persisted at the final path. On redeliver, Step 2 finds the blob via `find_any()`. If extraction succeeds, processing continues. If the crash was between S3c and S3d, the RawStore does not have the extracted data, so `find_any()` is the recovery path — correct.
- **Invariant: no partial blob at the final path** — holds because `os.replace()` is atomic on POSIX.
- **Invariant: no lost message** — holds because the message is still in PEL.
- **Invariants preserved.**

**Crash at S3d** (extraction): Blob is on disk. RawStore not yet written. On redeliver, `raw_store.exists()` returns False (S1 falls through), `find_any()` returns the cached blob (S2 succeeds), extraction runs again. **Invariants preserved.**

**Crash at S3e** (`raw_store.set()`):
- If Redis SET NX succeeds but disk write has not happened: Redis has the data but disk does not. The `RawStore.set()` implementation (raw_store.py:158-178) handles this: if the disk write fails after Redis SET NX, it deletes the Redis key so the next attempt can retry both. On redeliver, the coordinator re-enters at S1, `raw_store.exists()` may return True (if Redis key was not deleted) or False (if it was). Either way, the message eventually reaches S4. **Invariants preserved.**
- If both Redis and disk succeed: proceed to S4 on redeliver. **Invariants preserved.**

**Crash at S4** (`publish(stream:parse)` before ACK): RawStore has the data. On redeliver, S1 returns True ("cached" path), Fetcher calls `_publish_and_ack()` which publishes to stream:parse and ACKs. At-least-once delivery to stream:parse. **Invariants preserved.**

**Crash at S5** (ACK): The ACK may or may not have completed. If it did not, the message is redelivered. S1 returns True, Fetcher publishes again (idempotent: parser handles duplicate match_ids). If it did complete, no further action needed. **Invariants preserved.**

**Crash at DLQ path** (nack_to_dlq): The `_nack_with_fallback` function in service.py (lines 100-122) handles this: if `nack_to_dlq` fails, emergency-ACK prevents PEL loops. The message may be lost in this extreme edge case (nack fails AND emergency ACK fails). This is a pre-existing property of the pipeline's failure handling, not introduced by the waterfall. **Pre-existing behavior, not a regression.**

---

### Cross-cutting Verification: `find_any()` Priority Ordering End-to-End

The coordinator must pass the correct ordered list to `find_any()`. Tracing the data flow:

1. `SourceRegistry.__init__` sorts entries by `priority` (ascending) into `self._entries`.
2. `SourceRegistry.all_sources` returns `list(self._entries)` — preserves sorted order.
3. The coordinator (Section 3.9) must call `find_any(match_id, [e.name for e in self._registry.all_sources])` or equivalent.
4. `find_any()` iterates `source_names` in the given order.

The design does not show a `SourceRegistry` method that returns just the source names in order. The implementer must extract names from `all_sources`. This is trivial but undocumented. Not a correctness issue.

One edge case: `find_any()` uses `all_sources` names (all registered sources), while Step 3 uses `sources_for(data_type)` (sources filtered by data_type support). If a source does NOT support the requested data_type but has a cached blob for the match_id, `find_any()` will return that blob. The coordinator then calls `_get_extractor(source_name, data_type)`, which returns `None` (because the source does not support this data_type). The coordinator logs a warning and skips. This is correct behavior — no data quality issue, no crash.

Alternatively, the coordinator could pass `[e.name for e in self._registry.sources_for(data_type)]` to `find_any()`, which would skip sources that do not support the requested data_type. This is more efficient (fewer stat calls) and semantically cleaner. The design does not specify which list is passed; either is correct.

---

### Atomicity Assessment of R3 Changes

No new atomicity gaps were introduced by the R2-to-R3 changes. The changes are:

1. **`uuid4().hex` in tmp file name** — pure local computation, no shared state.
2. **`can_extract()` on cache-hit path** — pure function call on a local dict, no shared state.
3. **`find_any(match_id, source_names)` parameter change** — iteration order change, no new shared state.
4. **`blob_validation_failed` immediate DLQ routing** — uses existing `nack_to_dlq` path, no new atomicity requirements.
5. **`required_context_keys` check** — pure set comparison on local data, no shared state.
6. **`SourceRegistry` startup cross-check** — runs once at startup, no concurrency.
7. **`MAX_BLOB_SIZE_BYTES` check** — pure comparison on local bytes, no shared state.
8. **`BlobStore.write(data: bytes | str)` type change** — encoding logic is local, no shared state.
9. **`is_relative_to()` replacing `startswith()`** — local path comparison, no shared state.

None of these changes involve multi-key Redis operations, new shared mutable state, or new concurrent access patterns. The atomicity profile of the design is unchanged from R2.

---

### Summary Table

| ID | Severity | Title | Status |
|----|----------|-------|--------|
| FV-R2-1 | MAJOR | BlobStore tmp file collision | RESOLVED — uuid4().hex eliminates same-PID collisions |
| FV-R2-2 | informational | Crash recovery documentation | RESOLVED — implicitly documented in Step 2 prose |
| FV-R2-3 | MINOR | find_any() non-deterministic order | RESOLVED — source_names parameter enforces priority order |
| FV-R2-4 | MINOR | Cache-hit path skips can_extract() | RESOLVED — Step 2 now calls can_extract() before extract() |
| FV-R2-5 | informational | max(hints) correctness | No action needed |
| FV-R2-6 | informational | try_token() false-negative safety | No action needed |
| FV-R2-7 | informational | find_any() partial blob safety | No action needed |
| FV-R2-8 | MAJOR | blob_validation_failed retry exhaustion | RESOLVED — immediate DLQ routing skips retry loop |
| FV-R3-1 | MINOR | Stale cache blob not deleted when can_extract() fails | NEW |
| FV-R3-2 | MINOR | json.JSONDecodeError in find_any() propagates as unhandled exception | NEW |
| FV-R3-3 | MINOR | retry_after_ms from SUCCESS + failed can_extract() is not collected | NEW |
| FV-R3-4 | MINOR | blob_validation_failed + THROTTLED interaction archives prematurely | NEW (residual from FV-R2-8 fix) |

---

### Verdict

**APPROVED**

All eight R2 formal-verifier issues are resolved. The two MAJOR issues (FV-R2-1 and FV-R2-8) have correct, sufficient fixes:

- **FV-R2-1**: The `uuid4().hex` component in the tmp file name eliminates same-PID coroutine collisions. The `O_CREAT | O_EXCL` flag provides defense-in-depth. The remaining TOCTOU on `path.exists()` produces a correct final state (idempotent overwrite of identical content via `os.replace`).

- **FV-R2-8**: Immediate DLQ routing with `max_attempts=1` when `blob_validation_failed=True` prevents wasted retry cycles. The DLQ archive entry preserves the flag for operator debugging.

The four new MINOR issues (FV-R3-1 through FV-R3-4) are defensive hardening suggestions, not correctness violations. None can cause message loss, data corruption, or invariant violations.

The core correctness properties hold:
1. **No message loss**: Every message is either ACKed (after successful processing) or archived (after DLQ exhaustion). Crash at any point preserves the message in the PEL for redelivery.
2. **Idempotent writes**: RawStore SET NX ensures first-writer-wins for Redis. BlobStore write-once semantics (exists check + atomic rename) ensure no torn writes. Reprocessing the same message produces the same final state.
3. **Bounded retries**: `blob_validation_failed` messages are immediately archived. Normal failures are bounded by `max_attempts`. No infinite loops.
4. **Atomic blob persistence**: tmpfile-fsync-rename pattern ensures no partial blob at the final path. `uuid4().hex` in tmp names prevents coroutine collisions.
5. **Priority-preserving cache lookup**: `find_any()` iterates source names in registry priority order. The highest-fidelity cached blob is always preferred.

---

## Review Round 3 -- Optimizer

**Reviewer**: optimizer
**Round**: 3
**Date**: 2026-03-25

### Round 2 Issue Disposition

**MINOR-1 (sources_for() allocates per call on immutable result)** -- NOT ADDRESSED, remains acceptable.

`SourceRegistry.sources_for()` (Section 3.3, lines 262-271) still performs an O(S) list comprehension on every call. The revision notes table does not list this issue, and the code is unchanged. As stated in the R2 review, this is negligible at S=2-5 and 20 calls/s. The `_extractor_index` pre-computation (Section 3.9, lines 658-662) handles the higher-fanout lookup. The `SourceRegistry.__init__()` was modified to accept `extractor_index` for the startup cross-check (ai-specialist MAJOR-2 fix), which would have been a natural place to also add `self._by_type`, but this was not done. No performance concern at current or foreseeable scale. This issue can be closed as "accepted, not worth the complexity."

**MINOR-2 (find_any() iterdir() non-deterministic order)** -- RESOLVED.

The revision notes list this as "MAJOR (5 reviewers) -- find_any() non-deterministic order" (line 1275). The fix is correct: `find_any()` now accepts `source_names: list[str]` (Section 3.7, line 557) and iterates in priority order instead of using `Path.iterdir()`. The implementation at lines 578-583 does a simple `for name in source_names` loop, constructing a deterministic path `self._data_dir / name / platform / f"{match_id}.json"` per source and calling `blob_path.exists()` on each. This is O(S) stat calls where S = len(source_names), exactly as recommended. No filesystem iteration, no non-determinism. The first hit returns immediately (short-circuit), so the average case is O(1) when the highest-priority source has the blob. Resolved with the exact approach suggested in R2.

**MINOR-3 (FetchContext.extra dict allocated per instantiation even when unused)** -- NOT ADDRESSED, remains acceptable.

The R2 review explicitly stated "No fix needed." The `extra: dict = field(default_factory=dict)` pattern (Section 3.2, line 138) is standard Python. At 20 messages/s this is ~1.3 KB/s of short-lived heap. The R3 revision added `required_context_keys` to the Source protocol (Section 3.2, lines 178-188), which reads from `context.extra.keys()` in the coordinator loop (line 745). This means `extra` is now actively used on every iteration of the waterfall loop (for the set difference check), making the allocation even more justified. Closed as designed.

### New Checks for R2-to-R3 Changes

**1. `can_extract()` on the cache-hit path (developer CRITICAL, formal-verifier FV-R2-4)**

Section 3.9, lines 729-734. The coordinator now calls `extractor.can_extract(blob_dict)` on the cache-hit path (Step 2, item 2) before using the blob. This adds one function call to the cache-hit hot path. `can_extract()` is a synchronous method on the Extractor protocol (Section 3.4, lines 313-322) that inspects a few top-level keys of the blob dict (e.g., checking for the presence of `"info"`, `"metadata"`, etc.). This is O(1) -- a small constant number of dict key lookups. On a CPython dict with ~50-100 keys (typical for a match-v5 blob), `"key" in blob` is O(1) average case via hash table lookup. At 20 messages/s with cache hits, this adds nanoseconds per call. No performance concern.

The change also means that if `can_extract()` returns False on a cached blob, the coordinator falls through to Step 3 (network fetch). This is the correct behavior: pay one network round-trip to get a fresh blob rather than repeatedly failing on a stale cached blob. The alternative -- an infinite extraction-failure loop -- would be infinitely more expensive. The added check is a net performance improvement in the degraded case.

**2. `required_context_keys` check before each `fetch()` call (ai-specialist MAJOR-1)**

Section 3.9, lines 744-748. Before each `entry.source.fetch()` call, the coordinator computes:

```python
missing_keys = entry.source.required_context_keys - set(context.extra.keys())
```

This is a set difference operation. `entry.source.required_context_keys` is a frozenset (declared in the Source protocol, Section 3.2, line 179). `set(context.extra.keys())` constructs a set from the dict's keys view. The set difference is O(min(|R|, |E|)) where R = number of required keys and E = number of extra keys. For current sources: R=0 (both Riot and op.gg use core fields only, not `extra`), so the frozenset is empty and the set difference is O(1) -- it short-circuits immediately. Even for a hypothetical future source with R=3 and E=5, this is a handful of hash lookups.

However, `set(context.extra.keys())` allocates a new set object on every iteration of the source loop. At S=2-5 sources per fetch and 20 fetches/s, that is 40-100 set allocations per second. Each is an empty set (since `extra` is empty for current sources), ~216 bytes on CPython. Total: ~20 KB/s of transient allocations. Negligible, but the allocation could be hoisted outside the loop since `context` does not change between source iterations:

```python
extra_keys = set(context.extra.keys())  # once, before the loop
for entry in ...:
    missing_keys = entry.source.required_context_keys - extra_keys
```

This is a NIT-level observation. The current code is correct and the overhead is unmeasurable.

**3. `MAX_BLOB_SIZE_BYTES` check before `json.loads()` (security MINOR-4)**

Section 3.9, lines 753-757. The coordinator checks `len(response.raw_blob) > MAX_BLOB_SIZE_BYTES` before parsing. `len()` on a `bytes` object in CPython is O(1) -- it reads the `ob_size` field of the `PyBytesObject` struct. This is a single pointer dereference. The check prevents an O(N) `json.loads()` call on an N-byte oversized blob from ever executing, where N could be up to whatever the HTTP client accepts. This is a pure performance improvement for the adversarial case (oversized response) with zero cost on the normal path. Correct.

**4. Startup cross-check in `SourceRegistry.__init__()` (ai-specialist MAJOR-2)**

Section 3.3, lines 247-260. The cross-check iterates all entries and their supported_data_types, checking membership in `known_data_types`:

```python
known_data_types = {dt for (_, dt) in extractor_index}  # O(E) where E = extractor entries
for entry in self._entries:                              # O(S) sources
    for dt in entry.source.supported_data_types:         # O(D) data types per source
        if dt not in known_data_types:                   # O(1) set lookup
            raise ValueError(...)
```

Total complexity: O(E + S*D) where E = number of extractor registrations, S = number of sources, D = data types per source. At E=4, S=2, D=2, this is ~10 operations at startup. Runs exactly once. Zero hot-path impact. Correct.

The set comprehension `{dt for (_, dt) in extractor_index}` extracts only the DataType values from the `(source_name, DataType)` tuple keys, discarding the source_name. This means the check validates that the DataType string exists as a value somewhere in the extractor index, but does not validate that the specific `(source.name, dt)` pair exists. For example, if source "riot" declares `supported_data_types = frozenset({"match"})` and the only extractor registered is `("opgg", "match")`, the cross-check passes because "match" is in `known_data_types`, even though there is no extractor for `("riot", "match")`. This is a false negative in the validation -- but it is caught at runtime by `_get_extractor(entry.name, data_type)` returning `None` in the coordinator's fresh-fetch path (line 760), which logs a warning and continues to the next source. The startup check catches the most common error (typos in DataType strings); the runtime check catches the rarer case (correct DataType but wrong source pairing). Together they provide adequate coverage. See NIT-1 below.

**5. `find_any()` revised implementation -- filesystem I/O analysis**

Section 3.7, lines 571-583. The revised `find_any()` performs:
- One `self._data_dir.exists()` call -- O(1) stat
- One `match_id.split("_")[0]` -- O(K) where K = length of match_id, but K < 30 always
- One `_validate_platform()` -- O(1) regex match on a short string
- Per source name: one `blob_path.exists()` -- O(1) stat per source

On a miss (the dominant case for new matches), all S stat calls return False. On Linux/ext4, `stat()` on a non-existent file in an existing directory is a single directory lookup -- O(1) with dentry cache hit (hot), O(log N) with cold cache where N = number of entries in the parent directory. Since each `{source}/{platform}/` directory contains individual match files, and the Fetcher processes matches for a small number of platforms (1-3), the directory entries are modest (hundreds to low thousands of files). With warm dentry cache (the normal case for a Fetcher that has been running), each `stat()` completes in microseconds.

The `asyncio.to_thread(blob_path.read_bytes)` call on a hit deserializes the blob in the thread pool. This is O(B) where B = blob size (15-50 KB). The `json.loads(data)` is also O(B). Total cost on a hit: one disk read + one JSON parse, both proportional to blob size. This is unavoidable and efficient.

No performance regression from the R2 design. The change from `iterdir()` to explicit name iteration eliminates an unnecessary directory listing syscall and makes the cost strictly O(S) stat calls.

### Issues Found

**NIT-1: Startup cross-check validates DataType existence globally, not per-source**

- **Section**: 3.3, `SourceRegistry.__init__()` (lines 247-260)
- **Current behavior**: `known_data_types = {dt for (_, dt) in extractor_index}` collects all DataType values across all extractors. A source declaring `supported_data_types = frozenset({"match"})` passes validation as long as any extractor (for any source) handles "match".
- **Correct check**: Validate that `(entry.name, dt)` exists as a key in `extractor_index`, not just that `dt` exists as a value.
- **Impact**: At S=2-5 sources with 1-2 data types each, the chance of a false-positive passing validation is low. The runtime `_get_extractor()` check catches any actual mismatch. This is a correctness observation, not a performance issue.
- **Fix**: Replace `if dt not in known_data_types` with `if (entry.name, dt) not in extractor_index`. Same O(1) lookup cost (dict membership check), more precise validation.
- **Priority**: NIT. The runtime fallback is safe. The startup check could be more precise for free.

**NIT-2: `set(context.extra.keys())` allocated inside the source iteration loop**

- **Section**: 3.9, lines 744-748 (coordinator waterfall loop)
- **Current behavior**: `set(context.extra.keys())` is evaluated on each iteration of `for entry in self._registry.sources_for(data_type)`.
- **Impact**: S allocations per fetch (S=2-5), 20 fetches/s, ~100 allocations/s of empty sets. Approximately 20 KB/s of transient heap.
- **Fix**: Hoist `extra_keys = set(context.extra.keys())` before the loop.
- **Priority**: NIT. Unmeasurable overhead at current scale.

### Performance Assessment

The R2-to-R3 revision introduces four new checks on the coordinator's hot path:

| Check | Location | Cost | Frequency |
|-------|----------|------|-----------|
| `can_extract()` on cache hit | Step 2, line 731 | O(1) dict key lookups | Per cache hit |
| `required_context_keys` set difference | Step 3 loop, line 745 | O(1) for empty frozenset | Per source per fetch |
| `MAX_BLOB_SIZE_BYTES` len check | Step 3 loop, line 754 | O(1) `len()` on bytes | Per successful fetch |
| Startup cross-check | `SourceRegistry.__init__`, line 251 | O(E + S*D), once | Startup only |

None of these add meaningful overhead. The `can_extract()` check on the cache-hit path is the most impactful change in terms of hot-path cost, and it is O(1) with constant factors measured in nanoseconds.

The `find_any()` change from `iterdir()` to explicit `source_names` iteration is a strict improvement: it eliminates a directory listing syscall and makes the access pattern fully deterministic. The O(S) stat-call cost is unchanged; the constant factor is slightly better.

The overall performance profile of the design is unchanged from R2:

1. **Bottleneck**: Riot API rate limit (20 req/s, 100 req/2min). No computation in the waterfall is within orders of magnitude of this constraint.
2. **BlobStore find_any()**: O(S) stat calls, S=2-5. Microseconds per call.
3. **Coordinator loop**: O(S) sources, O(1) extractor lookup, O(B) JSON parse (once per fetch). B = 15-50 KB.
4. **try_token()**: O(1) Redis round-trip, ~0.5ms.
5. **Disk writes**: O(B) atomic write per successful fetch. No concurrent-append contention.
6. **Memory**: 300-500 KB per in-flight fetch (two dicts), 6-10 MB at 20 concurrent fetches.

No performance regressions were introduced by the R2-to-R3 changes.

### Verdict

**APPROVED**

All three R2 optimizer issues are resolved or accepted:

- MINOR-1 (sources_for pre-computation): accepted as negligible, consistent with R2 assessment.
- MINOR-2 (find_any non-deterministic order): resolved with the exact approach recommended.
- MINOR-3 (FetchContext.extra allocation): accepted as designed, now validated by active use via `required_context_keys`.

The four new checks added in the R2-to-R3 revision (cache-hit `can_extract()`, `required_context_keys` validation, `MAX_BLOB_SIZE_BYTES` guard, startup cross-check) all have O(1) or startup-only cost. None introduce performance regressions. Two NITs identified (startup cross-check precision, set allocation hoisting) are below the threshold for revision and can be addressed during implementation.

---

## Review Round 3 — Security

**Reviewer**: security
**Round**: 3
**Date**: 2026-03-25

### Round 2 Issue Disposition

**MINOR-1 (_blob_path() startswith prefix collision) -- RESOLVED.**

The revision notes state: "Replaced `str(path).startswith(str(self._data_dir))` with `path.is_relative_to(self._data_dir)`." Verified in Section 3.7, `_blob_path()` (line 513): the code now reads `if not path.is_relative_to(self._data_dir)`. This is the correct fix. `Path.is_relative_to()` (available since Python 3.9, project targets 3.12+) performs a proper path component comparison, not a string prefix check. The edge case where `BLOB_DATA_DIR=/data/blob` would incorrectly match `/data/blob-escape/evil.json` is eliminated. The path is also `.resolve()`d before the check (line 512), which canonicalizes symlinks and relative segments. No further action needed.

**MINOR-2 (find_any() reads blobs from filesystem-discovered directories without validating source_name) -- RESOLVED.**

The revision notes list: "Changed `find_any(match_id)` to `find_any(match_id, source_names: list[str])`. Iterates in registry priority order instead of `iterdir()`." Verified in Section 3.7 (lines 557-583): `find_any()` now accepts a `source_names: list[str]` parameter and iterates over that list (line 578: `for name in source_names`) rather than calling `self._data_dir.iterdir()`. This fully closes the rogue-directory attack surface. An attacker-planted directory under `BLOB_DATA_DIR` with a name not in the registry's source list will never be iterated. Only directories whose names match registered sources are checked. The coordinator passes the registry's priority-ordered list (Section 3.9, line 727), so the source names are always controlled by application configuration, not by filesystem contents.

**MINOR-3 (/player/refresh endpoint missing region validation) -- ACCEPTED AS OUT OF SCOPE.**

The revision notes state: "Out of scope for design doc -- tracked as code fix in the UI service." This is acceptable. The region validation gap is a pre-existing issue in `lol-pipeline-ui/src/lol_ui/routes/stats.py` that predates the waterfall design and is not introduced or worsened by it. The design document is not the correct venue for specifying UI service code fixes. The fix remains a one-line addition (validate `region` against `_REGIONS_SET` in `player_refresh`, matching the existing `show_stats` pattern). The important thing is that it is tracked. No objection.

**MINOR-4 (blob size not bounded before persistence or extraction) -- RESOLVED.**

The revision notes state: "Added `MAX_BLOB_SIZE_BYTES = 2 * 1024 * 1024` (2 MB) check after receiving `response.raw_blob`. Oversized responses treated as UNAVAILABLE." Verified in two locations:

1. Section 3.9, prose paragraph (line 711): "A `MAX_BLOB_SIZE_BYTES: int = 2 * 1024 * 1024` (2 MB) constant is checked in the coordinator immediately after receiving `response.raw_blob`. If `len(response.raw_blob) > MAX_BLOB_SIZE_BYTES`, the response is treated as a fetch failure."

2. Section 3.9, coordinator pseudocode (lines 753-757): The size check occurs inside the `if response.result == FetchResult.SUCCESS` block, *before* `json.loads(response.raw_blob)` on line 759. This is the correct placement -- the size guard rejects oversized responses before any parsing, preventing the `json.loads()` memory amplification that was the primary concern.

The 2 MB threshold is proportionate: Riot match-v5 responses are 15-50 KB, op.gg responses are similarly sized, and a response exceeding 1 MB is anomalous. The 2 MB bound provides a 40x safety margin over the largest expected legitimate response. Oversized responses are treated as `UNAVAILABLE` (not `blob_validation_failed`), which is semantically correct -- the blob was never parsed or validated, it was rejected at the transport layer. No further action needed.

### Issues Found

**MINOR-1: `find_any()` bypasses `_blob_path()` path-traversal check -- constructs paths directly without `resolve()` or `is_relative_to()` validation**

- **Section**: 3.7, `find_any()` (lines 578-582)
- **Code**: `blob_path = self._data_dir / name / platform / f"{match_id}.json"`
- **Issue**: The `_blob_path()` method (lines 509-515) applies three-layer path traversal prevention: platform regex validation, `Path.resolve()`, and `path.is_relative_to(self._data_dir)`. The `find_any()` method validates the platform segment via `_validate_platform()` (line 575) but then constructs the blob path directly without calling `_blob_path()`. The constructed path is not `.resolve()`d and not checked against `is_relative_to(self._data_dir)`.
- **Exploitability**: Low. The `source_names` list comes from the registry (controlled by application configuration, validated against `^[a-z0-9_]+$`), and `platform` is validated against `^[A-Z0-9]+$`. The remaining component, `match_id`, could theoretically contain path traversal sequences in its non-platform portion (the part after the first underscore), but the filename is interpolated as `f"{match_id}.json"` which on POSIX systems would create a file *named* with slashes embedded -- `Path.__truediv__` does not split on slashes within a single component when constructed this way. Actually, `Path("base") / "a/b"` does resolve `a/b` as a subpath on Python's `pathlib`, which means a `match_id` of `NA1_../../etc/passwd` would produce path segments `NA1` (platform, validated) and then the filename `NA1_../../etc/passwd.json`. Using `pathlib`, `self._data_dir / name / "NA1" / "NA1_../../etc/passwd.json"` would resolve to an escaped path. This is blocked by `_blob_path()` via `resolve()` + `is_relative_to()`, but `find_any()` omits both checks.
- **Fix**: Either (a) call `_blob_path(name, match_id)` from within `find_any()` instead of constructing the path manually, or (b) apply `resolve()` + `is_relative_to()` to the constructed path in `find_any()`, consistent with `_blob_path()`. Option (a) is preferred as it centralizes path construction.
- **Impact**: Very low in practice because the `match_id` values reaching `find_any()` originate from stream envelopes whose `match_id` fields are populated by the Crawler from Riot API or op.gg API responses, not from user input. However, defense-in-depth demands that all path construction go through the same validation, especially since `find_any()` processes `match_id` values that may have been redelivered via DLQ (where the envelope content is not re-validated).

**MINOR-2: `find_any()` does not apply the `MAX_BLOB_SIZE_BYTES` check on cached blobs read from disk**

- **Section**: 3.7, `find_any()` (lines 580-582); 3.9 (line 711, blob size limit)
- **Code**: `data = await asyncio.to_thread(blob_path.read_bytes)` followed by `return (name, json.loads(data))`
- **Issue**: The `MAX_BLOB_SIZE_BYTES` guard in the coordinator (Section 3.9, line 754) applies only to `response.raw_blob` from a fresh fetch. The `find_any()` method reads blobs from disk and calls `json.loads(data)` without checking `len(data)`. If a blob was written to disk before the size limit was introduced (or if the disk is externally modified), an oversized blob on disk would bypass the size guard and be fully deserialized.
- **Exploitability**: Very low. Requires write access to `BLOB_DATA_DIR` to plant an oversized blob, or a historical blob written before the size limit was added. Since the design specifies write-once semantics and the size check is applied on all fresh writes, only pre-existing blobs or externally modified files are affected.
- **Fix**: Add `if len(data) > MAX_BLOB_SIZE_BYTES: continue` before `json.loads(data)` in the `find_any()` loop, or apply the same check in `BlobStore.read()`.
- **Impact**: Negligible. This is a defense-in-depth suggestion for consistency between the fresh-fetch and cache-hit paths.

### New Security Considerations in R3 Changes

**`required_context_keys` on Source protocol (ai-specialist MAJOR-1 fix)**: No security concern. The property is a `frozenset[str]` used for startup validation and pre-fetch key presence checking (Section 3.9, lines 744-748). The coordinator compares it against `context.extra.keys()` using set subtraction. The keys are never used in path construction, Redis key construction, or any injection-susceptible context. The values of `context.extra` are passed to `Source.fetch()` only, where each source implementation controls how they are used. This is a well-scoped extension with no new attack surface.

**`SourceRegistry.__init__()` startup cross-check (ai-specialist MAJOR-2 fix)**: No security concern. The cross-check validates that every source's `supported_data_types` appears in the extractor index. It raises `ValueError` at startup on mismatch. This is a fail-fast configuration validation that improves correctness without introducing any new trust boundary or input processing.

**`find_any(match_id, source_names)` parameter change (multiple reviewers)**: Security-positive. As analyzed in MINOR-2 disposition above, this change eliminates the rogue-directory attack surface by removing filesystem iteration in favor of a controlled list.

**Tmp file UUID naming (`uuid4().hex`) for BlobStore writes (formal-verifier FV-R2-1 fix)**: The tmp file name is now `.tmp_{match_id}_{os.getpid()}_{uuid4().hex}.json` (Section 3.7, line 543). `uuid4()` uses `os.urandom()` (CSPRNG) on CPython, producing 128 bits of randomness. This is more than sufficient to prevent prediction attacks on tmp file names. The purpose is collision avoidance between same-PID coroutines, not cryptographic security, so even a weaker random source would be acceptable. The `O_EXCL` flag on the subsequent `os.open()` call (line 548) provides the authoritative collision guard regardless. No concern.

**`blob_validation_failed` immediate DLQ routing (formal-verifier FV-R2-8 fix)**: Section 4 (lines 815-822) specifies that when `blob_validation_failed=True` and `status=all_exhausted`, the Fetcher routes the message directly to DLQ with `max_attempts=1`, preventing retry-budget exhaustion against structurally bad blobs. This is security-positive: it reduces the amplification potential of a poisoned blob (from `max_attempts` network round-trips down to 1) and prevents a compromised source from causing sustained retry churn.

### Verdict

**APPROVED**

All four Round 2 security issues (MINOR-1 through MINOR-4) have been resolved. The fixes are correctly implemented in the design text:

- MINOR-1: `path.is_relative_to(self._data_dir)` replaces the string prefix check. Correct.
- MINOR-2: `find_any()` iterates a caller-provided `source_names` list, not `iterdir()`. Rogue-directory attack surface eliminated.
- MINOR-3: Accepted as out of scope for this design doc. Tracked as a UI service code fix.
- MINOR-4: `MAX_BLOB_SIZE_BYTES = 2 MB` check placed before `json.loads()` in the coordinator. Correct placement and threshold.

The two new MINOR issues identified in this round (path validation bypass in `find_any()` and missing size check on cached blob reads) are defense-in-depth suggestions with very low exploitability. Neither represents a security vulnerability. They can be addressed during implementation without requiring another design revision.

The R2-to-R3 changes (required_context_keys, startup cross-check, source_names parameter, UUID tmp naming, blob_validation_failed routing) introduce no new security concerns and in several cases improve the security posture.

---

## Review Round 3 — AI Specialist

**Reviewer**: ai-specialist
**Round**: 3
**Date**: 2026-03-25

### Round 2 Issue Disposition

**MAJOR-1 (FetchContext.extra untyped footgun) -- RESOLVED.**

The `required_context_keys: frozenset[str]` property has been added to the Source protocol (Section 3.2, lines 178-188). The coordinator's waterfall loop (Section 3.9, lines 744-748) checks `entry.source.required_context_keys - set(context.extra.keys())` before calling `fetch()`, skipping with a warning when keys are missing. The FetchContext docstring (Section 3.2, line 208) documents that `extra` is populated by dumping all non-standard envelope payload fields as-is.

This is a complete and correct fix. The coordinator remains source-agnostic: it does not know what the keys mean, only that they must exist. The check runs at fetch-time, not startup-time -- this is the right choice because the envelope contents vary per message and cannot be fully validated at startup. The protocol docstring correctly notes that core fields (match_id, puuid, region) are always available, so `required_context_keys` only governs `extra` keys.

One minor note: the docstring says "the coordinator logs a warning if a source declares required keys that are not available in the envelope schema, preventing silent configuration errors" (line 208), which describes startup validation. But the pseudocode at lines 744-748 performs per-message validation (checking `context.extra.keys()` at fetch-time), not startup validation against the schema. These are complementary, not contradictory -- the per-message check is the operational safety net, and a startup schema check would be a configuration-time early warning. The design should clarify that both checks are intended, or that the per-message check is the sole mechanism. This is a documentation nit, not a functional gap.

**MAJOR-2 (DataType=str has no typo safety) -- RESOLVED.**

The `SourceRegistry.__init__()` (Section 3.3, lines 244-260) now accepts an optional `extractor_index: dict[tuple[str, DataType], Extractor]` parameter and cross-checks every source's `supported_data_types` against the keys of that index. A source declaring `frozenset({"mtach"})` when the extractor index only contains `("riot", "match")` entries would raise `ValueError` at startup.

The fix is correct and catches the exact failure mode described in R2: typo in a data type string silently drops traffic. The check is directional -- it validates that every source-declared data type has a corresponding extractor, but does not check the reverse (an extractor registered for a data type that no source supports). The forward direction is the critical one; the reverse is harmless (an orphaned extractor wastes no resources).

However, the check at line 252 (`known_data_types = {dt for (_, dt) in extractor_index}`) strips the source_name from the key and checks only the data type string. This means if Source A declares `supported_data_types = frozenset({"match"})` and the only extractor registered is `("source_b", "match")`, the check passes even though there is no extractor for `("source_a", "match")`. The error message at line 257-259 misleadingly says "no extractor is registered for (entry.name, dt)" when the actual check is weaker (it only verifies `dt` exists in any extractor key, not specifically `(entry.name, dt)`). See MINOR-1 below.

**MAJOR-3 (find_any() non-deterministic ordering) -- RESOLVED.**

`find_any()` now accepts `source_names: list[str]` (Section 3.7, line 557) and iterates in that order instead of using `Path.iterdir()`. The implementation (lines 578-582) constructs deterministic paths `self._data_dir / name / platform / f"{match_id}.json"` for each name in the priority list. The coordinator passes the registry's priority-ordered source name list (Section 3.9, line 727).

This is a complete fix. The iteration order is now fully deterministic, controlled by the registry priority, and the highest-fidelity blob is always preferred. The security reviewer's MINOR-2 concern (iterdir discovering rogue directories) is also resolved as a side effect: `find_any()` only checks paths for known, registered source names.

**MAJOR-4 (try_token() starvation risk) -- DOCUMENTED, NOT MITIGATED. Acceptable.**

The R2-to-R3 revision notes do not list MAJOR-4 as an addressed issue, and no new text was added to Section 3.5 documenting the starvation constraint. However, the `try_token()` specification (lines 390-414) is now fully specified, and the operational constraint (non-blocking callers lose to blocking callers when tokens are scarce) is inherent to the pattern.

This is acceptable. The starvation scenario requires sustained full-capacity utilization of the Riot rate limit by blocking callers (timeline fetch), leaving zero tokens for non-blocking waterfall callers. At current scale (20 req/s with 1-10 workers, timeline consuming at most 1 req per match), this is not a realistic concern. The design correctly prioritizes implementation simplicity (single token pool, no reservation) over handling a theoretical edge case. If this becomes a production issue, the mitigation is straightforward (token reservation or brief wait), and it can be added without architectural changes.

Verdict: Not explicitly resolved, but the risk was correctly assessed as acceptable in R2 and remains so. No action required.

**MINOR-1 (blob_validation_failed diagnostic signal) -- RESOLVED.**

Section 4 (lines 815-822) now specifies distinct Fetcher behavior for `all_exhausted + blob_validation_failed`: the Fetcher routes the message directly to DLQ with `max_attempts=1`, preventing the message from burning all retry cycles against the same structurally bad blob. This directly addresses both the diagnostic concern from R2 and the formal verifier's FV-R2-8 (retry exhaustion against unchanging bad source).

The fix is correct. The `blob_validation_failed` flag is no longer a passive diagnostic -- it actively changes control flow. The DLQ archive entry preserves the flag for operational debugging. This is the right approach: the coordinator signals "at least one source returned un-extractable data," and the Fetcher uses this to short-circuit retries.

**MINOR-2 (Proactive emit needs can_extract() for extra data types) -- NOT ADDRESSED, acceptable.**

The proactive emit remains deferred (Section 5, lines 874-878). Since the feature is not being implemented in this phase, the validation gap is theoretical. The R2 recommendation (call `can_extract()` for each additional data type before including it in the `stream:blob_available` payload) remains valid and should be applied when the proactive emit is activated. No action needed now.

**MINOR-3 (SourceRegistry priority uniqueness constraint) -- NOT ADDRESSED, acceptable.**

`SourceRegistry` still uses `priority: int` with no uniqueness check. Python's stable `sorted()` makes the behavior deterministic based on insertion order when priorities tie. At 2-5 sources, this is unlikely to be a problem. The `SOURCE_WATERFALL_ORDER` config string already implies ordering (first = highest priority), and the priority integer is derived from the position in that list. No action needed.

### Issues Found

**MINOR-1: SourceRegistry startup cross-check validates data type existence but not (source_name, data_type) pair.**

Section 3.3, lines 251-260. The cross-check builds `known_data_types = {dt for (_, dt) in extractor_index}`, which collects all data type strings across all source-extractor registrations. It then checks whether each source's declared data types exist in this set. This catches the typo case (a data type string that appears nowhere in the extractor index), but does not catch the case where Source A declares support for "match" and the only "match" extractor is registered for Source B.

Concretely: if `extractor_index = {("opgg", "match"): OpggExtractor}` and `RiotSource.supported_data_types = frozenset({"match"})`, the cross-check passes because "match" is a known data type. But at runtime, `_get_extractor("riot", "match")` returns `None` because no extractor is keyed to `("riot", "match")`. The coordinator would log a warning and skip, which is safe but surprising -- the startup check was supposed to catch this.

The fix is simple: check for the specific `(entry.name, dt)` pair in the extractor index, not just `dt` in the set of all data types. Change line 255 from `if dt not in known_data_types` to `if (entry.name, dt) not in extractor_index`. The error message at lines 257-259 already describes this stronger check ("no extractor is registered for (entry.name, dt)"), so the message is correct but the implementation does not match it.

Priority: MINOR. The current check catches the most common failure mode (data type typos). The uncaught case (source declares a data type that only another source's extractor handles) is an unusual misconfiguration that is caught at runtime by the `_get_extractor() is None` guard.

**MINOR-2: RiotSource.fetch() call site passes `region=context.region` to try_token(), but the try_token() specification does not accept a `region` parameter.**

Section 3.5, line 359-362: `granted = await try_token(self._r, limit_per_second=self._cfg.api_rate_limit_per_second, region=context.region)`. The `try_token()` specification (lines 393-413) accepts `key_prefix`, `limit_per_second`, and `limit_long` -- no `region` parameter. The specification explicitly notes: "The 'region' parameter on wait_for_token() is a legacy compat shim and is NOT replicated here" (lines 407-408).

The call site and the specification are inconsistent. The `region` parameter would either be passed as a keyword argument that `try_token()` does not accept (causing a `TypeError` at runtime), or it would need to be mapped to `key_prefix` (e.g., `key_prefix=f"ratelimit:{context.region}"`).

The fix is to update the call site in Section 3.5 to match the specification. If per-region rate limiting is intended (which it should be, since Riot rate limits are per-region), the call should be:

```python
granted = await try_token(
    self._r,
    key_prefix=f"ratelimit:{context.region}",
    limit_per_second=self._cfg.api_rate_limit_per_second,
)
```

Or the specification should add a `region` parameter that constructs the key_prefix internally. Either way, the current inconsistency would cause an implementation error.

Priority: MINOR. The intent is clear (non-blocking, per-region rate check), and the fix is a one-line parameter adjustment. But this is exactly the kind of spec-vs-callsite mismatch that leads to implementation bugs if not corrected.

### Summary

| R2 Issue | Disposition |
|----------|-------------|
| MAJOR-1 (required_context_keys) | Resolved |
| MAJOR-2 (DataType typo safety) | Resolved (with minor gap -- see MINOR-1) |
| MAJOR-3 (find_any() ordering) | Resolved |
| MAJOR-4 (try_token starvation) | Not explicitly addressed; acceptable |
| MINOR-1 (blob_validation_failed) | Resolved |
| MINOR-2 (proactive emit validation) | Not addressed; deferred feature, acceptable |
| MINOR-3 (priority uniqueness) | Not addressed; acceptable |

| New Issue | Severity |
|-----------|----------|
| MINOR-1: Registry cross-check validates dt existence, not (source, dt) pair | MINOR |
| MINOR-2: RiotSource.fetch() passes `region` param that try_token() spec rejects | MINOR |

### Verdict

**APPROVED WITH MINORS**

All four R2 MAJOR issues are resolved or acceptably dispositioned. The design is sound: the waterfall coordination pattern is correct, `required_context_keys` properly bridges the source-agnostic coordinator with source-specific context needs, the `SourceRegistry` cross-check catches the dominant DataType typo failure mode at startup, `find_any()` now iterates in deterministic priority order, and `blob_validation_failed` actively short-circuits retries rather than being a passive diagnostic.

The two new MINOR issues are spec inconsistencies that would cause minor implementation friction but do not represent architectural problems. Both have obvious one-line fixes.

No further review rounds are needed from the AI Specialist perspective.

---

## Phase 6 — Production Readiness Review (2026-03-25)

### Developer Review

**Verdict**: REQUEST CHANGES — F1 (crash on null raw_blob) and F5 (unhandled ExtractionError in live path) must be fixed before production. F3 (disabled cross-check) and F6 (blocking I/O) addressed in same pass.

| ID | Severity | Issue |
|----|----------|-------|
| F1 | CRITICAL | `json.loads(None)` crash in `_handle_success()` when source returns SUCCESS without raw_blob |
| F5 | MAJOR | `ExtractionError` not caught in `_handle_success()` — blob persisted then extraction fails → unbreakable retry loop |
| F3 | MAJOR | Startup cross-check disabled in `_build_coordinator()` — latent typo-safety gap |
| F6 | MAJOR | Synchronous `stat()` calls on async event loop in BlobStore (lines 58, 65, 80, 127) |
| F9 | MAJOR | No per-source metrics/counters — operator blind to waterfall fallback rates |
| F12 | MAJOR | No isolated BlobStore unit tests |
| F14 | MAJOR | No test for `ExtractionError` in live fetch path |
| F2 | MINOR | `source_waterfall_order` config field declared but not consumed |
| F4 | MINOR | match_id suffix not validated in `_blob_path()` (mitigated by is_relative_to backstop) |
| F7 | MINOR | `find_any()` bypasses `_blob_path()` defense-in-depth |
| F8 | MINOR | `OpggClient` not closed on shutdown |
| F10 | MINOR | Silent `can_extract=False` on live fetch — no log emitted |
| F11 | MINOR | BlobStore disabled despite `blob_data_dir` already in Config |
| F13 | MINOR | No isolated SourceRegistry unit tests |
| F15 | MINOR | No test for BlobStore concurrent write race |

---

### Formal Verifier Review

**Verdict**: CONDITIONAL — correct under stated assumptions.

**Assumptions**: (1) Redis keys do not expire between duplicate deliveries. (2) Sources return deterministic content for same match_id. (3) `system:halted` is eventually cleared.

| # | Severity | Finding |
|---|----------|---------|
| 1 | MINOR | BlobStore.write() TOCTOU allows duplicate writes of identical content — benign, data integrity preserved by atomic rename |
| 2 | MAJOR | `auth_error` handler does not ACK — message stays in PEL during halt (by design, but undocumented); prolonged halt causes XAUTOCLAIM churn proportional to PEL size |
| 3 | MINOR | Duplicate `stream:parse` messages when N coroutines process same match_id — parser is idempotent |
| 4 | MINOR | Timeline fetch uses plain SET not SET NX — duplicate API call possible under concurrency |
| 5 | MINOR | Startup cross-check bypassed — documented, latent risk only |
| 6 | MINOR | No JSONDecodeError guard on raw_blob parse in `_handle_success` |

**Note on Finding 2**: The halt-induced PEL accumulation should be documented in `docs/architecture/06-failure-resilience.md` as expected behavior. During a prolonged halt, XAUTOCLAIM repeatedly redelivers stuck PEL messages to all fetcher instances. Operators should monitor PEL size via `XPENDING stream:match_ids fetchers` and clear halt promptly.

---

### Optimizer Review

**Top findings by impact**:

| # | Severity | Finding |
|---|----------|---------|
| 1.2 | MAJOR | `RawStore.exists()` does full bundle scan on Redis miss — O(300K) string comparisons per call at 6 months of data |
| 4.1 | MAJOR | Synchronous `stat()` in async `BlobStore` methods blocks event loop — 5–50ms per call on slow filesystems |
| 1.1 | MINOR | Double JSON serialize/deserialize of Riot blob — `response.data` available but unused, raw_blob re-parsed |
| 2.1 | MINOR | Three in-memory copies of match blob during `_handle_success` — ~1MB peak per message |
| 3.1 | MINOR | `find_any()` sequential stat() per source — grows linearly with source count |
| 5.3 | MINOR | `source_names` property rebuilds list per call — negligible but cacheable |

**Top recommendations**: (1) Wrap synchronous stat() calls in `asyncio.to_thread()`. (2) Short-circuit `RawStore.set()` bundle scan when match is known-new. (3) Use `response.data` directly in `_handle_success` when non-None (avoids redundant JSON parse).

---

### Security Review

**Verdict**: No CRITICAL or MAJOR findings. Implementation demonstrates good security awareness.

| # | Severity | Finding |
|---|----------|---------|
| 1.1 | MINOR | `find_any()` skips `is_relative_to` backstop — asymmetry with `_blob_path()` |
| 1.2 | MINOR | match_id suffix unsanitized in filename — `is_relative_to` backstop catches traversal |
| 2.3 | MINOR | match_id not URL-encoded in `riot_api.py get_match()` — informational |
| 3.2 | MINOR | Cached blob read has no size guard — requires host-level access to exploit |
| 4.2 | MINOR | Dead `opgg_api_key` config field — no leak vector, cleanup recommended |
| 5.3 | MINOR | No disk quota for blob writes — operational concern, 2MB/blob write-once |

**Positive**: Three-layer path traversal defense in `_blob_path()`, source name regex at construction time, 2MB blob size limit, atomic writes, write-once semantics, non-root container, capability dropping, no new secrets introduced.

**Recommended actions**: (1) Add `.resolve()` + `is_relative_to` to `find_any()`. (2) Add match_id format regex `^[A-Z0-9]+_\d+$` at `FetchContext` construction. (3) Remove dead `opgg_api_key` field. (4) URL-encode match_id in `riot_api.py get_match()`.

---

### Architect Review

**Verdict**: Foundational abstractions excellent; integration layer has critical gaps.

| ID | Severity | Finding |
|----|----------|---------|
| DD-1 | MAJOR | `source_waterfall_order` declared, exposed in compose, but never consumed — operator env var has no effect |
| DD-2 | MAJOR | BlobStore always `None` — `blob_data_dir` config and Docker volume exist but not wired into `_build_coordinator()` |
| DD-3 | MAJOR | `ExtractionError` unhandled in fresh-fetch path — blob persisted before extraction; creates poisoned-blob retry loop |
| DD-4 | MAJOR | `_build_coordinator()` uses hardcoded conditionals — adding new source requires fetcher code edit, violating design's "zero coordinator changes" promise |
| DD-5 | MINOR | Registry startup cross-check bypassed — `OpggSource` declares `BUILD` but no BUILD extractor exists |
| DD-6 | MINOR | `source:stats` Redis Hash not implemented — no per-source operational metrics |
| DD-7 | MINOR | `OpggSource.fetch()` always returns UNAVAILABLE — op.gg integration inert end-to-end (BlobStore also disabled per DD-2) |

**Positive**: Core abstractions (`Source` protocol, `Extractor` protocol, `SourceRegistry`, `BlobStore`, `WaterfallCoordinator`) are source-agnostic with zero source-specific conditionals. Safe rollback with default config. Clean service boundary separation.

**Addressing DD-1 through DD-4 brings implementation into full design conformance.**