"""python -m lol_seed entry point."""

import asyncio
import sys

from lol_seed.main import main

sys.exit(asyncio.run(main(sys.argv)))
