"""Tests for LCU standalone logger."""

import json
import logging

from lol_lcu.log import get_logger


class TestLogger:
    """Tests for the JSON-format logger."""

    def test_returns_logger(self):
        logger = get_logger("test")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test"

    def test_json_format(self, capsys):
        logger = get_logger("test_json", level=logging.INFO)
        # Remove existing handlers to isolate test
        logger.handlers.clear()
        handler = logging.StreamHandler()
        from lol_lcu.log import JsonFormatter
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.info("hello world")
        captured = capsys.readouterr()
        data = json.loads(captured.err)
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert "timestamp" in data
