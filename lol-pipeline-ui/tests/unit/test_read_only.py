"""STRUCT-3: Static analysis tests asserting lol-pipeline-ui has NO Redis write calls.

After splitting admin operations into lol-pipeline-admin-ui, the read-only
UI service must contain zero Redis mutation calls in its production source.
Test files (test_*.py, conftest.py) are excluded from the scan since they
legitimately seed fake-Redis state for test setup.

These tests scan every .py file under src/lol_ui/ (excluding test files)
and assert that none of the forbidden Redis write method patterns appear.

Exception: POST /player/refresh (routes/stats.py::player_refresh) is an
intentional write endpoint that clears cooldowns and publishes to stream:puuid.
Its imports and call-sites are excluded from the read-only invariant via
_WRITE_ALLOWED_FUNCTIONS below.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Root of the lol-pipeline-ui production source
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "lol_ui"

# Functions that are intentionally allowed to perform Redis writes.
# POST /player/refresh is a write endpoint that clears cooldowns and
# publishes to stream:puuid — it lives in the otherwise read-only stats module.
# Key: relative path from _SRC_ROOT, Value: set of function names.
_WRITE_ALLOWED_FUNCTIONS: dict[str, set[str]] = {
    "routes/stats.py": {
        "player_refresh",
        "show_stats",
        "_auto_enqueue_or_wait",
        "_resolve_puuid",
        "stats_load_more",
    },
}

# Module-level import lines that are only used by allowed write functions.
# These lines are excluded from import-pattern scanning.
# Key: relative path from _SRC_ROOT, Value: set of line substrings to allow.
_WRITE_ALLOWED_IMPORTS: dict[str, set[str]] = {
    "routes/stats.py": {
        "from lol_pipeline.priority import",
        "from lol_pipeline.streams import publish",
    },
}

# Redis write method patterns that must not appear in read-only UI source.
# Each pattern matches `<variable>.method(` with optional whitespace.
# We also catch pipeline-batched writes like `pipe.set(`.
_WRITE_METHOD_PATTERNS: list[re.Pattern[str]] = [
    # Direct Redis mutations
    re.compile(r"\.\s*xadd\s*\("),
    re.compile(r"\.\s*xdel\s*\("),
    re.compile(r"(?<!\w)(?:r|pipe|redis)\s*\.\s*set\s*\("),
    re.compile(r"(?<!\w)(?:r|pipe|redis)\s*\.\s*delete\s*\("),
    re.compile(r"\.\s*hdel\s*\("),
    re.compile(r"\.\s*lrem\s*\("),
    re.compile(r"\.\s*hset\s*\("),
    re.compile(r"\.\s*zadd\s*\("),
    re.compile(r"\.\s*zrem\s*\("),
    re.compile(r"\.\s*zremrangebyrank\s*\("),
    re.compile(r"\.\s*sadd\s*\("),
    re.compile(r"\.\s*srem\s*\("),
    re.compile(r"\.\s*rpush\s*\("),
    re.compile(r"\.\s*lpush\s*\("),
    re.compile(r"\.\s*incr\s*\("),
    re.compile(r"\.\s*decr\s*\("),
    re.compile(r"\.\s*expire\s*\("),
    re.compile(r"\.\s*setex\s*\("),
    re.compile(r"\.\s*setnx\s*\("),
    re.compile(r"\.\s*mset\s*\("),
    re.compile(r"\.\s*flushall\s*\("),
    re.compile(r"\.\s*flushdb\s*\("),
]

# Imported common-lib functions that perform Redis writes
_WRITE_IMPORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"from\s+lol_pipeline\.streams\s+import\s+.*\bpublish\b"),
    re.compile(r"from\s+lol_pipeline\.streams\s+import\s+.*\breplay_from_dlq\b"),
    re.compile(r"from\s+lol_pipeline\.priority\s+import\s+.*\bset_priority\b"),
]

# Call-site patterns for imported write functions
_WRITE_CALL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bpublish\s*\("),
    re.compile(r"\breplay_from_dlq\s*\("),
    re.compile(r"\bset_priority\s*\("),
]


def _allowed_line_ranges(path: Path) -> list[tuple[int, int]]:
    """Return (start_line, end_line) ranges for functions in _WRITE_ALLOWED_FUNCTIONS.

    Uses AST to find the exact line range of each allowed function, so only
    those lines are excluded from the write-method scan.
    """
    rel = str(path.relative_to(_SRC_ROOT))
    func_names = _WRITE_ALLOWED_FUNCTIONS.get(rel)
    if not func_names:
        return []

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in func_names:
                ranges.append((node.lineno, node.end_lineno or node.lineno))
    return ranges


def _is_allowed_import(path: Path, line: str) -> bool:
    """Return True if *line* matches an allowed import for *path*."""
    rel = str(path.relative_to(_SRC_ROOT))
    allowed = _WRITE_ALLOWED_IMPORTS.get(rel)
    if not allowed:
        return False
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in allowed)


def _is_test_file(path: Path) -> bool:
    """Return True for test files and conftest.py that are excluded from scanning."""
    name = path.name
    return name.startswith("test_") or name == "conftest.py"


def _production_source_files() -> list[Path]:
    """Collect all non-test .py source files under src/lol_ui/."""
    assert _SRC_ROOT.is_dir(), f"Source root not found: {_SRC_ROOT}"
    return sorted(p for p in _SRC_ROOT.rglob("*.py") if not _is_test_file(p))


def _scan_file_for_patterns(
    path: Path,
    patterns: list[re.Pattern[str]],
    *,
    skip_ranges: list[tuple[int, int]] | None = None,
    skip_imports: bool = False,
) -> list[tuple[int, str, str]]:
    """Return (line_number, pattern_text, line_text) for every match in *path*.

    Parameters
    ----------
    skip_ranges:
        Line ranges ``(start, end)`` to exclude (used for allowed write functions).
    skip_imports:
        If True, also exclude lines that match ``_WRITE_ALLOWED_IMPORTS`` for this file.
    """
    violations: list[tuple[int, str, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("#"):
            continue
        # Skip lines inside allowed write functions
        if skip_ranges and any(start <= lineno <= end for start, end in skip_ranges):
            continue
        # Skip allowed import lines
        if skip_imports and _is_allowed_import(path, line):
            continue
        for pat in patterns:
            if pat.search(line):
                violations.append((lineno, pat.pattern, line.rstrip()))
    return violations


class TestUiHasNoRedisWriteMethods:
    """After STRUCT-3, the UI service must have zero Redis write method calls."""

    def test_no_redis_write_methods_in_source(self) -> None:
        """Scan all production source files for direct Redis write method calls.

        Lines inside _WRITE_ALLOWED_FUNCTIONS (e.g. player_refresh) are excluded.
        """
        all_violations: list[str] = []
        for src_file in _production_source_files():
            ranges = _allowed_line_ranges(src_file)
            violations = _scan_file_for_patterns(
                src_file, _WRITE_METHOD_PATTERNS, skip_ranges=ranges
            )
            for lineno, pattern, line in violations:
                rel = src_file.relative_to(_SRC_ROOT)
                all_violations.append(f"  {rel}:{lineno}  pattern={pattern!r}\n    {line}")
        assert not all_violations, (
            f"Found {len(all_violations)} Redis write method call(s) in read-only UI:\n"
            + "\n".join(all_violations)
        )

    def test_no_write_function_imports(self) -> None:
        """No production source file should import write functions from lol_pipeline.

        Imports required by _WRITE_ALLOWED_FUNCTIONS (e.g. publish, set_priority
        for player_refresh) are excluded via _WRITE_ALLOWED_IMPORTS.
        """
        all_violations: list[str] = []
        for src_file in _production_source_files():
            violations = _scan_file_for_patterns(
                src_file, _WRITE_IMPORT_PATTERNS, skip_imports=True
            )
            for lineno, pattern, line in violations:
                rel = src_file.relative_to(_SRC_ROOT)
                all_violations.append(f"  {rel}:{lineno}  pattern={pattern!r}\n    {line}")
        assert not all_violations, (
            f"Found {len(all_violations)} write-function import(s) in read-only UI:\n"
            + "\n".join(all_violations)
        )

    def test_no_write_function_calls(self) -> None:
        """No production source file should call publish(), replay_from_dlq(), or set_priority().

        Call-sites inside _WRITE_ALLOWED_FUNCTIONS (e.g. player_refresh) are excluded.
        """
        all_violations: list[str] = []
        for src_file in _production_source_files():
            ranges = _allowed_line_ranges(src_file)
            violations = _scan_file_for_patterns(
                src_file, _WRITE_CALL_PATTERNS, skip_ranges=ranges
            )
            for lineno, pattern, line in violations:
                rel = src_file.relative_to(_SRC_ROOT)
                all_violations.append(f"  {rel}:{lineno}  pattern={pattern!r}\n    {line}")
        assert not all_violations, (
            f"Found {len(all_violations)} write-function call(s) in read-only UI:\n"
            + "\n".join(all_violations)
        )

    def test_source_root_exists_and_has_files(self) -> None:
        """Sanity check: the source root exists and contains Python files."""
        files = _production_source_files()
        assert len(files) > 10, f"Expected at least 10 production source files, found {len(files)}"
