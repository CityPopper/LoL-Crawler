# Source Waterfall — Remaining Work

This document tracks unimplemented items from the original source waterfall design. All core architecture, protocols, source implementations, and coordinator logic have been completed. Only the items below remain.

---

## Missing Features

### MF-1: `source:stats:{source_name}` Redis Hash
`source:stats:{source_name}` | Hash | no TTL | Fetch count, success count, throttle count per source. Written by WaterfallCoordinator. No implementation exists.

### MF-2: `WATERFALL_LOG_LEVEL` config variable
`WATERFALL_LOG_LEVEL` | str | default `"INFO"` | Log level for waterfall coordinator. Not in `config.py` or `.env.example`.

### MF-3: `admin blob-cleanup --older-than 90d` command
Add an `admin blob-cleanup --older-than 90d` command for disk blob lifecycle management. No such command exists.

### MF-4: `OpggSource` BUILD support incomplete
`OpggSource.supported_data_types = frozenset({MATCH})` only; no `OpggBuildExtractor` exists. The design showed `frozenset({MATCH, BUILD})`.

---

## Missing Tests

### MT-1: No integration test for `ExtractionError` in live fetch path
`tests/integration/test_it_wf_waterfall.py` (IT-WF-01 through IT-WF-04) does not test `ExtractionError` during a live source fetch.

### MT-2: Missing test fixture
`tests/unit/sources/opgg/fixtures/sample_match_blob.json` does not exist.
