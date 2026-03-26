"""SourceRegistry -- ordered list of data sources with startup validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lol_pipeline.sources.base import DataType, Extractor, Source

_SOURCE_NAME_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class SourceEntry:
    """One registered source with its priority and authority declarations.

    primary_for controls terminal semantics:
    - NOT_FOUND from a primary source is terminal (match does not exist).
    - AUTH_ERROR from a primary source triggers system:halted.
    - From non-primary sources, both are treated as UNAVAILABLE.
    """

    name: str
    source: Source
    priority: int
    primary_for: frozenset[DataType] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not _SOURCE_NAME_RE.match(self.name):
            raise ValueError(f"source name {self.name!r} must match ^[a-z0-9_]+$")
        if self.name != self.source.name:
            raise ValueError(
                f"SourceEntry name {self.name!r} must match source.name {self.source.name!r}"
            )


class SourceRegistry:
    """Ordered, validated collection of data sources.

    Startup cross-check: every (source_name, data_type) pair declared in
    source.supported_data_types must have a matching extractor registered
    in the extractor_index. This catches typos at startup rather than
    silently dropping traffic at runtime.
    """

    def __init__(
        self,
        entries: list[SourceEntry],
        extractor_index: dict[tuple[str, DataType], Extractor] | None = None,
    ) -> None:
        self._entries = sorted(entries, key=lambda e: e.priority)
        self._by_name: dict[str, SourceEntry] = {e.name: e for e in self._entries}

        # Pre-compute sources_for lookup -- O(1) at runtime.
        self._by_type: dict[DataType, list[SourceEntry]] = {}
        for entry in self._entries:
            for dt in entry.source.supported_data_types:
                self._by_type.setdefault(dt, []).append(entry)

        # Startup cross-check: validate (source_name, data_type) pairs.
        if extractor_index is not None:
            for entry in self._entries:
                for dt in entry.source.supported_data_types:
                    if (entry.name, dt) not in extractor_index:
                        raise ValueError(
                            f"Source {entry.name!r} declares support for data type "
                            f"{dt!r}, but no extractor is registered for "
                            f"({entry.name!r}, {dt!r}). Check for typos."
                        )

    def sources_for(self, data_type: DataType) -> list[SourceEntry]:
        """Return sources supporting data_type, ordered by priority. O(1)."""
        return list(self._by_type.get(data_type, []))

    def get(self, name: str) -> SourceEntry | None:
        return self._by_name.get(name)

    @property
    def all_sources(self) -> list[SourceEntry]:
        return list(self._entries)

    @property
    def source_names(self) -> list[str]:
        """Source names in priority order. Used by BlobStore.find_any()."""
        return [e.name for e in self._entries]
