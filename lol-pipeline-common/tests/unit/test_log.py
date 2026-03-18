"""Unit tests for lol_pipeline.log."""

from __future__ import annotations

import json
import logging


class TestGetLogger:
    def test_returns_logger_with_name(self):
        from lol_pipeline.log import get_logger

        log = get_logger("test-svc")
        assert log.name == "test-svc"
        assert isinstance(log, logging.Logger)

    def test_json_format_stdout(self, capsys):
        from lol_pipeline.log import get_logger

        log = get_logger("test-json")
        log.info("hello world")
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert data["logger"] == "test-json"
        assert "timestamp" in data

    def test_extra_fields_in_output(self, capsys):
        from lol_pipeline.log import get_logger

        log = get_logger("test-extra")
        log.info("with extras", extra={"puuid": "abc123", "count": 5})
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["puuid"] == "abc123"
        assert data["count"] == 5

    def test_idempotent_handlers(self):
        """Calling get_logger twice returns the same logger without duplicating handlers."""
        from lol_pipeline.log import get_logger

        log1 = get_logger("test-idem")
        n = len(log1.handlers)
        log2 = get_logger("test-idem")
        assert log1 is log2
        assert len(log2.handlers) == n

    def test_propagate_disabled(self):
        from lol_pipeline.log import get_logger

        log = get_logger("test-prop")
        assert log.propagate is False
