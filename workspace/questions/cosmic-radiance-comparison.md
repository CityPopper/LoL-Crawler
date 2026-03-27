# Cosmic-Radiance Feature Party Comparison

**Repo**: https://github.com/DarkIntaqt/cosmic-radiance
**What it is**: A Go-based rate-limiting proxy for the Riot Games API. Not a data crawler — purely a rate limiter.
**Comparison scope**: cosmic-radiance vs our `lol-pipeline-rate-limiter` service.

---

## Side-by-Side Feature Matrix

| Feature | cosmic-radiance | lol-pipeline-rate-limiter |
|---|---|---|
| **Rate limiting algorithm** | Dynamic — learns limits from API response headers | Static — hardcoded limits per env var |
| **Efficiency** | ~99% utilisation (self-tuning) | ~90% (18/20 short, 90/100 long — manual headroom) |
| **Endpoint detection** | Automatic (parses Riot OpenAPI schema) | Manual (known_sources list) |
| **Priority queuing** | Yes — `X-Priority: high` header, 5× batch size | Yes — `player:priority:*` Redis keys, deferred low-priority messages |
| **Multi-source support** | Riot API only | Riot (per-region), OP.GG, UI-reserved buckets |
| **Prometheus metrics** | Yes — `/metrics` endpoint | No |
| **Proxy mode** | Yes — transparent `<platform>.api.riotgames.com` proxy | No — token-bucket HTTP service only |
| **PATH mode** | Yes — `/<platform>/<method>` passthrough | No |
| **cooling-off / backoff signal** | Implicit (learns from 429 Retry-After headers) | Explicit — `PUT /cooling-off/{bucket}` from fetcher |
| **Per-region buckets** | Automatic (platform → region routing) | Manual (`riot:americas`, `riot:europe`, etc.) |
| **Language** | Go 1.24 | Python 3.14 |
| **Docker support** | Yes | Yes |
| **Request timeout** | Configurable (default 10s) | N/A — token only, no proxying |
| **Latency buffer** | Yes — `ADDITIONAL_WINDOW_SIZE=125ms` | No |
| **Queue batch sizes** | Normal 25, Priority 125 | N/A |
| **Polling interval** | 10ms (configurable) | N/A |

---

## What cosmic-radiance has that we DON'T

### 1. Dynamic Rate Limit Discovery ← most valuable
Cosmic-radiance reads `X-Rate-Limit-Count` and `X-Rate-Limit-Type` response headers directly from Riot and self-tunes. Our system uses hardcoded `RATELIMIT_RIOT_SHORT_LIMIT=18` — which is why we just manually lowered it from 20 to 18 to stop 429s. Cosmic-radiance would have *never needed that change*.

### 2. ~99% API Utilisation
By learning the actual ceiling from live headers rather than guessing a safe margin, cosmic-radiance consistently achieves near-100% throughput. Our static 90% setting wastes ~10% of the key quota.

### 3. Prometheus Metrics
Built-in `/metrics` with queue sizes, rate-limit state, request counts. We have nothing — diagnosing rate issues currently requires reading raw logs.

### 4. Proxy / PATH Mode
Cosmic-radiance can sit between any service and Riot transparently. Our rate limiter is a token service — callers must explicitly call `try_token()` before every request.

### 5. Latency Buffer (`ADDITIONAL_WINDOW_SIZE`)
An explicit buffer for network jitter prevents burst overrun near window edges. We have no equivalent — this is partly why we still get occasional 429s even after adding headroom.

---

## What WE have that cosmic-radiance DOESN'T

### 1. Multi-Source Rate Limiting
We handle Riot *and* OP.GG *and* UI-reserved buckets in one service, with separate short/long limits per source. Cosmic-radiance is Riot-only.

### 2. Explicit cooling-off API
When our fetcher receives a 429, it calls `PUT /cooling-off/{bucket}` to immediately block that bucket. Cosmic-radiance learns this passively from the next response — there's a gap where it would still issue requests until the next poll cycle (10ms default).

### 3. Stream/Redis-native Priority
Our priority system is woven into the pipeline (Redis streams, `player:priority:*` keys, deferred messages). Cosmic-radiance's priority is HTTP header-based — entirely separate from our pipeline state.

---

## Integration Options

### Option A — Replace our rate-limiter with cosmic-radiance (Riot only)
- Swap `lol-pipeline-rate-limiter` → cosmic-radiance container in docker-compose
- Remove `try_token()`/`wait_for_token()` calls for Riot; route Riot calls through cosmic-radiance HTTP proxy instead
- Keep our existing rate-limiter *only* for OP.GG + UI buckets (or fold OP.GG into a second service instance)
- **Pro**: Dynamic limits, 99% utilisation, Prometheus metrics, no more manual tuning
- **Con**: Large refactor — every Riot API call site changes from token-acquire to proxied HTTP. OP.GG still needs a separate solution.

### Option B — Borrow the dynamic-discovery logic, implement in our service
- Port the response-header parsing and self-tuning algorithm into `lol-pipeline-rate-limiter` (Python)
- Keep our existing architecture (multi-source, cooling-off, Redis-native)
- **Pro**: Minimal integration cost, keeps OP.GG/UI support, keeps explicit cooling-off
- **Con**: Replication effort; we lose the proxy mode (not currently needed anyway)

### Option C — Run cosmic-radiance alongside for Riot only (dual-track)
- cosmic-radiance handles Riot quota; our limiter handles OP.GG/UI
- Fetcher/Crawler send Riot calls *through* cosmic-radiance proxy
- **Pro**: Clean separation, best-of-both
- **Con**: Two rate-limit services adds operational complexity

---

## Questions [H]

1. **Integration depth**: Do you want to replace our custom rate-limiter with cosmic-radiance (Option A/C), or just borrow the dynamic-discovery idea (Option B)?
2. **OP.GG scope**: Cosmic-radiance doesn't handle OP.GG. Should OP.GG rate limiting stay in our service, or is it lower priority?
3. **Proxy vs token model**: Switching to Option A requires changing every Riot API call to go through an HTTP proxy rather than acquiring a token first. Are you comfortable with that architectural shift?
4. **Priority queue compatibility**: Our priority system is Redis-stream-based. If we switch to cosmic-radiance's HTTP-header priority, the two systems need to be reconciled. How much priority-queue fidelity do you need?
5. **Scope**: Should this be a full replacement or start as a parallel spike to measure the utilisation improvement?
