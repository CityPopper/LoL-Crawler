"""RATE-DYN-2 Domain Registry — Phase 1 Interface Spec.

This file defines the machine-verifiable contracts (types, protocols, stubs)
for the domain-based rate-limiter refactor.  It is NOT implementation.

Import this from tests to get type-checked stubs.  Do NOT import from
``lol_rate_limiter`` modules here — this spec is the source of truth.

-------------------------------------------------------------------------------
BEHAVIORAL SPEC: load_domains_from_env
-------------------------------------------------------------------------------

Environment variable convention:
  - Each domain is defined by a set of env vars sharing a common prefix.
  - Domain name ``riot:americas`` maps to env key prefix ``DOMAIN_RIOT_AMERICAS_``
    (uppercase the name, replace ``:`` with ``_``, prepend ``DOMAIN_``).
  - Recognised suffixes: ``SHORT_LIMIT``, ``SHORT_WINDOW_MS``, ``LONG_LIMIT``,
    ``LONG_WINDOW_MS``, ``HEADER_AWARE``, ``HAS_METHOD_LIMITS``, ``UI_PCT``.

Discovery:
  - Scan all env vars for keys matching ``DOMAIN_*_SHORT_LIMIT``.
  - Extract the domain prefix from the key (everything between ``DOMAIN_`` and
    ``_SHORT_LIMIT``), then reverse the name transform to recover the domain
    name (lowercase, ``_`` back to ``:`` only where it was originally ``:``).
  - The canonical domain name is stored in ``DOMAIN_<PREFIX>_NAME`` when the
    simple reverse transform is ambiguous.  If ``_NAME`` is absent, use the
    lowercased prefix with ``_`` replaced by ``:``.

Domain name validation:
  - Must match ``^[a-z0-9:_-]+$``.
  - Raise ``ValueError`` at startup on mismatch (crash-fast).

Defaults when env var is absent:
  - ``SHORT_WINDOW_MS`` = 1000
  - ``LONG_WINDOW_MS`` = 120000
  - ``HEADER_AWARE`` = false
  - ``HAS_METHOD_LIMITS`` = false
  - ``UI_PCT`` = 0.0

Return value:
  - ``dict[str, Domain]`` keyed by canonical domain name.
  - Empty dict when no ``DOMAIN_*_SHORT_LIMIT`` env vars exist.

-------------------------------------------------------------------------------
BEHAVIORAL SPEC: acquire_token_for_domain
-------------------------------------------------------------------------------

1. Cooling-off check:
   - Read ``ratelimit:{domain.name}:cooling_off`` PTTL.
   - If TTL > 0: return ``(False, ttl_ms)`` immediately.

2. Resolve effective limits and Redis key prefix:

   a) If ``domain.ui_pct == 0.0`` (no UI split):
      - key_prefix = ``ratelimit:{domain.name}``
      - short_limit = ``domain.short_limit``
      - long_limit = ``domain.long_limit``

   b) If ``domain.ui_pct > 0`` and ``is_ui=True``:
      - key_prefix = ``ratelimit:{domain.name}:ui``
      - ui_long = ``floor(domain.long_limit * domain.ui_pct)``
      - ui_short = ``max(1, floor(domain.short_limit * domain.ui_pct))``
      - short_limit = ui_short
      - long_limit = ui_long

   c) If ``domain.ui_pct > 0`` and ``is_ui=False``:
      - key_prefix = ``ratelimit:{domain.name}:pipeline``
      - ui_long = ``floor(domain.long_limit * domain.ui_pct)``
      - pipeline_long = ``domain.long_limit - ui_long``
      - short_limit = ``domain.short_limit``  (full short budget for pipeline)
      - long_limit = pipeline_long

3. Lua script dispatch:
   - If ``domain.has_method_limits`` and ``endpoint`` is non-empty:
     Use ``LUA_RATE_LIMIT_METHOD_SCRIPT`` (8 keys).
     Method-level bucket prefix = ``{key_prefix}:{endpoint}``.
     Method-level limits default to the resolved short_limit/long_limit.
   - Otherwise:
     Use ``LUA_RATE_LIMIT_SCRIPT`` (4 keys).

4. Return value:
   - ``(True, None)`` when token is granted.
   - ``(False, retry_after_ms)`` when denied (retry_after_ms is a positive int).

5. RPM counter:
   - On grant, increment ``{key_prefix}:rpm:{minute_bucket}`` with 2-hour TTL
     (same pattern as current ``acquire_token``).

-------------------------------------------------------------------------------
BEHAVIORAL SPEC: POST /token/acquire
-------------------------------------------------------------------------------

Request body (JSON):
  ``{"domain": "riot:americas", "endpoint": "match", "priority": 0, "is_ui": false}``

  - ``domain`` (required): domain name string.
  - ``endpoint`` (optional, default ""): method-level endpoint name.
  - ``priority`` (optional, default 0): integer, accepted but NOT used in DYN-2.
  - ``is_ui`` (optional, default false): boolean, selects UI sub-bucket.

Behavior:
  - Look up ``domain`` in ``cfg.domains``; return 404 ``{"error": "unknown domain"}``
    if not found.
  - Call ``acquire_token_for_domain(r, domain_obj, endpoint, is_ui=is_ui)``.
  - On Redis failure: fail open — return ``{"granted": true, "retry_after_ms": null}``.
  - On success: return ``{"granted": <bool>, "retry_after_ms": <int|null>}``.

-------------------------------------------------------------------------------
BEHAVIORAL SPEC: POST /cooling-off
-------------------------------------------------------------------------------

Request body (JSON):
  ``{"domain": "opgg", "delay_ms": 5000}``

  - ``domain`` (required): domain name string.
  - ``delay_ms`` (optional, default 60000): milliseconds to block.

Behavior:
  - Look up ``domain`` in ``cfg.domains``; return 404 if not found.
  - Set ``ratelimit:{domain}:cooling_off`` with ``PX=max(delay_ms, 1000)``.
  - Cooling-off is domain-level (NOT per sub-bucket).
  - Return ``{"ok": true}`` on success, ``{"ok": false}`` on Redis failure.

-------------------------------------------------------------------------------
BEHAVIORAL SPEC: POST /cooling-off/reset
-------------------------------------------------------------------------------

Request body (JSON):
  ``{"domain": "opgg"}``

  - ``domain`` (required): domain name string.

Behavior:
  - Look up ``domain`` in ``cfg.domains``; return 404 if not found.
  - Delete the following Redis keys:
    - ``ratelimit:{domain}:halved``
    - ``ratelimit:{domain}:limits:short``
    - ``ratelimit:{domain}:limits:long``
  - This forces the domain to fall back to config-default limits.
  - Return ``{"ok": true}``.

-------------------------------------------------------------------------------
BEHAVIORAL SPEC: POST /headers
-------------------------------------------------------------------------------

Request body (JSON):
  ``{"domain": "riot:americas", "rate_limit": "20:1,100:120",
    "rate_limit_count": "5:1,40:120"}``

  - ``domain`` (required): domain name string.
  - ``rate_limit`` (optional): Riot-format limit header.
  - ``rate_limit_count`` (optional): Riot-format count header.

Behavior:
  - Look up ``domain`` in ``cfg.domains``; return 404 if not found.
  - If ``domain.header_aware`` is ``False``: return ``{"updated": false}``
    (200 OK, no-op).
  - If ``domain.header_aware`` is ``True``: parse limit headers, store in
    Redis with 1-hour TTL at ``ratelimit:{domain}:limits:short`` and
    ``ratelimit:{domain}:limits:long``.  Return ``{"updated": true, ...}``.

-------------------------------------------------------------------------------
BEHAVIORAL SPEC: GET /status
-------------------------------------------------------------------------------

Response body (JSON):
  ``{"domains": {"riot:americas": {...}, "opgg": {...}}}``

Per-domain fields:
  - ``short_limit`` (int): effective short limit (from Redis override or config).
  - ``long_limit`` (int): effective long limit (from Redis override or config).
  - ``halved`` (bool): from ``ratelimit:{domain}:halved`` Redis key.
  - ``halve_count`` (int): from ``ratelimit:{domain}:halve_count`` (default 0).
  - ``header_aware`` (bool): from ``domain.header_aware`` config.
  - ``has_method_limits`` (bool): from ``domain.has_method_limits`` config.
  - ``halved_at`` (int, optional): epoch seconds from
    ``ratelimit:{domain}:halved_at``.  Omitted when not set.

Behavior:
  - Iterate over ``cfg.domains``.
  - For each domain, read Redis keys to populate dynamic fields.
  - On Redis failure for a domain, include the domain with config defaults and
    ``halved=false, halve_count=0``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

# ---------------------------------------------------------------------------
# Domain dataclass
# ---------------------------------------------------------------------------

_DOMAIN_NAME_RE: re.Pattern[str] = re.compile(r"^[a-z0-9:_-]+$")


@dataclass(frozen=True, slots=True)
class Domain:
    """Rate-limit domain configuration.

    Each domain represents a distinct upstream API or source with its own
    rate-limit budget.  Instances are immutable after construction.
    """

    name: str
    short_limit: int
    short_window_ms: int = 1000
    long_limit: int = 100
    long_window_ms: int = 120_000
    header_aware: bool = False
    has_method_limits: bool = False
    ui_pct: float = 0.0

    def __post_init__(self) -> None:
        if not _DOMAIN_NAME_RE.match(self.name):
            msg = (
                f"Invalid domain name {self.name!r}: "
                "must match ^[a-z0-9:_-]+$"
            )
            raise ValueError(msg)
        if not (0.0 <= self.ui_pct <= 1.0):
            msg = f"ui_pct must be 0.0-1.0, got {self.ui_pct}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class LoadDomainsProtocol(Protocol):
    """Protocol for the ``load_domains_from_env`` function.

    Implementations scan the process environment for ``DOMAIN_*_SHORT_LIMIT``
    keys, derive domain names, validate them, and return a mapping of
    domain name -> Domain.
    """

    def __call__(self) -> dict[str, Domain]: ...


class AcquireTokenForDomainProtocol(Protocol):
    """Protocol for ``acquire_token_for_domain``.

    Implementations check cooling-off, resolve effective limits based on
    ``ui_pct`` and ``is_ui``, select the appropriate Lua script, and return
    ``(granted, retry_after_ms)``.
    """

    async def __call__(
        self,
        r: object,
        domain: Domain,
        endpoint: str = "",
        *,
        is_ui: bool = False,
    ) -> tuple[bool, int | None]: ...


class ConfigProtocol(Protocol):
    """Protocol for the post-DYN-2 Config shape.

    The new Config drops all per-source limit fields and exposes a single
    ``domains`` mapping populated by ``load_domains_from_env()``.
    """

    @property
    def redis_url(self) -> str: ...

    @property
    def secret(self) -> str: ...

    @property
    def domains(self) -> dict[str, Domain]: ...


# ---------------------------------------------------------------------------
# Stubs (NotImplementedError)
# ---------------------------------------------------------------------------


def load_domains_from_env() -> dict[str, Domain]:
    """Load domain configs from environment variables.

    Scans for ``DOMAIN_*_SHORT_LIMIT`` env vars, derives domain names,
    validates them against ``^[a-z0-9:_-]+$``, and returns a dict keyed
    by domain name.

    Raises ``ValueError`` at startup if any domain name is invalid.

    See module docstring for full behavioral specification.
    """
    raise NotImplementedError


async def acquire_token_for_domain(
    r: object,
    domain: Domain,
    endpoint: str = "",
    *,
    is_ui: bool = False,
) -> tuple[bool, int | None]:
    """Acquire a rate-limit token for the given domain.

    1. Check domain-level cooling-off key.
    2. Resolve effective limits and key prefix based on ``ui_pct`` / ``is_ui``.
    3. Dispatch to the appropriate Lua script (4-key or 8-key).
    4. Return ``(True, None)`` on grant, ``(False, retry_after_ms)`` on denial.

    See module docstring for full behavioral specification.
    """
    raise NotImplementedError
