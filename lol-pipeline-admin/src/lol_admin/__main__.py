"""python -m lol_admin entry point."""

import asyncio
import sys

from lol_admin.main import main

sys.exit(asyncio.run(main(sys.argv)))
