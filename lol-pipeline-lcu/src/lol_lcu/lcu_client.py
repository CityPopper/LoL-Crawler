"""LCU HTTP client — reads lockfile, queries League client API."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
import urllib3

from lol_lcu.log import get_logger

# Suppress insecure HTTPS warnings (LCU uses self-signed certs)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = get_logger("lcu")

_TIMEOUT = 10


def _redact_lockfile(content: str) -> str:
    """Redact sensitive fields from lockfile content for safe logging.

    Lockfile format: ``LeagueClient:PID:PORT:PASSWORD:PROTOCOL``
    Only the first two fields (app name, PID) are safe to log.
    """
    parts = content[:80].split(":")
    if len(parts) <= 2:
        return content[:80]
    return ":".join(parts[:2]) + ":***"


class LcuNotRunningError(Exception):
    """Raised when the League client is not running or lockfile is missing."""


class LcuAuthError(Exception):
    """Raised when LCU returns 401/403 — lockfile credentials are stale."""


class LcuClient:
    """Client for the local League Client Update (LCU) HTTP API."""

    def __init__(self, install_path: str | None = None) -> None:
        self.install_path = install_path or os.environ.get("LEAGUE_INSTALL_PATH", "")
        self.host = os.environ.get("LCU_HOST", "127.0.0.1")

        lockfile = Path(self.install_path) / "lockfile"
        if not lockfile.exists():
            raise LcuNotRunningError(
                f"Lockfile not found at {lockfile}. Is the League client running?"
            )

        content = lockfile.read_text().strip()
        if not content:
            raise LcuNotRunningError("Lockfile is empty. Is the League client running?")

        parts = content.split(":")
        if len(parts) < 4:
            raise LcuNotRunningError(
                f"Malformed lockfile (expected at least 4 colon-separated fields, "
                f"got {len(parts)}): {_redact_lockfile(content)}"
            )
        try:
            self.port = int(parts[2])
        except ValueError as exc:
            raise LcuNotRunningError(
                f"Malformed lockfile (non-numeric port: {parts[2]!r})"
            ) from exc
        if not (1 <= self.port <= 65535):
            raise LcuNotRunningError(f"Malformed lockfile (port out of range: {self.port})")
        self.password = parts[3]

    @property
    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"

    def _get(self, path: str) -> Any:
        """Make an authenticated GET request to the LCU API."""
        url = f"{self.base_url}{path}"
        try:
            resp = requests.get(
                url,
                auth=("riot", self.password),
                verify=False,  # noqa: S501
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            if resp.status_code in (401, 403):
                raise LcuAuthError(
                    f"LCU returned {resp.status_code} — lockfile credentials likely stale "
                    f"({self.base_url}, LCU_HOST={self.host})"
                ) from exc
            raise LcuNotRunningError(
                f"LCU API request failed ({self.base_url}, LCU_HOST={self.host}): {exc}"
            ) from exc
        except (requests.RequestException, ValueError) as exc:
            raise LcuNotRunningError(
                f"LCU API request failed ({self.base_url}, LCU_HOST={self.host}): {exc}"
            ) from exc

    def current_summoner(self) -> dict[str, Any]:
        """Get the current summoner (puuid, gameName, tagLine)."""
        return self._get("/lol-summoner/v1/current-summoner")  # type: ignore[no-any-return]

    def match_history(
        self, puuid: str, beg_index: int = 0, end_index: int = 20
    ) -> list[dict[str, Any]]:
        """Get paginated match history for a puuid."""
        data = self._get(
            f"/lol-match-history/v1/products/lol/{puuid}/matches"
            f"?begIndex={beg_index}&endIndex={end_index}"
        )
        return data.get("games", {}).get("games", [])  # type: ignore[no-any-return]
