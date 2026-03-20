# Security

## Threat Model

### Current: Local-Only Development

The pipeline runs on a single developer workstation via Podman Compose (default) / Docker Compose. The attack surface is minimal:

| Threat | Risk | Mitigation |
|--------|------|------------|
| API key leak via git | High | `.env` is gitignored; `RIOT_API_KEY` never in source; ruff S105/S106 enforced |
| Redis exposed on LAN | Medium | Port 6379 bound to localhost only in dev; no auth configured |
| Container escape | Low | Single-user machine; no untrusted workloads |
| Dependency supply chain | Medium | Pinned dependencies in `pyproject.toml`; no wildcard version ranges |

### Future: Bare-Metal Production

When moving to bare-metal production, the threat model expands:

| Threat | Risk | Mitigation Required |
|--------|------|---------------------|
| Redis network exposure | High | Enable `requirepass`, bind to private interface, TLS mandatory |
| API key compromise | High | Secrets manager (HashiCorp Vault, systemd credentials); short-lived tokens |
| Unauthorized API access | Medium | Firewall rules; no public-facing ports except UI (if exposed) |
| Host compromise | Medium | Non-root containers; read-only filesystems; dropped capabilities |
| Log data leakage | Medium | Structured JSON logs may contain PUUIDs; restrict log access |
| DDoS via seed endpoint | Medium | Rate-limit the UI/seed endpoint; authentication layer |

---

## Secret Management

### RIOT_API_KEY Lifecycle

The Riot API key is the single most sensitive credential in the pipeline.

**Development keys** expire every 24 hours. Regenerate at https://developer.riotgames.com.

**Production keys** are permanent but revocable. Applied for via the Riot Developer Portal.

**Rules:**

1. The key is read exclusively via `os.environ` / pydantic-settings (`Config.riot_api_key`)
2. Never hardcode, log, or include in Docker images
3. Never commit to git — enforced by ruff rules S105 (hardcoded passwords) and S106 (hardcoded API keys)
4. The key is injected at runtime via `.env` file (dev) or secrets manager (prod)

**Key rotation procedure:**

```bash
# 1. Generate new key at https://developer.riotgames.com
# 2. Update .env
sed -i 's/^RIOT_API_KEY=.*/RIOT_API_KEY=RGAPI-new-key-here/' .env

# 3. If system:halted was set due to 403, resume
just admin system-resume

# 4. Restart all services to pick up new key
docker compose restart

# 5. Replay any DLQ entries that failed with 403
just admin dlq replay --all
```

### .env Handling

| Rule | Enforcement |
|------|-------------|
| `.env` is gitignored | `.gitignore` contains `.env` |
| `.env.example` is committed | Contains placeholder values and documentation |
| No secrets in `.env.example` | Placeholder: `RGAPI-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| No secrets in Docker images | Config injected at runtime via `env_file: .env` |
| No secrets in logs | `_JsonFormatter` does not log env vars; `RiotClient` does not log the key |

### Production Secret Management (Recommendations)

For bare-metal production:

1. **systemd credential store**: `LoadCredential=` directive injects secrets as files
2. **HashiCorp Vault**: Service reads key from Vault at startup via HTTP API
3. **Environment files with restricted permissions**: `chmod 600 /etc/lol-pipeline/.env`, owned by the service user
4. **Key rotation without downtime**: Update the secret source, then `docker compose restart` — Redis Streams preserve all in-flight messages

---

## Redis Security

### Current (Local Development)

Redis runs as a Docker container with no authentication. This is acceptable for single-developer local use.

```yaml
# docker-compose.yml — current config
redis:
  image: redis:7-alpine
  ports:
    - "6379:6379"  # bound to localhost by Docker
```

### Production Hardening

**1. Enable authentication:**

```
redis-server --requirepass <strong-random-password>
```

Update `REDIS_URL` to include the password:

```
REDIS_URL=redis://:password@redis-host:6379/0
```

**2. Enable TLS:**

```
redis-server \
  --tls-port 6380 \
  --port 0 \
  --tls-cert-file /tls/redis.crt \
  --tls-key-file /tls/redis.key \
  --tls-ca-cert-file /tls/ca.crt
```

Update `REDIS_URL`:

```
REDIS_URL=rediss://:password@redis-host:6380/0
```

(Note: `rediss://` with double-s enables TLS in redis-py.)

**3. ACL configuration (Redis 7):**

Create a dedicated user for the pipeline with minimal permissions:

```
ACL SETUSER lol-pipeline on >password ~stream:* ~delayed:* ~player:* ~match:* ~raw:* ~system:* ~ratelimit:* +@read +@write +@stream +@sortedset +@hash +@set +@string +@generic +@scripting -@admin -@dangerous
```

**4. Network binding:**

```
redis-server --bind 127.0.0.1 --protected-mode yes
```

**5. Disable dangerous commands:**

```
rename-command FLUSHALL ""
rename-command FLUSHDB ""
rename-command DEBUG ""
rename-command CONFIG "CONFIG_SECRET_PREFIX"
```

---

## Docker Hardening

### Current State

Containers run as root with the default Docker capability set. For local development this is the pragmatic default since services install packages at startup via `pip install -e`.

### Production Recommendations

**1. Non-root user:**

Add to each Dockerfile:

```dockerfile
RUN useradd -r -s /bin/false lol
USER lol
```

**2. Read-only root filesystem:**

```yaml
services:
  crawler:
    read_only: true
    tmpfs:
      - /tmp
```

**3. Drop capabilities:**

```yaml
services:
  crawler:
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
```

**4. Resource limits:**

```yaml
services:
  crawler:
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
```

**5. No privileged mode** — never use `privileged: true`.

**6. Pin image digests in production:**

```dockerfile
FROM python:3.12-slim@sha256:<digest>
```

---

## Input Validation

### User-Supplied Input

The pipeline accepts user input at two entry points:

| Entry Point | Input | Validation |
|-------------|-------|------------|
| Seed CLI / Web UI | Riot ID (`GameName#TagLine`) | Must contain `#`; split on first `#`; game_name and tag_line must be non-empty |
| Seed CLI / Web UI | Region string | Must be a key in `PLATFORM_TO_REGION` dict; unknown regions raise `ValueError` |

### Redis Key Construction

Redis keys are constructed from PUUIDs (hex strings from Riot API) and match IDs (format: `NA1_12345`). These are not raw user input — they pass through the Riot API first.

**Rules:**

- No Redis key may be constructed from raw user input without validation
- PUUIDs are 78-character hex strings with hyphens — validated by Riot API response
- Match IDs are alphanumeric with underscores — validated by Riot API response
- The `game_name` and `tag_line` are only used in API URLs (URL-encoded by httpx) and display; never in Redis keys

### External API Responses

All Riot API responses are consumed via `RiotClient`, which:

1. Validates HTTP status codes before returning data
2. Raises typed exceptions (`NotFoundError`, `AuthError`, `RateLimitError`, `ServerError`)
3. Sets a 30-second HTTP timeout to prevent hanging connections
4. Uses `httpx` which handles encoding, TLS verification, and connection pooling

---

## Dependency Security

### Pinning Strategy

Each service's `pyproject.toml` declares dependencies with version ranges:

```toml
dependencies = [
    "lol-pipeline-common>=1.0.0,<2.0.0",
    "redis>=5.0.0,<8.0.0",
    "httpx>=0.27.0",
]
```

### Scanning

**Automated (ruff S rules):**

| Rule | What It Catches |
|------|-----------------|
| S105 | Hardcoded passwords in source |
| S106 | Hardcoded API keys |
| S108 | `/tmp` usage (insecure temp files) |
| S311 | Insecure random for security operations |
| S324 | Weak hashing (MD5/SHA1) |
| S501 | Disabled SSL verification |
| S603, S607 | Shell injection via subprocess |

**Recommended additions for production:**

```bash
# pip-audit — scan installed packages for known CVEs
pip install pip-audit
pip-audit

# Safety — scan requirements files
pip install safety
safety check
```

### Supply Chain Mitigations

1. Use `pip install --require-hashes` in production Dockerfiles
2. Pin exact versions in a lockfile (`pip-compile` or `uv`)
3. Run `pip-audit` in CI before Docker build step
4. Review dependency changelogs before major version upgrades

---

## Coding Security Rules

The `S` ruleset in ruff embeds the bandit security scanner. All rules are enforced across the entire codebase except `S101` (assert) which is suppressed in test files.

### Key Enforcement

| Rule | Description | Project Impact |
|------|-------------|----------------|
| S105, S106 | No hardcoded credentials | Ensures `RIOT_API_KEY` stays in env vars |
| S108 | No `/tmp` usage | Prevents insecure temp file creation |
| S311 | No `random` for security | Rate limiter uses Redis atomics, not Python random |
| S501 | No disabled SSL verification | All Riot API calls use TLS |
| S603, S607 | No shell injection | No subprocess calls in service code |

### Project-Specific Security Requirements

These are enforced by code review, not linter rules:

1. `RIOT_API_KEY` must only be read from `os.environ` / pydantic-settings
2. No Redis keys constructed from raw user input without sanitization
3. All external HTTP calls go through `RiotClient` (rate-limited, authenticated)
4. TLS must be enabled in all production Redis connections
5. The `system:halted` flag is the only manual safety override — never bypass it programmatically

---

## Incident Response

### HTTP 403 (API Key Invalid/Revoked)

**Symptoms:**
- `system:halted` flag is set
- All services exit their worker loops
- CRITICAL log: `system halted`
- Docker restart backoff creates a tight exit-restart-exit loop

**Response:**

```bash
# 1. Verify the key is invalid
curl -H "X-Riot-Token: $RIOT_API_KEY" \
  "https://americas.api.riotgames.com/riot/account/v1/accounts/me"

# 2. Rotate the key
# Edit .env with the new key

# 3. Resume the system
just admin system-resume

# 4. Force-restart all services (don't wait for Docker backoff)
docker compose restart

# 5. Replay DLQ entries that accumulated during the outage
just admin dlq replay --all

# 6. Verify services are processing
just streams
just logs fetcher
```

### API Key Compromise

If the key may have been exposed (committed to git, leaked in logs, etc.):

```bash
# 1. IMMEDIATELY regenerate the key at https://developer.riotgames.com
# 2. Update .env with the new key
# 3. If the old key was committed to git:
#    - Rotate the key (already done in step 1)
#    - Consider the git history permanently compromised
#    - Do NOT try to rewrite git history (it doesn't help if pushed)
# 4. Resume and restart as above
```

### HTTP 429 (Rate Limit Exceeded)

This is normal operation — the pipeline handles it automatically:

1. Fetcher/Crawler receive 429 with `Retry-After` header
2. Message goes to `delayed:messages` with appropriate delay
3. Delay Scheduler moves it back to the target stream when ready
4. Recovery handles DLQ entries with exponential backoff

**If 429s are excessive:**

```bash
# Check rate limiter state
docker compose exec redis redis-cli ZCARD "ratelimit:short"
docker compose exec redis redis-cli ZCARD "ratelimit:long"

# Check actual limits from Riot API
docker compose exec redis redis-cli MGET "ratelimit:limits:short" "ratelimit:limits:long"

# Reduce workers if you're hitting limits too hard
just scale fetcher 1
```

---

## Hardening Recommendations (Production Checklist)

### Priority 1 — Before First Production Deploy

- [ ] Enable Redis authentication (`requirepass`)
- [ ] Enable Redis TLS (`rediss://` URL scheme)
- [ ] Bind Redis to private interface only
- [ ] Move `RIOT_API_KEY` to a secrets manager or systemd credentials
- [ ] Set `.env` file permissions to `600`
- [ ] Run containers as non-root user
- [ ] Drop all Linux capabilities (`cap_drop: ALL`)
- [ ] Set `no-new-privileges` security option
- [ ] Pin Docker image digests

### Priority 2 — After Initial Deployment

- [ ] Configure Redis ACLs for minimal permissions
- [ ] Enable `read_only: true` on container filesystems
- [ ] Set CPU and memory resource limits per container
- [ ] Add `pip-audit` to CI pipeline
- [ ] Add authentication to the Web UI (basic auth or reverse proxy)
- [ ] Disable Redis `FLUSHALL`, `FLUSHDB`, `DEBUG` commands
- [ ] Set up log rotation and access controls

### Priority 3 — Ongoing

- [ ] Schedule weekly `pip-audit` scans
- [ ] Monitor Riot Developer Portal for key status changes
- [ ] Review and rotate Redis passwords quarterly
- [ ] Audit Docker image updates for CVEs
- [ ] Review ruff security rules on each ruff version upgrade
