DC      := "docker compose"
LCU_DIR := "lol-pipeline-lcu"

# List available recipes
default:
    @just --list

# 1. Copy .env.example → .env (edit it before building)
setup:
    #!/usr/bin/env bash
    set -euo pipefail
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

# 2. Build all Docker images
build:
    {{DC}} build

# 3. Start all services (Redis + workers); idempotent
run:
    {{DC}} up -d

# Setup + build + run in one step
up: setup build run

# Internal: ensure the stack is running; start it automatically if not
_ensure_up:
    #!/usr/bin/env bash
    set -euo pipefail
    STATUS=$(docker compose ps --format '{{{{.Status}}}}' redis 2>/dev/null | head -1)
    if [[ "$STATUS" != "Up"* ]]; then
        echo "Stack not running — starting..."
        docker compose up -d
        echo "Waiting for Redis to be healthy..."
        for i in $(seq 1 20); do
            STATUS=$(docker compose ps --format '{{{{.Status}}}}' redis 2>/dev/null | head -1)
            [[ "$STATUS" == *"healthy"* ]] && break
            sleep 1
        done
    fi

# Seed a player into the pipeline (auto-starts stack if needed)
# e.g. just seed "Faker#KR1" kr
seed riot_id region="na1": _ensure_up
    {{DC}} run --rm seed "{{riot_id}}" "{{region}}"

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

# Restart a single service (e.g. just restart crawler)
restart svc:
    {{DC}} restart {{svc}}

# Scale a service (e.g. just scale fetcher 3)
scale svc count:
    {{DC}} up --scale {{svc}}={{count}} -d

# Show Redis stream depths
streams:
    #!/usr/bin/env bash
    set -euo pipefail
    exec_redis() { {{DC}} exec -T redis redis-cli "$@"; }
    printf "%-24s %s\n" "stream:puuid:"      "$(exec_redis XLEN stream:puuid)"
    printf "%-24s %s\n" "stream:match_id:"   "$(exec_redis XLEN stream:match_id)"
    printf "%-24s %s\n" "stream:parse:"      "$(exec_redis XLEN stream:parse)"
    printf "%-24s %s\n" "stream:analyze:"    "$(exec_redis XLEN stream:analyze)"
    printf "%-24s %s\n" "stream:dlq:"        "$(exec_redis XLEN stream:dlq)"
    printf "%-24s %s\n" "stream:dlq:archive:" "$(exec_redis XLEN stream:dlq:archive)"
    printf "%-24s %s\n" "delayed:messages:"  "$(exec_redis ZCARD delayed:messages)"
    printf "%-24s %s\n" "system:halted:"     "$(exec_redis GET system:halted)"

# Run an admin command (auto-starts stack if needed)
# e.g. just admin stats Faker#KR1
admin *args: _ensure_up
    {{DC}} run --rm admin {{args}}

# Collect LCU match history (one-shot). On WSL, runs via Windows Python (the LCU
# only accepts connections from Windows localhost — Docker and WSL2 get 403).
# On native Windows/macOS, runs locally. Set LEAGUE_INSTALL_PATH in .env.
lcu:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "${LEAGUE_INSTALL_PATH:-}" ] && [ -f ".env" ]; then
        set -a; source ".env"; set +a
    fi
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "WSL detected — running LCU collector via Windows Python (LCU only accepts localhost)..."
        WIN_PROJECT=$(wslpath -w "$(pwd)/{{LCU_DIR}}")
        WIN_DATA=$(wslpath -w "$(pwd)/{{LCU_DIR}}/lcu-data")
        WIN_INSTALL=$(wslpath -w "${LEAGUE_INSTALL_PATH}")
        powershell.exe -Command "cd '${WIN_PROJECT}'; pip install -q -e .; \$env:LEAGUE_INSTALL_PATH='${WIN_INSTALL}'; \$env:LCU_HOST='127.0.0.1'; python -m lol_lcu --data-dir '${WIN_DATA}'"
    else
        cd {{LCU_DIR}} && pip install -q -e . && python3 -m lol_lcu --data-dir lcu-data
    fi

# Continuously collect LCU match history, polling every LCU_POLL_INTERVAL_MINUTES minutes (default: 5).
# UI reloads data automatically when LCU_POLL_INTERVAL_MINUTES > 0.
lcu-watch:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "${LEAGUE_INSTALL_PATH:-}" ] && [ -f ".env" ]; then
        set -a; source ".env"; set +a
    fi
    POLL="${LCU_POLL_INTERVAL_MINUTES:-5}"
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "WSL detected — running LCU collector via Windows Python (LCU only accepts localhost)..."
        WIN_PROJECT=$(wslpath -w "$(pwd)/{{LCU_DIR}}")
        WIN_DATA=$(wslpath -w "$(pwd)/{{LCU_DIR}}/lcu-data")
        WIN_INSTALL=$(wslpath -w "${LEAGUE_INSTALL_PATH}")
        powershell.exe -Command "cd '${WIN_PROJECT}'; pip install -q -e .; \$env:LEAGUE_INSTALL_PATH='${WIN_INSTALL}'; \$env:LCU_HOST='127.0.0.1'; python -m lol_lcu --data-dir '${WIN_DATA}' --poll-interval ${POLL}"
    else
        cd {{LCU_DIR}} && pip install -q -e . && python3 -m lol_lcu --data-dir lcu-data --poll-interval "$POLL"
    fi

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

# Lint + type-check all service repos
check: lint typecheck

# Update API mock fixtures from live Riot API (uses Pwnerer#1337)
update-mocks:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Fetching live data for Pwnerer#1337..."
    python3 scripts/update_mocks.py

# Run integration tests (requires Docker for testcontainers)
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
    PYTEST="python3 -m pytest"
    if [ -f ".venv/bin/python" ]; then PYTEST=".venv/bin/python -m pytest"; fi
    PIDS=()
    NAMES=()
    TMPDIR=$(mktemp -d)
    for dir in lol-pipeline-*/; do
        name="${dir%/}"
        if [ -d "$dir/tests/unit" ]; then
            NAMES+=("$name")
            (cd "$dir" && ../$PYTEST tests/unit -q --tb=short > "$TMPDIR/$name.out" 2>&1; echo $? > "$TMPDIR/$name.rc") &
            PIDS+=($!)
        fi
    done
    FAILED=0
    for i in "${!PIDS[@]}"; do
        wait "${PIDS[$i]}" || true
        RC=$(cat "$TMPDIR/${NAMES[$i]}.rc")
        echo "=== ${NAMES[$i]} ==="
        cat "$TMPDIR/${NAMES[$i]}.out"
        [ "$RC" -ne 0 ] && FAILED=1
    done
    rm -rf "$TMPDIR"
    exit $FAILED

# Run unit tests for a single service (e.g. just test-svc crawler)
test-svc name:
    #!/usr/bin/env bash
    set -euo pipefail
    PYTEST="python3 -m pytest"
    if [ -f ".venv/bin/python" ]; then PYTEST=".venv/bin/python -m pytest"; fi
    $PYTEST lol-pipeline-{{name}}/tests/unit -v --tb=short

# Run all unit + contract tests
test-all: test contract

# Run all unit tests with coverage report
coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    PYTEST="python3 -m pytest"
    if [ -f ".venv/bin/python" ]; then PYTEST=".venv/bin/python -m pytest"; fi
    for dir in lol-pipeline-*/; do
        name="${dir%/}"
        short="${name#lol-pipeline-}"
        if [ -d "$dir/tests/unit" ] && [ -d "$dir/src" ]; then
            pkg=$(ls "$dir/src/")
            echo "=== $name ==="
            (cd "$dir" && ../$PYTEST tests/unit --cov="src/$pkg" --cov-report=term-missing -q --tb=short)
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
