#!/usr/bin/env python3
"""
Fetch live Riot API responses for Pwnerer#1337 and save them as mock fixtures.

Usage:
    python scripts/update_mocks.py

Requires RIOT_API_KEY in environment (or .env).
Saves fixtures to scripts/fixtures/pwnerer1337/.
Also updates pact example payloads with real PUUID data.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Load .env from repo root if RIOT_API_KEY not already set
_ROOT_ENV = Path(__file__).parent.parent / ".env"
if "RIOT_API_KEY" not in os.environ and _ROOT_ENV.exists():
    for line in _ROOT_ENV.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Add common src to path for standalone execution
_COMMON_SRC = Path(__file__).parent.parent / "lol-pipeline-common" / "src"
if str(_COMMON_SRC) not in sys.path:
    sys.path.insert(0, str(_COMMON_SRC))

from lol_pipeline.riot_api import RiotClient  # noqa: E402

_FIXTURES = Path(__file__).parent / "fixtures" / "pwnerer1337"
_GAME_NAME = "Pwnerer"
_TAG_LINE = "1337"
_REGION = "na1"
_MATCH_COUNT = 5


def _save(filename: str, data: object) -> None:
    _FIXTURES.mkdir(parents=True, exist_ok=True)
    path = _FIXTURES / filename
    path.write_text(json.dumps(data, indent=2))
    print(f"  saved {path.relative_to(Path(__file__).parent.parent)}")


def _update_pact(pact_path: Path, puuid: str) -> None:
    """Update the example PUUID in a pact file with the real value."""
    if not pact_path.exists():
        return
    pact = json.loads(pact_path.read_text())
    changed = False
    for msg in pact.get("messages", []):
        payload = msg.get("contents", {}).get("payload", {})
        if "puuid" in payload and payload["puuid"] != puuid:
            payload["puuid"] = puuid
            changed = True
        if "game_name" in payload:
            payload["game_name"] = _GAME_NAME
            changed = True
        if "tag_line" in payload:
            payload["tag_line"] = _TAG_LINE
            changed = True
        if "region" in payload:
            payload["region"] = _REGION
            changed = True
    if changed:
        pact_path.write_text(json.dumps(pact, indent=2))
        print(f"  updated pact {pact_path.name}")


async def run() -> None:
    api_key = os.environ.get("RIOT_API_KEY", "")
    if not api_key:
        print("ERROR: RIOT_API_KEY not set. Run 'just setup' and edit .env first.")
        sys.exit(1)

    client = RiotClient(api_key)
    try:
        print(f"Fetching account for {_GAME_NAME}#{_TAG_LINE} ({_REGION})...")
        account = await client.get_account_by_riot_id(_GAME_NAME, _TAG_LINE, _REGION)
        _save("account.json", account)
        puuid: str = account["puuid"]
        print(f"  PUUID: {puuid[:20]}...")

        print(f"Fetching {_MATCH_COUNT} recent match IDs...")
        match_ids = await client.get_match_ids(
            puuid, _REGION, start=0, count=_MATCH_COUNT
        )
        _save("match_ids.json", match_ids)
        print(f"  Got {len(match_ids)} match IDs")

        if match_ids:
            print(f"Fetching match details for {match_ids[0]}...")
            match_data = await client.get_match(match_ids[0], _REGION)
            _save(f"match_{match_ids[0]}.json", match_data)
            print(f"  Saved match {match_ids[0]}")

        # Update pact files with real PUUID
        print("Updating pact example data...")
        _root = Path(__file__).parent.parent
        for pact_file in _root.rglob("pacts/*.json"):
            _update_pact(pact_file, puuid)

        print(
            f"\nDone. Fixtures saved to {_FIXTURES.relative_to(Path(__file__).parent.parent)}/"
        )
        print("Re-run 'just contract' to verify all contract tests still pass.")

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
