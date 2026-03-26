# Container runtime: podman if available, otherwise docker. Override: RUNTIME=docker just <recipe>
RUNTIME := env_var_or_default("RUNTIME", if `command -v podman >/dev/null 2>&1 && echo found || echo missing` == "found" { "podman" } else { "docker" })
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


# 2. Build all Docker images (including one-shot tools)
build:
    {{DC}} build
    {{RUNTIME}} build -f Dockerfile.service --build-arg SERVICE_NAME=admin --build-arg MODULE_NAME=lol_admin -t {{PROJECT}}-admin:latest .
    {{RUNTIME}} build -f Dockerfile.service --build-arg SERVICE_NAME=admin-ui --build-arg MODULE_NAME=lol_admin_ui -t {{PROJECT}}-admin-ui:latest .

# 3. Start all services (Redis + workers); idempotent
run:
    {{DC}} up -d

# Setup + build + run in one step (downloads seed data and auto-seeds Redis on fresh clone)
up: setup build _seed-data-check _aof-cleanup _decompress-current-month run _auto-seed

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
seed riot_id region="na1":
    @echo "DEPRECATED: use 'just admin track {{riot_id}} --region {{region}}' instead"
    just admin track "{{riot_id}}" --region "{{region}}"

# Stop all services (containers paused, data preserved)
stop:
    {{DC}} stop

# Remove containers (data volumes preserved)
down:
    {{DC}} down

# Remove containers AND wipe all Redis data
reset:
    {{DC}} down -v

# Compress active .jsonl data files to .zst for upload (run before `just upload`)
compact-data:
    #!/usr/bin/env bash
    set -euo pipefail
    DATA_DIR="pipeline-data/riot-api/NA1"
    count=0
    for f in "$DATA_DIR"/*.jsonl; do
        [ -f "$f" ] || continue
        out="${f%.jsonl}.jsonl.zst"
        echo "Compressing $(basename "$f")..."
        python3 -c "import zstandard,pathlib,sys;s=pathlib.Path(sys.argv[1]);d=pathlib.Path(sys.argv[2]);d.write_bytes(zstandard.ZstdCompressor(level=19).compress(s.read_bytes()))" "$f" "$out"
        rm "$f"
        echo "  -> $(basename "$out")"
        count=$((count + 1))
    done
    if [ "$count" -eq 0 ]; then
        echo "No .jsonl files to compact."
    else
        echo "Compacted $count file(s). Run 'just upload' to push to HF."
    fi

# Download seed data from Hugging Face Datasets
download:
    python3 scripts/download_seed.py

# Upload seed data to Hugging Face Datasets (run 'just compact-data' first)
upload:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Step 1/2: Compacting active .jsonl files..."
    just compact-data
    echo "Step 2/2: Uploading to Hugging Face..."
    python3 scripts/anonymize_and_upload.py
    echo ""
    echo "Upload complete. To regenerate dump.rdb, see workspace/design-seed-data.md SEED-7."

# Internal: download seed data if missing
_seed-data-check:
    #!/usr/bin/env bash
    set -euo pipefail
    DATA_DIR="pipeline-data/riot-api/NA1"
    DUMP="redis-data/dump.rdb"
    if [ ! -f "$DUMP" ] && ! ls "$DATA_DIR"/*.jsonl.zst &>/dev/null 2>&1; then
        echo "No seed data found — downloading from Hugging Face..."
        python3 scripts/download_seed.py
    fi

# Internal: remove stale AOF directory (bind mount survives docker compose down -v)
_aof-cleanup:
    #!/usr/bin/env bash
    set -euo pipefail
    AOF_DIR="${REDIS_DATA_DIR:-./redis-data}/appendonlydir"
    if [ -d "$AOF_DIR" ]; then
        echo "Removing stale AOF directory: $AOF_DIR"
        rm -rf "$AOF_DIR"
    fi

# Internal: decompress current month .zst → .jsonl if .jsonl missing
_decompress-current-month:
    #!/usr/bin/env bash
    set -euo pipefail
    MONTH=$(date +%Y-%m)
    ZST="pipeline-data/riot-api/NA1/${MONTH}.jsonl.zst"
    JSONL="pipeline-data/riot-api/NA1/${MONTH}.jsonl"
    if [ -f "$ZST" ] && [ ! -f "$JSONL" ]; then
        echo "Decompressing ${ZST}..."
        python3 -c "import zstandard,pathlib;s=pathlib.Path('$ZST');d=pathlib.Path('$JSONL');d.write_bytes(zstandard.ZstdDecompressor().decompress(s.read_bytes()));print('Decompressed to $JSONL')"
    fi

# Internal: auto-seed Redis from disk if players:all is empty (dump.rdb path is preferred)
_auto-seed:
    #!/usr/bin/env bash
    set -euo pipefail
    DC="{{DC}}"
    # Wait for Redis ready
    for i in $(seq 1 20); do
        $DC exec -T redis redis-cli ping &>/dev/null 2>&1 && break
        sleep 1
    done
    # Check if Redis has player data (use ZCARD players:all, not DBSIZE)
    ZCARD=$($DC exec -T redis redis-cli ZCARD players:all 2>/dev/null | tr -d '[:space:]' || echo "0")
    if [ "$ZCARD" = "0" ]; then
        echo "Redis player data empty — seeding from disk in background..."
        mkdir -p logs
        python3 scripts/seed_from_disk.py >> logs/seed.log 2>&1 &
        echo "Seed running in background. Logs: logs/seed.log"
    else
        echo "Redis already seeded (${ZCARD} players). Skipping seed."
    fi

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

# Export stream depths as machine-readable JSON (for scripting)
streams-json:
    #!/usr/bin/env bash
    set -euo pipefail
    REDIS_CTR="{{_redis_ctr}}"
    exec_redis() { {{RUNTIME}} exec "$REDIS_CTR" redis-cli "$@"; }
    PUUID=$(exec_redis XLEN stream:puuid)
    MATCH_ID=$(exec_redis XLEN stream:match_id)
    PARSE=$(exec_redis XLEN stream:parse)
    ANALYZE=$(exec_redis XLEN stream:analyze)
    DLQ=$(exec_redis XLEN stream:dlq)
    DLQ_ARCHIVE=$(exec_redis XLEN stream:dlq:archive)
    DELAYED=$(exec_redis ZCARD delayed:messages)
    HALTED=$(exec_redis GET system:halted)
    if [ "$HALTED" = "1" ]; then HALTED_BOOL="true"; else HALTED_BOOL="false"; fi
    printf '{"stream:puuid":%s,"stream:match_id":%s,"stream:parse":%s,"stream:analyze":%s,"stream:dlq":%s,"stream:dlq:archive":%s,"delayed:messages":%s,"system_halted":%s}\n' \
        "$PUUID" "$MATCH_ID" "$PARSE" "$ANALYZE" "$DLQ" "$DLQ_ARCHIVE" "$DELAYED" "$HALTED_BOOL"

# One-shot health check: calls /health, formats output, exit 0 if healthy, 1 if issues
monitor port="8080":
    #!/usr/bin/env bash
    set -euo pipefail
    URL="http://localhost:{{port}}/health"
    RESP=$(curl -sf "$URL" 2>/dev/null) || {
        echo "FAIL: Cannot reach $URL (is the UI running?)"
        exit 1
    }
    # Parse JSON via python3 one-liner (no jq dependency required)
    _py_get() { echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin)[$1])"; }
    STATUS=$(_py_get "'status'")
    REDIS=$(_py_get "'redis'")
    HALTED=$(_py_get "'system_halted'")
    DLQ=$(_py_get "'dlq_depth'")
    MEM=$(_py_get "'redis_memory_mb'")
    STREAMS=$(echo "$RESP" | python3 -c "import sys,json;[print(f'  {k:24s} {v}') for k,v in json.load(sys.stdin)['streams'].items()]")
    echo "=== Pipeline Health Check ==="
    echo ""
    printf "%-24s %s\n" "Status:" "$STATUS"
    printf "%-24s %s\n" "Redis:" "$REDIS"
    printf "%-24s %s\n" "System Halted:" "$HALTED"
    printf "%-24s %s\n" "DLQ Depth:" "$DLQ"
    printf "%-24s %s MB\n" "Redis Memory:" "$MEM"
    echo ""
    echo "--- Stream Depths ---"
    echo "$STREAMS"
    echo ""
    # Exit 1 if halted or DLQ has entries
    ISSUES=0
    if [ "$HALTED" = "True" ]; then
        echo "WARNING: System is HALTED"
        ISSUES=1
    fi
    if [ "$DLQ" != "0" ]; then
        echo "WARNING: DLQ has $DLQ entries"
        ISSUES=1
    fi
    if [ "$ISSUES" -eq 0 ]; then
        echo "All checks passed."
    fi
    exit $ISSUES

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
    for svc in crawler fetcher parser player-stats champion-stats recovery delay-scheduler discovery ui; do
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

# Full CI mirror — runs exactly what GitHub Actions runs (minus docker build + integration tests)
# Intended to be run inside the dev container (via just dev-ci); calls internal recipes directly.
ci: lint typecheck _test _contract

# Build the dev container (Python 3.14 + all deps + Node.js + Claude Code CLI)
dev-build:
    {{RUNTIME}} build -f .devcontainer/Dockerfile -t lol-crawler-dev .

# Internal: build dev image only if it doesn't already exist
_ensure-dev-image:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! {{RUNTIME}} image inspect lol-crawler-dev >/dev/null 2>&1; then
        echo "Dev image not found — building lol-crawler-dev..."
        just dev-build
    fi

# Run a command inside the dev container (e.g. just dev "just lint")
dev *args:
    {{RUNTIME}} run --rm -v "{{justfile_directory()}}:/workspace" -w /workspace lol-crawler-dev {{args}}

# Run full CI inside the dev container (consistent Python 3.14 environment)
dev-ci: dev-build
    {{RUNTIME}} run --rm -v "{{justfile_directory()}}:/workspace" -w /workspace lol-crawler-dev \
        bash -c "just _reinstall-workspace && just ci"

# Update API mock fixtures from live Riot API (uses Pwnerer#1337)
update-mocks:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Fetching live data for Pwnerer#1337..."
    python3 scripts/update_mocks.py

# Run integration tests inside the dev container (testcontainers via Docker socket)
integration: _ensure-dev-image
    {{RUNTIME}} run --rm \
        -v "{{justfile_directory()}}:/workspace" \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -w /workspace \
        lol-crawler-dev \
        bash -c "just _reinstall-workspace && just _integration"

# Internal: run integration tests directly (used inside container)
_integration:
    python3 -m pytest tests/integration/ -v

# Run end-to-end tests (requires running stack with valid API key)
e2e:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest tests/e2e/ -v

# Internal: reinstall all packages from workspace sources (run inside container)
_reinstall-workspace:
    uv pip install --system -q \
        -e "lol-pipeline-common/[dev]" \
        -e lol-pipeline-admin \
        -e lol-pipeline-admin-ui \
        -e lol-pipeline-player-stats \
        -e lol-pipeline-champion-stats \
        -e lol-pipeline-crawler \
        -e lol-pipeline-delay-scheduler \
        -e lol-pipeline-discovery \
        -e lol-pipeline-fetcher \
        -e lol-pipeline-parser \
        -e lol-pipeline-recovery \
        -e lol-pipeline-ui

# Run all unit tests inside the dev container (services tested in parallel)
test: _ensure-dev-image
    {{RUNTIME}} run --rm -v "{{justfile_directory()}}:/workspace" -w /workspace lol-crawler-dev \
        bash -c "just _reinstall-workspace && just _test"

# Internal: run all unit tests directly (used inside container)
_test:
    #!/usr/bin/env bash
    set -euo pipefail
    PYTEST="python3 -m pytest"
    PIDS=()
    NAMES=()
    OUTTMP=$(mktemp -d)
    for dir in lol-pipeline-*/; do
        name="${dir%/}"
        if [ -d "$dir/tests/unit" ]; then
            NAMES+=("$name")
            # Collect test paths: always tests/unit, plus any colocated test_*.py in src/
            TEST_PATHS="tests/unit"
            for f in $(find "$dir/src" -name "test_*.py" 2>/dev/null); do
                TEST_PATHS="$TEST_PATHS ${f#$dir/}"
            done
            (cd "$dir" && RC=0 && $PYTEST $TEST_PATHS -q --tb=short > "$OUTTMP/$name.out" 2>&1 || RC=$?; echo $RC > "$OUTTMP/$name.rc") &
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

# Run unit tests for a single service inside the dev container (e.g. just test-svc crawler)
test-svc name: _ensure-dev-image
    {{RUNTIME}} run --rm -v "{{justfile_directory()}}:/workspace" -w /workspace lol-crawler-dev \
        bash -c "just _reinstall-workspace && just _test-svc {{name}}"

# Internal: run unit tests for a single service directly (used inside container)
_test-svc name:
    #!/usr/bin/env bash
    set -euo pipefail
    PATHS="lol-pipeline-{{name}}/tests/unit"
    for f in $(find "lol-pipeline-{{name}}/src" -name "test_*.py" 2>/dev/null); do
        PATHS="$PATHS $f"
    done
    python3 -m pytest $PATHS -v --tb=short

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

# Run all unit tests with coverage report inside the dev container
coverage: _ensure-dev-image
    {{RUNTIME}} run --rm -v "{{justfile_directory()}}:/workspace" -w /workspace lol-crawler-dev \
        bash -c "just _reinstall-workspace && just _coverage"

# Internal: run coverage report directly (used inside container)
_coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in lol-pipeline-*/; do
        name="${dir%/}"
        if [ -d "$dir/tests/unit" ] && [ -d "$dir/src" ]; then
            pkg=$(ls "$dir/src/")
            # Collect test paths: always tests/unit, plus any colocated test_*.py in src/
            TEST_PATHS="tests/unit"
            for f in $(find "$dir/src" -name "test_*.py" 2>/dev/null); do
                TEST_PATHS="$TEST_PATHS ${f#$dir/}"
            done
            echo "=== $name ==="
            (cd "$dir" && python3 -m pytest $TEST_PATHS --cov="src/$pkg" --cov-report=term-missing -q --tb=short)
        fi
    done

# Consolidate individual match JSON files into JSONL+zstd bundles
consolidate:
    #!/usr/bin/env bash
    set -euo pipefail
    PYTHON="python3"
    if [ -f ".venv/bin/python" ]; then PYTHON=".venv/bin/python"; fi
    $PYTHON scripts/consolidate_match_data.py --delete-originals

# Run pip-audit across all services to check for known CVEs
security-audit:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v pip-audit &>/dev/null; then
        echo "Installing pip-audit..."
        pip install -q pip-audit
    fi
    FAILED=0
    for dir in lol-pipeline-*/; do
        if [ -f "$dir/pyproject.toml" ]; then
            echo "=== $dir ==="
            pip install -q -e "$dir" 2>/dev/null || true
        fi
    done
    echo ""
    echo "=== Running pip-audit ==="
    pip-audit --skip-editable || FAILED=1
    exit $FAILED

# Run contract tests for all services inside the dev container
contract: _ensure-dev-image
    {{RUNTIME}} run --rm -v "{{justfile_directory()}}:/workspace" -w /workspace lol-crawler-dev \
        bash -c "just _reinstall-workspace && just _contract"

# Internal: run contract tests directly (used inside container)
_contract:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in lol-pipeline-*/; do
        if [ -f "$dir/pyproject.toml" ] && [ -d "$dir/tests/contract" ]; then
            echo "=== $dir ===" && (cd "$dir" && python3 -m pytest tests/contract/ -v)
        fi
    done
