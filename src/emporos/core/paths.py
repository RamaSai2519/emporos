"""Where research data lives on the development machine.

Research datasets (filings, event stores, PDF text, snapshots, F&O archives, journals, run state)
are expensive or impossible to rebuild, so they never live under `~/.cache`: desktop cleaners empty
it (2026-09-26, everything was lost at once). `research_dir()` is `$EMPOROS_RESEARCH_DIR` when it
is set, else `~/.local/share/emporos/research`. Only a cache that is cheap to derive again from
something already durable may stay under `~/.cache`."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["RESEARCH_DIR_ENV", "research_dir"]

RESEARCH_DIR_ENV = "EMPOROS_RESEARCH_DIR"


def research_dir() -> Path:
    override = os.environ.get(RESEARCH_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "emporos" / "research"
