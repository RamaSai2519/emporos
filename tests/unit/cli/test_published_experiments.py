"""EM-188: every curation report published before the registry has a registry entry, and the
committed index says what the committed records say."""

from __future__ import annotations

import json
from pathlib import Path

from emporos.cli.experiment_registry import (
    DEFAULT_EXPERIMENTS_DIR,
    INDEX_JSON,
    INDEX_MARKDOWN,
    ExperimentIndex,
)

STRATEGIES = Path("docs/strategies")


def published_entries() -> list[tuple[str, str]]:
    """(strategy, directory) for every entry of every published curation JSON."""
    found: list[tuple[str, str]] = []
    for pattern in ("*.json", "benchmark_50k/*.json", "em171/*.json"):
        for path in sorted(STRATEGIES.glob(pattern)):
            found += [(d["strategy"], path.parent.name) for d in json.loads(path.read_text())]
    return found


def records() -> list[dict[str, object]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(DEFAULT_EXPERIMENTS_DIR.glob("EXP-*.json"))
    ]


def test_every_published_curation_report_has_a_registry_entry() -> None:
    entries = published_entries()

    backfilled = [r for r in records() if not r["declaration"]["predeclared"]]  # type: ignore[index]

    assert entries and len(backfilled) >= len(entries)
    slugs = {r["slug"] for r in backfilled}
    for strategy, _ in entries:
        assert any(str(s).startswith(strategy.replace("_", "-")) for s in slugs), strategy


def test_every_published_report_is_marked_and_never_accepted_unless_predeclared() -> None:
    for record in records():
        declaration = record["declaration"]  # type: ignore[index]
        if not declaration["predeclared"]:
            assert record["outcome"] != "accepted"
            assert "BACKFILLED_NOT_PREDECLARED" in record["notes"]  # type: ignore[operator]


def test_the_committed_index_is_what_the_committed_records_produce() -> None:
    index = ExperimentIndex()
    rows = index.rows(records())

    committed_json = json.loads((DEFAULT_EXPERIMENTS_DIR / INDEX_JSON).read_text())

    assert committed_json == {"experiments": rows}
    assert (DEFAULT_EXPERIMENTS_DIR / INDEX_MARKDOWN).read_text() == index.markdown(rows)
