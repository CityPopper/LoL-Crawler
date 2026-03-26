import os

import uvicorn

from lol_rate_limiter.main import app

_port = int(os.environ.get("RATE_LIMITER_PORT", "8079"))
_host = os.environ.get("RATE_LIMITER_HOST", "0.0.0.0")  # noqa: S104

uvicorn.run(app, host=_host, port=_port, log_level="info")
