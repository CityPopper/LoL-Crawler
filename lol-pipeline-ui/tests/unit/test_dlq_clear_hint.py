"""STRUCT-3: DLQ replay removed from UI — now in admin-ui."""

from __future__ import annotations


class TestDlqCorruptEntryHint:
    """STRUCT-3: DLQ replay endpoint removed from UI (moved to admin-ui)."""

    def test_dlq_replay__not_in_ui(self):
        """dlq_replay endpoint no longer exists in the read-only UI."""
        from lol_ui.routes import dlq

        assert not hasattr(dlq, "dlq_replay")

    def test_dlq_module__no_replay_import(self):
        """dlq module does not import replay_from_dlq."""
        import inspect

        from lol_ui.routes import dlq

        source = inspect.getsource(dlq)
        assert "replay_from_dlq" not in source
