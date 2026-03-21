# Coding Standards

## Goals

| Goal | Rationale |
|------|-----------|
| **Reduce cyclomatic complexity** | High branch counts make code hard to test exhaustively and easy to misread. Each additional path doubles the number of test cases required to achieve full coverage. |
| **Reduce cognitive complexity** | Code that requires the reader to hold many mental states simultaneously causes bugs at review time. Flat code with early returns is easier to reason about than deeply nested code. |
| **Increase security posture** | A pipeline that touches an external API key, user-supplied inputs, and a persistent data store is an attractive target. Automated security scanning catches common vulnerabilities before they reach production. |
| **Enforce type safety** | This is a message-passing pipeline: type mismatches in serialized envelopes cause silent data corruption downstream, not loud crashes. Strict typing catches interface drift at compile time. |

---

## Tooling

| Tool | Role | Run with |
|------|------|----------|
| `ruff` | Linter + formatter (replaces flake8, isort, black, bandit) | `ruff check .` / `ruff format .` |
| `mypy` | Static type checker | `mypy src/` |

Both tools are configured in `pyproject.toml` under `[tool.ruff]` and `[tool.mypy]`.
Both are required in every service's `dev` extras.
CI enforces both — a lint or type failure blocks merge.

---

## Configuration

> **Source of truth:** Each service's `pyproject.toml`. The config below is the canonical
> template — all services must stay in sync. When changing a value, update all services and
> this doc together.

```toml
[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "W",    # pycodestyle warnings
    "F",    # pyflakes — undefined names, unused imports
    "I",    # isort — deterministic import ordering
    "B",    # flake8-bugbear — likely bugs and design issues
    "C90",  # McCabe cyclomatic complexity
    "UP",   # pyupgrade — enforce modern Python 3.14 idioms
    "N",    # pep8-naming — consistent naming conventions
    "S",    # flake8-bandit — security vulnerability scanning
    "ANN",  # flake8-annotations — require type annotations
    "SIM",  # flake8-simplify — reduce unnecessary verbosity
    "PLR",  # pylint refactoring — branch/statement/arg limits
    "RUF",  # ruff-native rules
]
ignore = [
    "ANN401",  # `Any` allowed in serialization/middleware boundary code
    "PLR2004", # magic values — HTTP status codes, stream lengths are idiomatic
]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.pylint]
max-branches = 12
max-statements = 50
max-args = 7
max-returns = 6

[tool.ruff.lint.per-file-ignores]
"tests/**" = [
    "S101",  # assert statements are expected in tests
    "ANN",   # type annotations are not required in test code
    "SIM",   # simplification hints are not useful in test helpers
]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.mypy]
python_version = "3.14"
strict = true
warn_return_any = true
warn_unused_ignores = true
```

---

## Complexity Limits

All thresholds are configured in each service's `pyproject.toml` under `[tool.ruff.lint.mccabe]`
and `[tool.ruff.lint.pylint]`. The values below are the canonical defaults.

### Cyclomatic Complexity (McCabe) — `C901`, max 10

Each `if`, `elif`, `for`, `while`, `except`, `with`, `assert`, boolean operator, or
comprehension adds 1 to the complexity score. A score of 10 is the industry-standard
threshold for "acceptable"; above 10 requires refactoring into smaller functions.

**How to fix high complexity:**
- Extract conditional blocks into named helper functions
- Replace multi-branch `if/elif` chains with dispatch dicts or early returns
- Flatten nested loops into generators

### Cognitive Complexity — enforced via PLR (pylint refactoring) rules

The `PLR` rule set enforces structural complexity limits that directly target cognitive load:

| Rule | Limit | Description |
|------|-------|-------------|
| `PLR0912` | max-branches = 12 | Too many branches (if/elif/else) |
| `PLR0915` | max-statements = 50 | Too many statements in a function |
| `PLR0913` | max-args = 7 | Too many function arguments |
| `PLR0911` | max-returns = 6 | Too many return statements |

Combined with:

| Constraint | Why |
|------------|-----|
| Max line length: 100 | Forces expression decomposition |
| McCabe max: 10 | Limits decision paths |
| `SIM` rules enabled | Eliminates unnecessary `if`/`else` branches |
| `B` rules enabled | Catches anti-patterns that increase cognitive load |

**Target function length:** ≤ 40 lines of code (excluding blank lines and comments).
Functions over 40 lines should be reviewed for extraction. This is a guideline, not
enforced by a linter rule — use judgment.

**Nesting depth:** ≤ 3 levels. Deeply nested code is a design smell; use early returns
and guard clauses instead.

### Overriding Complexity Rules

When a function legitimately exceeds a limit (e.g., a parser with many branches):

**Per-line override** — suppress a single violation:
```python
def complex_parser(data: dict) -> Result:  # noqa: C901, PLR0912
    ...
```

**Per-file override** — in `pyproject.toml`:
```toml
[tool.ruff.lint.per-file-ignores]
"src/lol_parser/main.py" = ["PLR0912"]
```

**Guidelines for overrides:**
- Always include a comment explaining *why* the override is needed
- Prefer refactoring over overriding — overrides are a last resort
- Overrides do not exempt code from review

---

## Security Rules (S / bandit)

The `S` ruleset embeds the bandit security scanner. Key rules enforced:

| Rule | Description |
|------|-------------|
| S105, S106 | No hardcoded passwords or API keys in source |
| S108 | No `/tmp` usage (insecure temp files) |
| S311 | No insecure random for security-sensitive operations |
| S324 | No MD5/SHA1 for security-sensitive hashing |
| S501 | No SSL verification disabled |
| S603, S607 | No subprocess with shell injection risk |

**Project-specific security requirements:**
- `RIOT_API_KEY` must only ever be read from `os.environ` / pydantic-settings
- No Redis keys may be constructed from raw user input without sanitization
- All external HTTP calls must go through the `RiotClient` (rate-limited, authenticated)
- TLS must be enabled in all production Redis connections

---

## Type Annotation Requirements

`mypy --strict` enforces:
- All function parameters annotated
- All return types annotated
- No implicit `Any` except where explicitly suppressed with `# type: ignore[misc]`
- No untyped function bodies

**Preferred annotation patterns for this project:**

```python
# Payload dicts use TypedDict for structured shapes
class PuuidPayload(TypedDict):
    puuid: str
    game_name: str
    tag_line: str
    region: str

# Redis field dicts use dict[str, str] (all values are strings in Redis)
def to_redis_fields(self) -> dict[str, str]: ...

# Optional fields use X | None (not Optional[X])
def get_match(self, match_id: str, region: str) -> dict[str, Any] | None: ...

# Async functions annotated with Coroutine return types
async def consume(...) -> list[MessageEnvelope]: ...
```

---

## Import Ordering

The `I` (isort) ruleset enforces this order:
1. Standard library (`import json`, `from pathlib import Path`)
2. Third-party packages (`import redis`, `from pydantic_settings import BaseSettings`)
3. First-party (`from lol_pipeline.models import MessageEnvelope`)
4. Relative (`from .conftest import load_pact`)

Blank line between each group. Alphabetical within each group.

---

## Naming Conventions

| Construct | Convention | Example |
|-----------|------------|---------|
| Module-level constants | `SCREAMING_SNAKE_CASE` | `MAX_ATTEMPTS = 5` |
| Private module-level | `_SCREAMING_SNAKE_CASE` | `_LUA_SCRIPT = "..."` |
| Functions / methods | `snake_case` | `def acquire_token()` |
| Classes | `PascalCase` | `class MessageEnvelope` |
| Type aliases | `PascalCase` | `RedisFields = dict[str, str]` |
| Test functions | `test_{subject}__{scenario}__[outcome]` | `test_seed__within_cooldown__skips_publish` |

---

## Running Locally

```bash
# From any service directory:
ruff check .              # lint check
ruff format --check .     # format check (non-destructive)
ruff format .             # apply formatting
mypy src/                 # type check

# From the repo root (all services):
just lint
just typecheck
```

---

## CI Enforcement

Every service's CI pipeline runs in this order:

1. `pytest tests/unit --cov` — unit tests
2. `pytest tests/contract -x` — contract tests
3. `pytest tests/integration` — integration tests
4. `ruff check . && ruff format --check .` — lint
5. `mypy src/` — type check

**Lint and type failures block the Docker build step.** A failing lint is treated the
same as a failing test — the PR is not mergeable.
