# Container runtime: podman (default) or docker. Override: RUNTIME=docker just <recipe>
RUNTIME := env_var_or_default("RUNTIME", "podman")
DC      := RUNTIME + " compose"
# Compose project name (used for network name: <project>_default)
PROJECT := env_var_or_default("COMPOSE_PROJECT_NAME", "lol-crawler")
# Podman stores locally-built images under localhost/; Docker does not
_image_prefix := if RUNTIME == "podman" { "localhost/" } else { "" }
# Docker Compose v2 uses hyphens ({project}-{service}-{N}); Podman uses underscores
_redis_ctr := if RUNTIME == "podman" { PROJECT + "_redis_1" } else { PROJECT + "-redis-1" }

# List available recipes
default:
    @just --list

# 1. Copy .env.example → .env (edit it before building)
setup:
    #!/usr/bin/env bash
    set -euo pipefail
    PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 14 ]; }; then
        echo "ERROR: Python 3.14+ required (found $PY_VERSION)" >&2
        exit 1
    fi
    if ! command -v {{RUNTIME}} &>/dev/null; then
        echo "ERROR: '{{RUNTIME}}' not found. Install it or set RUNTIME=docker." >&2
        exit 1
    fi
    if [ ! -f .env ]; then
        cp .env.example .env
        echo "Created  .env — set RIOT_API_KEY before running 'just build'."
    else
        echo ".env already exists, skipping."
    fi
    if command -v pre-commit &>/dev/null; then
        pre-commit install
        echo "Pre-commit hooks installed."
    else
        echo "pre-commit not found — run 'pip install pre-commit && pre-commit install' to enable hooks."
    fi

# Create and populate a root-level virtual environment
venv:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -e "lol-pipeline-common/[dev]"
    for dir in lol-pipeline-*/; do
        if [ "$dir" != "lol-pipeline-common/" ] && [ -f "$dir/pyproject.toml" ]; then
            .venv/bin/pip install -e "$dir[dev]" 2>/dev/null || .venv/bin/pip install -e "$dir" || true
        fi
    done
    echo "Venv ready — activate with: source .venv/bin/activate"

# 2. Build all Docker images (including one-shot tools)
build:
    {{DC}} build
    {{RUNTIME}} build -f lol-pipeline-seed/Dockerfile  -t {{PROJECT}}-seed:latest  .
    {{RUNTIME}} build -f lol-pipeline-admin/Dockerfile -t {{PROJECT}}-admin:latest .

# 3. Start all services (Redis + workers); idempotent
run:
    {{DC}} up -d

# Setup + build + run in one step
up: setup build run

# Internal: ensure the stack is running; start it automatically if not
_ensure_up:
    #!/usr/bin/env bash
    set -euo pipefail
    DC="{{DC}}"
    # Probe Redis directly — works with both docker compose and podman-compose
    if ! $DC exec -T redis redis-cli ping &>/dev/null 2>&1; then
        echo "Stack not running — starting..."
        $DC up -d
        echo "Waiting for Redis to be healthy..."
        for i in $(seq 1 20); do
            $DC exec -T redis redis-cli ping &>/dev/null 2>&1 && break
            sleep 1
        done
    fi

# Seed a player into the pipeline (auto-starts stack if needed)
# e.g. just seed "Faker#KR1" kr
seed riot_id region="na1": _ensure_up
    #!/usr/bin/env bash
    set -euo pipefail
    # Run directly from image — packages are baked in; avoids compose profile limitations
    {{RUNTIME}} run --rm \
        --network "{{PROJECT}}_default" \
        --env-file .env \
        {{_image_prefix}}{{PROJECT}}-seed:latest \
        python -m lol_seed "{{riot_id}}" "{{region}}"

# Stop all services (containers paused, data preserved)
stop:
    {{DC}} stop

# Remove containers (data volumes preserved)
down:
    {{DC}} down

# Remove containers AND wipe all Redis data
reset:
    {{DC}} down -v

# Tail logs for a service (e.g. just logs fetcher)
logs svc:
    {{DC}} logs -f {{svc}}

# Tail merged logs from all services
logs-all:
    {{DC}} logs -f

# Restart a single service (e.g. just restart crawler)
restart svc:
    {{DC}} restart {{svc}}

# Open an interactive redis-cli session inside the Redis container
redis-cli:
    {{RUNTIME}} exec -it {{_redis_ctr}} redis-cli

# Scale a service (e.g. just scale fetcher 3)
scale svc count:
    {{DC}} up --scale {{svc}}={{count}} -d

# Internal: print stream depths + system:halted (used by streams and status)
_stream_depths:
    #!/usr/bin/env bash
    set -euo pipefail
    REDIS_CTR="{{_redis_ctr}}"
    exec_redis() { {{RUNTIME}} exec "$REDIS_CTR" redis-cli "$@"; }
    printf "%-24s %s\n" "Stream"             "Depth"
    printf "%-24s %s\n" "──────────────────────" "──────"
    printf "%-24s %s\n" "stream:puuid:"      "$(exec_redis XLEN stream:puuid)"
    printf "%-24s %s\n" "stream:match_id:"   "$(exec_redis XLEN stream:match_id)"
    printf "%-24s %s\n" "stream:parse:"      "$(exec_redis XLEN stream:parse)"
    printf "%-24s %s\n" "stream:analyze:"    "$(exec_redis XLEN stream:analyze)"
    printf "%-24s %s\n" "stream:dlq:"        "$(exec_redis XLEN stream:dlq)"
    printf "%-24s %s\n" "stream:dlq:archive:" "$(exec_redis XLEN stream:dlq:archive)"
    printf "%-24s %s\n" "delayed:messages:"  "$(exec_redis ZCARD delayed:messages)"
    printf "%-24s %s\n" "discover:players:"  "$(exec_redis ZCARD discover:players)"
    priority_count=$(exec_redis --no-auth-warning SCAN 0 MATCH "player:priority:*" COUNT 10000 | tail -n +2 | grep -v "^0$" | wc -w | tr -d ' ')
    printf "%-24s %s\n" "player:priority:*:" "$priority_count"
    echo ""
    printf "%-32s %s\n" "PEL (pending)"      "Pending"
    printf "%-32s %s\n" "──────────────────────────────" "──────"
    for stream in stream:puuid stream:match_id stream:parse stream:analyze stream:dlq; do
        groups=$(exec_redis XINFO GROUPS "$stream" 2>/dev/null) || continue
        echo "$groups" | awk -v stream="$stream" '
            /^name$/ { getline; grp=$0 }
            /^pel-count$/ { getline; printf "%-32s %s\n", stream " / " grp ":", $0 }
        '
    done
    echo ""
    halted=$(exec_redis GET system:halted)
    if [ "$halted" = "1" ]; then
        printf "%-24s %s\n" "system:halted" "*** HALTED ***"
    else
        printf "%-24s %s\n" "system:halted" "running"
    fi

# Show Redis stream depths
streams: _stream_depths

# Show a dashboard: container health, stream depths, DLQ depth, halt flag, last 3 log lines
status:
    #!/usr/bin/env bash
    set -euo pipefail
    DC="{{DC}}"

    echo "=== Container health ==="
    $DC ps

    echo ""
    echo "=== Stream depths ==="
    just _stream_depths

    echo ""
    echo "=== Last 3 log lines per service ==="
    for svc in crawler fetcher parser analyzer recovery delay-scheduler discovery ui; do
        echo "--- $svc ---"
        $DC logs --tail=3 "$svc" 2>/dev/null || true
    done

# Run an admin command (auto-starts stack if needed)
# e.g. just admin stats Faker#KR1
admin *args: _ensure_up
    #!/usr/bin/env bash
    set -euo pipefail
    # Run directly from image — packages are baked in; avoids compose profile limitations
    {{RUNTIME}} run --rm \
        --network "{{PROJECT}}_default" \
        --env-file .env \
        {{_image_prefix}}{{PROJECT}}-admin:latest \
        python -m lol_admin {{args}}

# Open the web UI in the default browser
ui:
    @echo "Web UI: http://localhost:8080"
    @xdg-open http://localhost:8080 2>/dev/null || open http://localhost:8080 2>/dev/null || true

# Lint all service repos (ruff check + format check)
lint:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in lol-pipeline-*/; do
        if [ -f "$dir/pyproject.toml" ]; then
            echo "=== $dir ===" && (cd "$dir" && ruff check . && ruff format --check .)
        fi
    done

# Auto-fix lint issues and format all service repos
fix:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in lol-pipeline-*/; do
        if [ -f "$dir/pyproject.toml" ]; then
            echo "=== $dir ===" && (cd "$dir" && ruff check --fix . && ruff format .)
        fi
    done

# Format all service repos (ruff format)
format:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in lol-pipeline-*/; do
        if [ -f "$dir/pyproject.toml" ]; then
            echo "=== $dir ===" && (cd "$dir" && ruff format .)
        fi
    done

# Type-check all service repos (mypy on src/ only)
typecheck:
    #!/usr/bin/env bash
    set -euo pipefail
    COMMON_SRC="$PWD/lol-pipeline-common/src"
    for dir in lol-pipeline-*/; do
        if [ -f "$dir/pyproject.toml" ]; then
            echo "=== $dir ===" && (cd "$dir" && MYPYPATH="$COMMON_SRC" mypy src/)
        fi
    done

# Lint + type-check all service repos (parallel)
check:
    #!/usr/bin/env bash
    set -euo pipefail
    OUTTMP=$(mktemp -d)
    (just lint       > "$OUTTMP/lint.out"      2>&1; echo $? > "$OUTTMP/lint.rc")      &
    (just typecheck  > "$OUTTMP/typecheck.out"  2>&1; echo $? > "$OUTTMP/typecheck.rc") &
    wait
    echo "=== lint ===" && cat "$OUTTMP/lint.out"
    echo "=== typecheck ===" && cat "$OUTTMP/typecheck.out"
    FAILED=0
    [ "$(cat "$OUTTMP/lint.rc")" -ne 0 ]      && FAILED=1
    [ "$(cat "$OUTTMP/typecheck.rc")" -ne 0 ]  && FAILED=1
    rm -rf "$OUTTMP"
    exit $FAILED

# Lint a single service (e.g. just lint-svc crawler)
lint-svc name:
    cd lol-pipeline-{{name}} && ruff check . && ruff format --check .

# Type-check a single service (e.g. just typecheck-svc crawler)
typecheck-svc name:
    #!/usr/bin/env bash
    set -euo pipefail
    COMMON_SRC="$PWD/lol-pipeline-common/src"
    cd lol-pipeline-{{name}} && MYPYPATH="$COMMON_SRC" mypy src/

# Quick post-change validation without API key (lint + typecheck + unit tests)
smoke: lint typecheck test

# Update API mock fixtures from live Riot API (uses Pwnerer#1337)
update-mocks:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Fetching live data for Pwnerer#1337..."
    python3 scripts/update_mocks.py

# Run integration tests (requires Docker or Podman for testcontainers)
integration:
    python3 -m pytest tests/integration/ -v

# Run end-to-end tests (requires running stack with valid API key)
e2e:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest tests/e2e/ -v

# Run all unit tests (services tested in parallel)
test:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f ".venv/bin/python" ] && .venv/bin/python -c "import pytest" 2>/dev/null; then
        PYTEST="$(pwd)/.venv/bin/python -m pytest"
    else
        PYTEST="$(command -v python3) -m pytest"
    fi
    PIDS=()
    NAMES=()
    OUTTMP=$(mktemp -d)
    for dir in lol-pipeline-*/; do
        name="${dir%/}"
        if [ -d "$dir/tests/unit" ]; then
            NAMES+=("$name")
            (cd "$dir" && RC=0 && $PYTEST tests/unit -q --tb=short > "$OUTTMP/$name.out" 2>&1 || RC=$?; echo $RC > "$OUTTMP/$name.rc") &
            PIDS+=($!)
        fi
    done
    FAILED=0
    for i in "${!PIDS[@]}"; do
        wait "${PIDS[$i]}" || true
        RC=$(cat "$OUTTMP/${NAMES[$i]}.rc")
        echo "=== ${NAMES[$i]} ==="
        cat "$OUTTMP/${NAMES[$i]}.out"
        [ "$RC" -ne 0 ] && FAILED=1
    done
    rm -rf "$OUTTMP"
    exit $FAILED

# Run unit tests for a single service (e.g. just test-svc crawler)
test-svc name:
    #!/usr/bin/env bash
    set -euo pipefail
    PYTEST="python3 -m pytest"
    if [ -f ".venv/bin/python" ]; then PYTEST=".venv/bin/python -m pytest"; fi
    $PYTEST lol-pipeline-{{name}}/tests/unit -v --tb=short

# Run UI in dev mode with hot reload (host, no Docker)
dev-ui:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    else
        PYTHON="$(command -v python3)"
    fi
    export PYTHONPATH="$PWD/lol-pipeline-common/src:$PWD/lol-pipeline-ui/src${PYTHONPATH:+:$PYTHONPATH}"
    exec $PYTHON -m uvicorn lol_ui.main:app --reload --host 127.0.0.1 --port 8080

# Run all unit + contract tests
test-all: test contract

# Run all unit tests with coverage report
coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -f ".venv/bin/python" ] && .venv/bin/python -c "import pytest" 2>/dev/null; then
        PYTEST="$(pwd)/.venv/bin/python -m pytest"
    else
        PYTEST="$(command -v python3) -m pytest"
    fi
    for dir in lol-pipeline-*/; do
        name="${dir%/}"
        short="${name#lol-pipeline-}"
        if [ -d "$dir/tests/unit" ] && [ -d "$dir/src" ]; then
            pkg=$(ls "$dir/src/")
            echo "=== $name ==="
            (cd "$dir" && $PYTEST tests/unit --cov="src/$pkg" --cov-report=term-missing -q --tb=short)
        fi
    done

# Consolidate individual match JSON files into JSONL+zstd bundles
consolidate:
    #!/usr/bin/env bash
    set -euo pipefail
    PYTHON="python3"
    if [ -f ".venv/bin/python" ]; then PYTHON=".venv/bin/python"; fi
    $PYTHON scripts/consolidate_match_data.py --delete-originals

# Run contract tests for all services
contract:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in lol-pipeline-*/; do
        if [ -f "$dir/pyproject.toml" ] && [ -d "$dir/tests/contract" ]; then
            echo "=== $dir ===" && (cd "$dir" && python3 -m pytest tests/contract/ -v)
        fi
    done
