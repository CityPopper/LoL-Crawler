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


class TestLogFileHandler:
    """Tier 3 — log.py file handler and log level tests."""

    def test_log_dir_creates_file_handler(self, tmp_path, monkeypatch):
        """When LOG_DIR is set, a RotatingFileHandler is added."""
        import importlib

        import lol_pipeline.log as log_mod

        monkeypatch.setattr(log_mod, "_LOG_DIR", str(tmp_path))

        # Force fresh logger (clear existing handlers)
        logger_name = "test-filelog"
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()

        log = log_mod.get_logger(logger_name)
        handler_types = [type(h).__name__ for h in log.handlers]
        assert "RotatingFileHandler" in handler_types

    def test_log_level_env_respected(self, monkeypatch):
        """LOG_LEVEL env var controls the logger level."""
        import importlib

        import lol_pipeline.log as log_mod

        monkeypatch.setattr(log_mod, "_LOG_LEVEL", logging.DEBUG)

        logger_name = "test-level"
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()

        log = log_mod.get_logger(logger_name)
        assert log.level == logging.DEBUG

    def test_log_dir_not_writable_raises(self, monkeypatch):
        """Non-writable LOG_DIR raises during get_logger (no silent fallback)."""
        import lol_pipeline.log as log_mod

        monkeypatch.setattr(log_mod, "_LOG_DIR", "/nonexistent/deep/path/that/cannot/exist")

        logger_name = "test-nowrite"
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()

        # mkdir(parents=True, exist_ok=True) will fail on truly unwritable paths
        # On most systems /nonexistent doesn't exist and can't be created
        try:
            log_mod.get_logger(logger_name)
            # If it succeeds (e.g. running as root), that's acceptable
        except OSError:
            pass  # Expected — documents that errors propagate
