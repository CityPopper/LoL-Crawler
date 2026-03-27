"""Configuration for the rate-limiter service — env vars only."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

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
            msg = f"Invalid domain name {self.name!r}: must match ^[a-z0-9:_-]+$"
            raise ValueError(msg)
        if not (0.0 <= self.ui_pct <= 1.0):
            msg = f"ui_pct must be 0.0-1.0, got {self.ui_pct}"
            raise ValueError(msg)


# ---------------------------------------------------------------------------
# Environment-driven domain loading
# ---------------------------------------------------------------------------

_SHORT_LIMIT_SUFFIX = "_SHORT_LIMIT"
_DOMAIN_PREFIX = "DOMAIN_"


def _parse_bool(val: str) -> bool:
    """Parse a boolean from an env var string."""
    return val.lower() in ("true", "1", "yes")


def load_domains_from_env() -> dict[str, Domain]:
    """Load domain configs from environment variables.

    Scans for ``DOMAIN_*_SHORT_LIMIT`` env vars, derives domain names,
    validates them against ``^[a-z0-9:_-]+$``, and returns a dict keyed
    by domain name.

    Raises ``ValueError`` at startup if any domain name is invalid.
    """
    domains: dict[str, Domain] = {}

    for key in os.environ:
        if not key.startswith(_DOMAIN_PREFIX) or not key.endswith(_SHORT_LIMIT_SUFFIX):
            continue

        # Extract the PREFIX between DOMAIN_ and _SHORT_LIMIT
        prefix = key[len(_DOMAIN_PREFIX) : -len(_SHORT_LIMIT_SUFFIX)]
        if not prefix:
            continue

        # Resolve canonical domain name
        name_key = f"{_DOMAIN_PREFIX}{prefix}_NAME"
        if name_key in os.environ:
            domain_name = os.environ[name_key]
        else:
            domain_name = prefix.lower().replace("_", ":")

        # Validation happens in Domain.__post_init__, but we crash-fast here
        # with a clearer message if the name is invalid.
        if not _DOMAIN_NAME_RE.match(domain_name):
            msg = (
                f"Invalid domain name {domain_name!r} "
                f"(from env prefix {prefix!r}): must match ^[a-z0-9:_-]+$"
            )
            raise ValueError(msg)

        env_pfx = f"{_DOMAIN_PREFIX}{prefix}_"
        short_limit = int(os.environ.get(f"{env_pfx}SHORT_LIMIT", "20"))
        long_limit = int(os.environ.get(f"{env_pfx}LONG_LIMIT", "100"))
        short_window_ms = int(os.environ.get(f"{env_pfx}SHORT_WINDOW_MS", "1000"))
        long_window_ms = int(os.environ.get(f"{env_pfx}LONG_WINDOW_MS", "120000"))
        header_aware = _parse_bool(os.environ.get(f"{env_pfx}HEADER_AWARE", "false"))
        has_method_limits = _parse_bool(
            os.environ.get(f"{env_pfx}HAS_METHOD_LIMITS", "false"),
        )
        ui_pct = float(os.environ.get(f"{env_pfx}UI_PCT", "0.0"))

        domains[domain_name] = Domain(
            name=domain_name,
            short_limit=short_limit,
            long_limit=long_limit,
            short_window_ms=short_window_ms,
            long_window_ms=long_window_ms,
            header_aware=header_aware,
            has_method_limits=has_method_limits,
            ui_pct=ui_pct,
        )

    return domains


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class Config:
    """Rate-limiter configuration sourced from environment variables."""

    def __init__(self) -> None:
        self.redis_url: str = os.environ.get("RATE_LIMITER_REDIS_URL", "redis://redis:6379")
        self.secret: str = os.environ.get("RATE_LIMITER_SECRET", "")
        self.domains: dict[str, Domain] = load_domains_from_env()
