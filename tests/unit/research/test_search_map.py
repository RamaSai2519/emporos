"""EM-192 (EM-191 F1): the edge-search map's model, loader and the committed map itself."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from emporos.core.errors import ConfigurationError
from emporos.research.search_map import (
    CellStatus,
    Foundation,
    FoundationStatus,
    Lane,
    LanePriority,
    SearchCell,
    SearchMap,
    SearchMapLoader,
    Status,
)

REPO = Path(__file__).resolve().parents[3]
COMMITTED_MAP = REPO / "docs" / "research" / "edge-search" / "search-map.yaml"

LANE = Lane("L3", LanePriority.P1, "Gaps")
F3 = Foundation("F3", "screener", Status(FoundationStatus.TODO), None)


def cell(
    cell_id: str = "L3-gap",
    *,
    parent: str | None = None,
    status: Status[CellStatus] | None = None,
    requires: tuple[str, ...] = (),
    experiments: tuple[str, ...] = (),
    lesson: str = "",
) -> SearchCell:
    return SearchCell(
        cell_id, "L3", "gap", parent, requires, status or Status(CellStatus.TODO), experiments,
        lesson,
    )  # fmt: skip


def rejected(cell_id: str = "L3-gap", *, parent: str | None = None) -> SearchCell:
    return cell(
        cell_id, parent=parent, status=Status(CellStatus.SCREEN_REJECT),
        experiments=("EXP-1",), lesson="gross negative",
    )  # fmt: skip


# --- the committed map ----------------------------------------------------------------------------


def test_the_committed_search_map_is_valid() -> None:
    search_map = SearchMapLoader().load(COMMITTED_MAP)

    assert set(search_map.lanes) == {f"L{n}" for n in range(1, 18)}
    assert {"F1", "F2", "F3", "F4", "VAULT"} <= set(search_map.foundations)


def test_no_committed_lane_cell_can_run_before_the_trial_counter_and_the_vault_seal() -> None:
    search_map = SearchMapLoader().load(COMMITTED_MAP)

    for each in search_map.cells.values():
        assert {"F2", "F2B", "VAULT"} <= set(each.requires), each.cell_id


# --- status ---------------------------------------------------------------------------------------


def test_blocked_must_name_an_em_ticket() -> None:
    with pytest.raises(ValueError, match="EM ticket"):
        Status(CellStatus.BLOCKED)
    with pytest.raises(ValueError, match="EM ticket"):
        Status(CellStatus.BLOCKED, "JIRA-1")


def test_only_blocked_names_a_ticket() -> None:
    with pytest.raises(ValueError, match="only a BLOCKED"):
        Status(CellStatus.TODO, "EM-1")


def test_a_blocked_status_prints_as_written_in_the_map() -> None:
    assert str(Status(CellStatus.BLOCKED, "EM-190")) == "BLOCKED(EM-190)"
    assert str(Status(CellStatus.TODO)) == "TODO"


def test_rejections_blocked_and_validated_are_terminal_but_todo_and_candidate_are_not() -> None:
    assert CellStatus.PAPER_REJECT.is_terminal and CellStatus.PAPER_REJECT.is_rejection
    assert CellStatus.BLOCKED.is_terminal and not CellStatus.BLOCKED.is_rejection
    assert CellStatus.VALIDATED.is_terminal
    assert not CellStatus.TODO.is_terminal
    assert not CellStatus.CANDIDATE.is_terminal


# --- cells and foundations ------------------------------------------------------------------------


def test_a_cell_id_starts_with_its_lane() -> None:
    with pytest.raises(ValueError, match="must start with its lane"):
        SearchCell("L4-gap", "L3", "gap", None, (), Status(CellStatus.TODO), (), "")
    with pytest.raises(ValueError, match="cell id"):
        cell("gap")


def test_a_cell_hypothesis_is_a_slug() -> None:
    with pytest.raises(ValueError, match="slug"):
        SearchCell("L3-gap", "L3", "Gap Up", None, (), Status(CellStatus.TODO), (), "")


def test_a_rejected_cell_records_its_lesson_and_its_experiments() -> None:
    with pytest.raises(ValueError, match="lesson"):
        cell(status=Status(CellStatus.INFEASIBLE), experiments=("EXP-1",))
    with pytest.raises(ValueError, match="experiment ids"):
        cell(status=Status(CellStatus.INFEASIBLE), lesson="moves too small")


def test_a_lesson_is_one_line() -> None:
    with pytest.raises(ValueError, match="one line"):
        cell(lesson="first\nsecond")


def test_a_done_foundation_names_its_ticket() -> None:
    with pytest.raises(ValueError, match="names the ticket"):
        Foundation("F1", "map", Status(FoundationStatus.DONE), None)
    with pytest.raises(ValueError, match="EM key"):
        Foundation("F1", "map", Status(FoundationStatus.DONE), "X-1")


# --- the map as a whole ---------------------------------------------------------------------------


def test_ids_are_unique() -> None:
    with pytest.raises(ValueError, match="duplicate cell"):
        SearchMap([LANE], [], [cell(), cell()])
    with pytest.raises(ValueError, match="duplicate lane"):
        SearchMap([LANE, LANE], [], [])


def test_every_reference_resolves() -> None:
    with pytest.raises(ValueError, match="unknown lane"):
        SearchMap([], [], [cell()])
    with pytest.raises(ValueError, match="unknown parent"):
        SearchMap([LANE], [], [cell(parent="L3-missing")])
    with pytest.raises(ValueError, match="unknown foundation"):
        SearchMap([LANE], [], [cell(requires=("D9",))])


def test_a_parent_chain_may_not_loop() -> None:
    with pytest.raises(ValueError, match="loops"):
        SearchMap([LANE], [], [cell("L3-a", parent="L3-b"), cell("L3-b", parent="L3-a")])


def test_children_are_found_by_parent() -> None:
    search_map = SearchMap([LANE], [], [rejected("L3-a"), cell("L3-b", parent="L3-a")])

    assert [c.cell_id for c in search_map.children("L3-a")] == ["L3-b"]


def test_a_todo_cell_is_runnable_only_once_its_foundations_are_done() -> None:
    done = Foundation("F3", "screener", Status(FoundationStatus.DONE), "EM-200")

    assert not SearchMap([LANE], [F3], [cell(requires=("F3",))]).is_runnable(cell(requires=("F3",)))
    assert SearchMap([LANE], [done], [cell(requires=("F3",))]).is_runnable(cell(requires=("F3",)))
    assert not SearchMap([LANE], [], [rejected()]).is_runnable(rejected())


def test_exhausted_only_when_every_cell_is_terminal() -> None:
    blocked = cell("L3-b", status=Status(CellStatus.BLOCKED, "EM-9"))

    assert SearchMap([LANE], [], [rejected("L3-a"), blocked]).is_exhausted()
    assert not SearchMap([LANE], [], [rejected("L3-a"), cell("L3-c")]).is_exhausted()
    assert not SearchMap([LANE], [], []).is_exhausted()


def test_status_counts_cover_every_status() -> None:
    counts = SearchMap([LANE], [], [rejected("L3-a"), cell("L3-b")]).status_counts()

    assert counts[CellStatus.SCREEN_REJECT] == 1
    assert counts[CellStatus.TODO] == 1
    assert counts[CellStatus.VALIDATED] == 0


def test_the_accessors_return_copies() -> None:
    search_map = SearchMap([LANE], [F3], [cell()])

    assert search_map.lanes["L3"] == LANE
    assert search_map.foundations["F3"] == F3
    assert "L3-gap" in search_map.cells


# --- the loader -----------------------------------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "search-map.yaml"
    path.write_text(dedent(text), encoding="utf-8")
    return path


VALID = """
    lanes:
      - {id: L3, priority: P1, name: Gaps}
    foundations:
      - {id: F3, name: screener, status: DONE, ticket: EM-200}
      - {id: D8, name: fees, status: BLOCKED(EM-190)}
    cells:
      - id: L3-gap
        lane: L3
        hypothesis: gap
        status: SCREEN_REJECT
        requires: [F3]
        experiments: [EXP-1]
        lesson: gross negative
      - {id: L3-gap-fade, lane: L3, hypothesis: gap-fade, parent: L3-gap, status: BLOCKED(EM-9)}
"""


def test_the_loader_reads_a_valid_map(tmp_path: Path) -> None:
    search_map = SearchMapLoader().load(write(tmp_path, VALID))

    child = search_map.cells["L3-gap-fade"]
    assert child.parent == "L3-gap"
    assert child.status == Status(CellStatus.BLOCKED, "EM-9")
    assert search_map.foundations["D8"].status.blocked_by == "EM-190"
    assert search_map.cells["L3-gap"].experiments == ("EXP-1",)
    assert search_map.is_exhausted()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]", "must be a mapping"),
        ("lanes: [\n", "not valid YAML"),
        ("lanes: []\nfoundations: []\n", "missing cells"),
        ("lanes: []\nfoundations: []\ncells: []\nextra: 1\n", "unknown key"),
        ("lanes: [1]\nfoundations: []\ncells: []\n", "list of mappings"),
        ("lanes: [{id: L3, priority: P9, name: x}]\nfoundations: []\ncells: []\n", "P1-P3"),
        ("lanes: [{id: L3, priority: P1}]\nfoundations: []\ncells: []\n", "missing name"),
        (
            "lanes: []\nfoundations: [{id: F1, name: x, status: MAYBE}]\ncells: []\n",
            "is not one of",
        ),
        (
            "lanes: []\nfoundations: [{id: F1, name: x, status: BLOCKED()}]\ncells: []\n",
            "is not one of",
        ),
        (
            "lanes: [{id: L3, priority: P1, name: x}]\nfoundations: []\n"
            "cells: [{id: L3-a, lane: L3, hypothesis: a, status: TODO, requires: F1}]\n",
            "must be a list",
        ),
        (
            "lanes: [{id: L3, priority: P1, name: x}]\nfoundations: []\n"
            "cells: [{id: L3-a, lane: L3, hypothesis: a, status: TODO, verdict: x}]\n",
            "unknown key",
        ),
        (
            "lanes: []\nfoundations: []\n"
            "cells: [{id: L3-a, lane: L3, hypothesis: a, status: TODO}]\n",
            "unknown lane",
        ),
    ],
)
def test_the_loader_refuses_a_malformed_map(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        SearchMapLoader().load(write(tmp_path, text))


def test_the_loader_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot read"):
        SearchMapLoader().load(tmp_path / "absent.yaml")
