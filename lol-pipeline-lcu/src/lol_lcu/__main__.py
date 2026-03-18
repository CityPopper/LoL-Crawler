"""Entry point: python -m lol_lcu."""

from __future__ import annotations

import argparse
import os

from lol_lcu.main import run


def main() -> None:
    parser = argparse.ArgumentParser(description="LCU Match History Collector")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("LCU_DATA_DIR", "lcu-data"),
        help="Directory for JSONL output files",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.environ.get("LCU_POLL_INTERVAL_MINUTES", "0")),
        help="Minutes between polls (0 = one-shot)",
    )
    args = parser.parse_args()
    run(data_dir=args.data_dir, poll_interval_minutes=args.poll_interval)


if __name__ == "__main__":
    main()
