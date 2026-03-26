"""Tests for scripts/anonymize_and_upload.py (HFV-2).

Covers three functions:
  - _is_anomalous_date  (5 tests)
  - _upload_file         (2 tests)
  - _process_file        (4 tests)

No network, no Docker — all use tmp_path + unittest.mock.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import zstandard as zstd

from anonymize_and_upload import _is_anomalous_date, _process_file, _upload_file


# =========================================================================
# _is_anomalous_date (5 tests)
# =========================================================================


def test_is_anomalous_date__epoch_year__returns_true() -> None:
    """1970-01 is anomalous (year < 2020)."""
    assert _is_anomalous_date("1970-01.jsonl.zst") is True


def test_is_anomalous_date__boundary_year__returns_false() -> None:
    """2020-01 is NOT anomalous (boundary: < 2020 means 2020 itself is OK)."""
    assert _is_anomalous_date("2020-01.jsonl.zst") is False


def test_is_anomalous_date__recent_year__returns_false() -> None:
    """2024-03 is a normal recent date."""
    assert _is_anomalous_date("2024-03.jsonl.zst") is False


def test_is_anomalous_date__no_date_in_filename__returns_false() -> None:
    """Filename without a date pattern does not raise and returns False."""
    assert _is_anomalous_date("matches.jsonl.zst") is False


def test_is_anomalous_date__pre_2020_year__returns_true() -> None:
    """1999-12 is anomalous (year < 2020)."""
    assert _is_anomalous_date("1999-12.jsonl.zst") is True


# =========================================================================
# _upload_file (2 tests)
# =========================================================================


def test_upload_file__calls_api_with_correct_args() -> None:
    """api.upload_file is called with path_in_repo='NA1/<filename>', correct repo_id/token."""
    api = MagicMock()
    local_path = Path("/tmp/fake/2024-03.jsonl.zst")
    filename = "2024-03.jsonl.zst"
    repo_id = "CityPopper/LoL-Scraper"
    token = "hf_test_token"

    _upload_file(api, local_path, filename, repo_id, token)

    api.upload_file.assert_called_once_with(
        path_or_fileobj=str(local_path),
        path_in_repo="NA1/2024-03.jsonl.zst",
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )


def test_upload_file__always_prepends_na1_prefix() -> None:
    """path_in_repo always has 'NA1/' prefix — bare filename is never passed."""
    api = MagicMock()
    local_path = Path("/tmp/fake/matches.jsonl.zst")
    filename = "matches.jsonl.zst"

    _upload_file(api, local_path, filename, "repo/id", "tok")

    call_kwargs = api.upload_file.call_args
    path_in_repo = call_kwargs.kwargs.get("path_in_repo") or call_kwargs[1].get("path_in_repo")
    assert path_in_repo.startswith("NA1/"), f"Expected NA1/ prefix, got: {path_in_repo}"
    assert path_in_repo != filename, "Bare filename should never be passed"


# =========================================================================
# _process_file (4 tests)
# =========================================================================


def _make_zst(tmp_path: Path, lines: list[str], filename: str = "test.jsonl.zst") -> Path:
    """Helper: compress lines into a .jsonl.zst file and return the path."""
    cctx = zstd.ZstdCompressor()
    raw = "\n".join(lines).encode("utf-8")
    if raw and not raw.endswith(b"\n"):
        raw += b"\n"
    zst_path = tmp_path / filename
    zst_path.write_bytes(cctx.compress(raw))
    return zst_path


def test_process_file__three_lines__returns_three(tmp_path: Path) -> None:
    """A zst file with 3 non-blank lines returns record count 3."""
    zst_path = _make_zst(tmp_path, ['{"a":1}', '{"b":2}', '{"c":3}'])

    with patch("anonymize_and_upload._upload_file") as mock_upload:
        count = _process_file(zst_path, MagicMock(), "repo", "tok")

    assert count == 3


def test_process_file__blank_lines_skipped__returns_two(tmp_path: Path) -> None:
    """2 real lines + 1 blank line → returns 2 (blank lines not counted)."""
    zst_path = _make_zst(tmp_path, ['{"a":1}', "", '{"b":2}'])

    with patch("anonymize_and_upload._upload_file") as mock_upload:
        count = _process_file(zst_path, MagicMock(), "repo", "tok")

    assert count == 2


def test_process_file__calls_upload_once(tmp_path: Path) -> None:
    """_upload_file is called exactly once with the correct path."""
    zst_path = _make_zst(tmp_path, ['{"a":1}', '{"b":2}', '{"c":3}'])

    with patch("anonymize_and_upload._upload_file") as mock_upload:
        _process_file(zst_path, MagicMock(), "repo", "tok")

    mock_upload.assert_called_once()
    call_args = mock_upload.call_args
    # Second positional arg is zst_path
    assert call_args[0][1] == zst_path


def test_process_file__empty_file__returns_zero_upload_still_called(tmp_path: Path) -> None:
    """Empty zst file → returns 0, but upload is still called once."""
    # Compress an empty byte string
    cctx = zstd.ZstdCompressor()
    zst_path = tmp_path / "empty.jsonl.zst"
    zst_path.write_bytes(cctx.compress(b""))

    with patch("anonymize_and_upload._upload_file") as mock_upload:
        count = _process_file(zst_path, MagicMock(), "repo", "tok")

    assert count == 0
    mock_upload.assert_called_once()
