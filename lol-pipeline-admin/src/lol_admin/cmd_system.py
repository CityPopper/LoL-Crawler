"""Admin CLI: system-halt and system-resume commands."""

from __future__ import annotations

import argparse

import redis.asyncio as aioredis

from lol_admin._helpers import _confirm, _print_info, _print_ok


async def cmd_system_halt(r: aioredis.Redis, args: argparse.Namespace) -> int:
    if not _confirm("Are you sure you want to halt the pipeline? [y/N]: ", args):
        _print_info("aborted")
        return 1
    await r.set("system:halted", "1")
    _print_ok("system halted \u2014 all consumers will stop on next message")
    return 0


async def cmd_system_resume(r: aioredis.Redis, args: argparse.Namespace) -> int:
    await r.delete("system:halted")
    _print_ok("system resumed \u2014 system:halted cleared")
    return 0
