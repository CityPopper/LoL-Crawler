# Consumer-Driven Contract Testing (CDCT)

This directory is the canonical schema source for the LoL Pipeline. All inter-service
message contracts use [Pact v3 Message Pacts](https://docs.pact.io/getting_started/how_pact_works#message-pacts).

---

## Service Isolation Principle

Each service knows only:
- **Its input** — the stream it consumes and the schema it expects
- **Its output** — the stream it produces and the schema it guarantees

Services have **no knowledge** of upstream producers, downstream consumers, pipeline topology,
or business logic of other services. This is enforced structurally: contract tests treat every
service as a black box verified only against its own inputs and outputs.

---

## Directory Structure

```
contracts/
├── README.md                              # this file
└── schemas/                               # canonical JSON Schema definitions (DRY source)
    ├── envelope.json                      # MessageEnvelope — all standard fields
    ├── dlq_envelope.json                  # DLQEnvelope — extends envelope, adds failure fields
    └── payloads/
        ├── puuid_payload.json             # stream:puuid payload
        ├── match_id_payload.json          # stream:match_id payload
        ├── parse_payload.json             # stream:parse payload
        └── analyze_payload.json           # stream:analyze payload
```

Per-service Pact files live in the **consumer's** repo under `pacts/`:

| Consumer                      | Provider                  | Pact File                                                           |
|-------------------------------|---------------------------|---------------------------------------------------------------------|
| `lol-pipeline-crawler`        | `lol-pipeline-seed`       | `lol-pipeline-crawler/pacts/crawler-seed.json`                      |
| `lol-pipeline-fetcher`        | `lol-pipeline-crawler`    | `lol-pipeline-fetcher/pacts/fetcher-crawler.json`                   |
| `lol-pipeline-parser`         | `lol-pipeline-fetcher`    | `lol-pipeline-parser/pacts/parser-fetcher.json`                     |
| `lol-pipeline-analyzer`       | `lol-pipeline-parser`     | `lol-pipeline-analyzer/pacts/analyzer-parser.json`                  |
| `lol-pipeline-recovery`       | `lol-pipeline-common`     | `lol-pipeline-recovery/pacts/recovery-common.json`                  |
| `lol-pipeline-delay-scheduler`| `lol-pipeline-common`     | `lol-pipeline-delay-scheduler/pacts/delay-scheduler-common.json`    |

> **Why `lol-pipeline-common` as provider for Recovery and Delay Scheduler?**
> `stream:dlq` messages and `delayed:messages` entries are produced by `nack_to_dlq()` and
> `requeue_delayed()` in `lol-pipeline-common/streams.py` — not by any individual service.
> The provider is the common library, not a specific upstream service.

---

## How Pact Works Here

### Consumer test — defines the contract

Runs in the consumer's repo. Generates the pact JSON file. Example (Crawler):

```python
# lol-pipeline-crawler/tests/contract/test_consumer.py
from pact import MessageConsumer, Provider

pact = MessageConsumer("lol-pipeline-crawler").has_pact_with(
    Provider("lol-pipeline-seed"),
    pact_dir="pacts",
)

def test_crawler_receives_puuid_message(pact):
    expected = {
        "id": "...",
        "source_stream": "stream:puuid",
        "type": "puuid",
        "payload": {"puuid": "...", "game_name": "...", "tag_line": "...", "region": "..."},
        "attempts": 0,
        "max_attempts": 5,
        "enqueued_at": "2024-01-01T00:00:00+00:00",
    }
    (
        pact
        .given("a player has been seeded successfully")
        .expects_to_receive("a player PUUID ready for crawling")
        .with_content(expected)
        .with_metadata({"contentType": "application/json"})
    )
    # assert the consumer can process this message without error
    result = CrawlerService().process_puuid_message(expected)
    assert result is not None
```

### Provider verification — validates the contract

Runs in the provider's repo. Loads the consumer's pact file from the sibling directory
(local dev) or Pact Broker (CI). Example (Seed verifying Crawler's contract):

```python
# lol-pipeline-seed/tests/contract/test_provider.py
from pact import MessageProvider

def produce_puuid_message():
    # Return an example message matching what Seed actually produces
    return {
        "id": str(uuid.uuid4()),
        "source_stream": "stream:puuid",
        "type": "puuid",
        "payload": {"puuid": "...", "game_name": "Faker", "tag_line": "KR1", "region": "kr"},
        "attempts": 0,
        "max_attempts": settings.MAX_ATTEMPTS,
        "enqueued_at": datetime.utcnow().isoformat() + "+00:00",
    }

provider = MessageProvider(
    provider="lol-pipeline-seed",
    consumer="lol-pipeline-crawler",
    pact_dir="../lol-pipeline-crawler/pacts",   # sibling repo in local dev
    message_providers={
        "a player PUUID ready for crawling": produce_puuid_message,
    },
)

def test_seed_satisfies_crawler_contract():
    provider.verify()
```

---

## Updating Contracts

When a **service is modified**, follow this checklist:

1. **If changing what a service produces (output changes):**
   - Update the consumer's pact file in the consumer's `pacts/` directory
   - Update the provider's message provider function in its `tests/contract/`
   - All provider verification tests must pass before merging

2. **If changing what a service consumes (input contract changes):**
   - Update the consumer test first — this defines the new expectation (red/green TDD)
   - The failing provider verification test is the signal the provider must update
   - Update the relevant JSON Schema in `contracts/schemas/` (source of truth)

3. **If changing the MessageEnvelope or DLQEnvelope structure:**
   - Update `contracts/schemas/envelope.json` or `dlq_envelope.json`
   - All affected pact files and provider tests must be updated
   - Bump `lol-pipeline-common` version per semver rules (see `docs/architecture/08-repo-structure.md`)

---

## Schema Reuse in Tests

The JSON schemas in `contracts/schemas/` are importable in test code for validation:

```python
import json
from pathlib import Path
from jsonschema import validate

SCHEMAS = Path(__file__).parent.parent.parent / "contracts" / "schemas"

def validate_puuid_payload(payload: dict) -> None:
    schema = json.loads((SCHEMAS / "payloads" / "puuid_payload.json").read_text())
    validate(instance=payload, schema=schema)
```

Use `jsonschema` (add to dev deps) for schema validation in provider tests to avoid
duplicating field-by-field assertions.

---

## Tools

| Tool                  | Purpose                                              |
|-----------------------|------------------------------------------------------|
| `pact-python>=2.0.0`  | Pact v3 consumer/provider message contract testing   |
| `jsonschema`          | JSON Schema validation of payloads in provider tests |

Both are in the `dev` extras of each service's `pyproject.toml`.
