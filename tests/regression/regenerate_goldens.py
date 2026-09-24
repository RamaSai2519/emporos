"""Regenerate a backtest golden DELIBERATELY:

    python -m tests.regression.regenerate_goldens            # show the diff, write nothing
    python -m tests.regression.regenerate_goldens --write    # replace the goldens

A golden is a promise that a number does not move by accident. Regenerating it is saying "this
change to the numbers is intended": read the diff first, explain it in the commit, and never do it
to turn a red build green. Without `--write` nothing on disk changes.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import sys

from tests.regression.goldens import GOLDENS


async def _run(write: bool) -> int:
    changed = 0
    for path, produce in GOLDENS.items():
        fresh = await produce()
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if fresh == current:
            print(f"unchanged: {path.name}")
            continue
        changed += 1
        diff = list(
            difflib.unified_diff(
                current.splitlines(),
                fresh.splitlines(),
                f"{path.name} (committed)",
                "regenerated",
                lineterm="",
            )
        )
        print(f"CHANGED: {path.name} ({len(diff)} diff lines)")
        print("\n".join(diff[:80]))
        if write:
            path.write_text(fresh, encoding="utf-8")
            print(f"  written: {path}")
    if changed and not write:
        print("\nnothing written: re-run with --write once the diff above is what you intend")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="replace the goldens on disk")
    sys.exit(asyncio.run(_run(parser.parse_args().write)))


if __name__ == "__main__":
    main()
