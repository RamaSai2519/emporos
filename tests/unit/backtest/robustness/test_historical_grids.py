"""EM-191 F2B: the proof-run grids that never reached their Mongo ledgers still count toward N."""

from __future__ import annotations

from pathlib import Path

import pytest

from emporos.backtest.robustness.historical_grids import (
    GridFamily,
    HistoricalGrid,
    HistoricalGridCounter,
    HistoricalGridLoader,
)
from emporos.core.errors import ConfigurationError

REPO = Path(__file__).resolve().parents[4]
MANIFEST = REPO / "docs/research/edge-search/historical-trials.yaml"

ROW = "{id: a, family: feature, ticket: EM-1, trials: 5, source: docs/x.md, evidence: five}"


def manifest(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "grids.yaml"
    path.write_text("grids:\n" + "".join(f"  - {row}\n" for row in rows), encoding="utf-8")
    return path


def grid(family: GridFamily, trials: int) -> HistoricalGrid:
    return HistoricalGrid(f"{family}-{trials}", family, "EM-1", trials, Path("x.md"), "x")


# --- counting -------------------------------------------------------------------------------------


async def test_a_counter_sums_only_its_own_family() -> None:
    grids = [
        grid(GridFamily.FEATURE, 140),
        grid(GridFamily.FEATURE, 10880),
        grid(GridFamily.LEAD_LAG, 2016),
    ]

    feature = HistoricalGridCounter("feature", GridFamily.FEATURE, grids)
    cross_sectional = HistoricalGridCounter("cs", GridFamily.CROSS_SECTIONAL, grids)

    assert feature.name == "feature"
    assert await feature.count() == 11020
    assert await cross_sectional.count() == 0


# --- the loader is strict -------------------------------------------------------------------------


def test_a_well_formed_manifest_loads(tmp_path: Path) -> None:
    (loaded,) = HistoricalGridLoader().load(manifest(tmp_path, ROW))

    assert loaded == HistoricalGrid("a", GridFamily.FEATURE, "EM-1", 5, Path("docs/x.md"), "five")


@pytest.mark.parametrize(
    "bad",
    [
        "{id: a, family: feature, ticket: EM-1, trials: 5, source: s, evidence: e, extra: 1}",
        "{id: a, family: feature, ticket: EM-1, trials: 5, source: s}",
        "{id: a, family: feature, ticket: EM-1, trials: 0, source: s, evidence: e}",
        "{id: a, family: feature, ticket: EM-1, trials: -3, source: s, evidence: e}",
        "{id: a, family: feature, ticket: EM-1, trials: '5', source: s, evidence: e}",
        "{id: a, family: feature, ticket: EM-1, trials: true, source: s, evidence: e}",
        "{id: a, family: nonsense, ticket: EM-1, trials: 5, source: s, evidence: e}",
        "just a string",
    ],
)
def test_a_malformed_row_is_refused_not_dropped(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ConfigurationError):
        HistoricalGridLoader().load(manifest(tmp_path, bad))


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="duplicate"):
        HistoricalGridLoader().load(manifest(tmp_path, ROW, ROW))


@pytest.mark.parametrize("text", ["grids: []", "[]", "{not: yaml: at all", "other: 1"])
def test_an_empty_or_unreadable_manifest_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "grids.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigurationError):
        HistoricalGridLoader().load(path)


def test_a_missing_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        HistoricalGridLoader().load(tmp_path / "absent.yaml")


# --- the committed manifest -----------------------------------------------------------------------


def test_the_committed_manifest_covers_em178_to_em181() -> None:
    grids = HistoricalGridLoader().load(MANIFEST)

    assert {g.ticket for g in grids} == {"EM-178", "EM-179", "EM-180", "EM-181"}
    assert {g.family for g in grids} == set(GridFamily)


def test_every_committed_grid_is_backed_by_the_line_in_its_report() -> None:
    for g in HistoricalGridLoader().load(MANIFEST):
        report = " ".join((REPO / g.source).read_text(encoding="utf-8").split())

        assert g.evidence in report, f"{g.grid_id}: {g.source} no longer says {g.evidence!r}"
        assert f"{g.trials:,}" in g.evidence or str(g.trials) in g.evidence, g.grid_id
