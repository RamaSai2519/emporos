"""Where research keeps what it cannot rebuild cheaply (durable, not a cache).

`EMPOROS_RESEARCH_DIR`, else `~/.local/share/emporos/research`. Caches under `~/.cache` are wiped
by desktop cleaners (one was, on 2026-09-26); the LLM journal, the autorun state, collected data and
the atlas ledgers live here instead."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["ENV_VAR", "research_dir"]

ENV_VAR = "EMPOROS_RESEARCH_DIR"


def research_dir() -> Path:
    override = os.environ.get(ENV_VAR)
    return Path(override) if override else Path.home() / ".local" / "share" / "emporos" / "research"
