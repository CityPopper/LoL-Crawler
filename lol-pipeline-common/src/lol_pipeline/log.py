"""Structured JSON logging — must NOT be named logging.py (shadows stdlib)."""

import json
import logging
import os
import pathlib
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

# Read from env vars whose defaults match Config.log_level / Config.log_dir.
_LOG_LEVEL: int = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
_LOG_DIR: str | None = os.environ.get("LOG_DIR") or None

_LOG_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
        data: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _LOG_RESERVED and not key.startswith("_"):
                data.setdefault(key, value)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data)


def get_logger(name: str) -> logging.Logger:
    """Return a logger that emits structured JSON to stdout (and optionally a file).

    If LOG_DIR is set, logs are also written to {LOG_DIR}/{name}.log.
    Log level is controlled by LOG_LEVEL (default: INFO).
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        formatter = _JsonFormatter()

        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(formatter)
        logger.addHandler(stdout_handler)

        if _LOG_DIR:
            pathlib.Path(_LOG_DIR).mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                f"{_LOG_DIR}/{name}.log",
                maxBytes=150 * 1024 * 1024,
                backupCount=3,
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        logger.setLevel(_LOG_LEVEL)
        logger.propagate = False
    return logger
