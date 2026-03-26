"""Base types for the source waterfall abstraction.

DataType is an open string alias -- new sources can define constants in their
own packages without editing this file. The coordinator treats DataType as an
opaque key and never switches on its values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# DataType -- open string alias
# ---------------------------------------------------------------------------

DataType = str

# Well-known pipeline data types.
MATCH: DataType = "match"
BUILD: DataType = "build"
# TIMELINE is intentionally excluded from the initial waterfall scope.
# When timeline fetching migrates into the waterfall, add:
#   TIMELINE: DataType = "timeline"


# ---------------------------------------------------------------------------
# FetchContext
# ---------------------------------------------------------------------------

_MATCH_ID_RE = re.compile(r"^[A-Z0-9]+_\d+$")


@dataclass(frozen=True)
class FetchContext:
    """All information available to a source for fetching data.

    Each source uses whatever fields it needs:
    - Riot uses match_id + region.
    - Op.gg may use puuid + region.
    - Future sources can use extra for anything else.

    The coordinator builds this from the stream envelope payload.
    """

    match_id: str
    puuid: str
    region: str
    extra: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _MATCH_ID_RE.match(self.match_id):
            msg = f"Invalid match_id format: {self.match_id!r}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# FetchResult / FetchResponse
# ---------------------------------------------------------------------------


class FetchResult(Enum):
    SUCCESS = "success"
    THROTTLED = "throttled"  # rate-limited; try next source
    NOT_FOUND = "not_found"  # permanent; terminal if source is primary
    AUTH_ERROR = "auth_error"  # critical; triggers system:halted if primary
    SERVER_ERROR = "server_error"  # transient; try next source
    UNAVAILABLE = "unavailable"  # source is down; try next source


@dataclass
class FetchResponse:
    result: FetchResult
    raw_blob: bytes | None = None
    data: dict[str, str] | None = None
    retry_after_ms: int | None = None
    available_data_types: frozenset[DataType] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Source Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Source(Protocol):
    @property
    def name(self) -> str:
        """Unique identifier (e.g. 'riot', 'opgg'). Must match ^[a-z0-9_]+$."""
        ...

    @property
    def supported_data_types(self) -> frozenset[DataType]:
        """Data types this source can provide. Used to skip inapplicable sources."""
        ...

    @property
    def required_context_keys(self) -> frozenset[str]:
        """Keys that must be present in FetchContext.extra.

        Return frozenset() if only the core fields (match_id, puuid, region)
        are needed. The coordinator checks this before calling fetch() and
        skips sources whose required keys are absent, logging a warning.
        """
        ...

    async def fetch(self, context: FetchContext, data_type: DataType) -> FetchResponse:
        """Fetch data for the given context and data type.

        The source is responsible for its own rate limiting, authentication,
        and error mapping. The coordinator only inspects FetchResult.
        """
        ...

    async def close(self) -> None:
        """Clean up resources (HTTP sessions, etc.)."""
        ...


# ---------------------------------------------------------------------------
# Extractor Protocol
# ---------------------------------------------------------------------------


class ExtractionError(Exception):
    """Raised by Extractor.extract() on failure."""


@runtime_checkable
class Extractor(Protocol):
    @property
    def source_name(self) -> str:
        """Which source's blobs this extractor handles. Must match source.name."""
        ...

    @property
    def data_types(self) -> frozenset[DataType]:
        """Data types this extractor can produce from a blob."""
        ...

    def can_extract(self, blob: dict[str, str]) -> bool:
        """Return True if this blob contains extractable data.

        Called BEFORE persisting a blob to BlobStore. If False, the blob is
        NOT persisted and the coordinator routes to the next source.
        """
        ...

    def extract(self, blob: dict[str, str], match_id: str, region: str) -> dict[str, str]:
        """Extract data from blob. Returns canonical Riot-shaped dict.

        Raises ExtractionError on failure.
        """
        ...


# ---------------------------------------------------------------------------
# WaterfallResult
# ---------------------------------------------------------------------------

WaterfallStatus = Literal["success", "not_found", "auth_error", "all_exhausted", "cached"]


@dataclass
class WaterfallResult:
    status: WaterfallStatus
    data: dict[str, str] | None = None
    source: str = ""
    retry_after_ms: int | None = None
    available_data_types: frozenset[DataType] = field(default_factory=frozenset)
    blob_validation_failed: bool = False
