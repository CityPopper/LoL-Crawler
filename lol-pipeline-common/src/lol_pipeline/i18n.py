"""Shared domain vocabulary for localization.

Two-layer i18n design:
- This module (common) — shared domain terms: roles, tiers, queues, statuses, failure codes.
- ``strings.py`` (per-service) — UI-specific text stays in the service package.

Usage::

    from lol_pipeline.i18n import label

    label("role", "TOP", "zh-CN")  # -> "上单"
    label("role", "TOP")           # -> "Top" (default English)

Missing translations are tracked idempotently via ``track_missing()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

DOMAIN_STRINGS: dict[str, dict[str, dict[str, str]]] = {
    "role": {
        "en": {
            "TOP": "Top",
            "JUNGLE": "Jungle",
            "MIDDLE": "Mid",
            "BOTTOM": "Bot",
            "UTILITY": "Support",
        },
        "zh-CN": {
            "TOP": "\u4e0a\u5355",
            "JUNGLE": "\u6253\u91ce",
            "MIDDLE": "\u4e2d\u5355",
            "BOTTOM": "\u4e0b\u8def",
            "UTILITY": "\u8f85\u52a9",
        },
    },
    "tier": {
        "en": {
            "CHALLENGER": "Challenger",
            "GRANDMASTER": "Grandmaster",
            "MASTER": "Master",
            "DIAMOND": "Diamond",
            "EMERALD": "Emerald",
            "PLATINUM": "Platinum",
            "GOLD": "Gold",
            "SILVER": "Silver",
            "BRONZE": "Bronze",
            "IRON": "Iron",
        },
        "zh-CN": {
            "CHALLENGER": "\u738b\u8005",
            "GRANDMASTER": "\u5b97\u5e08",
            "MASTER": "\u5927\u5e08",
            "DIAMOND": "\u94bb\u77f3",
            "EMERALD": "\u7fe0\u7389",
            "PLATINUM": "\u94c2\u91d1",
            "GOLD": "\u9ec4\u91d1",
            "SILVER": "\u767d\u94f6",
            "BRONZE": "\u9752\u94dc",
            "IRON": "\u9ed1\u94c1",
        },
    },
    "queue": {
        "en": {
            "420": "Ranked Solo/Duo",
            "440": "Ranked Flex",
            "450": "ARAM",
            "400": "Normal Draft",
        },
        "zh-CN": {
            "420": "\u5355\u53cc\u6392\u4f4d",
            "440": "\u7075\u6d3b\u6392\u4f4d",
            "450": "\u6781\u5730\u5927\u4e71\u6597",
            "400": "\u5339\u914d\u6a21\u5f0f",
        },
    },
    "status": {
        "en": {
            "running": "Running",
            "halted": "HALTED",
            "ok": "OK",
            "busy": "Busy",
            "backlog": "Backlog",
        },
        "zh-CN": {
            "running": "\u8fd0\u884c\u4e2d",
            "halted": "\u5df2\u505c\u6b62",
            "ok": "\u6b63\u5e38",
            "busy": "\u7e41\u5fd9",
            "backlog": "\u79ef\u538b",
        },
    },
    "failure_code": {
        "en": {
            "parse_error": "Parse Error",
            "http_403": "Auth Failed",
            "http_429": "Rate Limited",
            "http_500": "Server Error",
            "fetch_error": "Fetch Error",
        },
        "zh-CN": {
            "parse_error": "\u89e3\u6790\u9519\u8bef",
            "http_403": "\u8ba4\u8bc1\u5931\u8d25",
            "http_429": "\u9891\u7387\u9650\u5236",
            "http_500": "\u670d\u52a1\u5668\u9519\u8bef",
            "fetch_error": "\u83b7\u53d6\u9519\u8bef",
        },
    },
}

DDRAGON_LOCALE_MAP: dict[str, str] = {"en": "en_US", "zh-CN": "zh_CN"}


def label(domain: str, key: str, lang: str = "en") -> str:
    """Look up a domain-specific localized label.

    Falls back to the English value first, then to *key* itself.
    """
    domain_dict = DOMAIN_STRINGS.get(domain, {})
    lang_dict = domain_dict.get(lang, domain_dict.get("en", {}))
    return lang_dict.get(key, key)


async def track_missing(r: Redis, lang: str, domain: str, key: str) -> None:
    """SADD to ``i18n:missing:{lang}`` -- idempotent, no log flood."""
    await r.sadd(f"i18n:missing:{lang}", f"{domain}:{key}")  # type: ignore[misc]
