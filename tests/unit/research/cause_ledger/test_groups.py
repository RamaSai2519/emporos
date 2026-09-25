"""EM-244: the business-group map: dated membership, the day-before rule, sources on every row."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from emporos.research.cause_ledger.groups import GroupMember, YamlGroupMap

SRC = "https://example.org/x"


def row(
    group: str, symbol: str, first: date | None = None, last: date | None = None
) -> GroupMember:
    return GroupMember(group, symbol, first, last, SRC, date(2026, 9, 26))


def test_membership_is_dated_start_inclusive_end_exclusive() -> None:
    groups = YamlGroupMap(
        [row("g", "A"), row("g", "B", date(2022, 9, 16)), row("g", "C", None, date(2025, 7, 18))]
    )

    assert groups.members("g", date(2022, 9, 15)) == ("A", "C")
    assert groups.members("g", date(2022, 9, 16)) == ("A", "B", "C")
    assert groups.members("g", date(2025, 7, 17)) == ("A", "B", "C")
    assert groups.members("g", date(2025, 7, 18)) == ("A", "B")


def test_a_session_reads_membership_as_of_the_day_before() -> None:
    groups = YamlGroupMap([row("g", "A"), row("g", "B", date(2022, 9, 16))])

    # B joins on the 16th: the 16th's own session does not see it, the 17th's does.
    assert groups.members_for_session("g", date(2022, 9, 16)) == ("A",)
    assert groups.members_for_session("g", date(2022, 9, 17)) == ("A", "B")
    assert groups.peers_for_session("A", date(2022, 9, 16)) == ()
    assert groups.peers_for_session("A", date(2022, 9, 17)) == ("B",)


def test_a_group_of_one_is_not_a_group_and_peers_exclude_the_name() -> None:
    groups = YamlGroupMap([row("g", "A"), row("g", "B"), row("solo", "C"), row("h", "A")])

    assert groups.group_ids(date(2024, 1, 1)) == ("g",)
    assert groups.groups_of("A", date(2024, 1, 1)) == ("g", "h")
    assert groups.peers("A", date(2024, 1, 1)) == ("B",)
    assert groups.peers("Z", date(2024, 1, 1)) == ()


def test_a_row_without_a_source_or_with_an_empty_span_is_refused() -> None:
    with pytest.raises(ValueError, match="source"):
        YamlGroupMap([GroupMember("g", "A", None, None, "", date(2026, 9, 26))])
    with pytest.raises(ValueError, match="before"):
        YamlGroupMap([row("g", "A", date(2024, 2, 1), date(2024, 1, 1))])


def test_the_committed_map_uses_only_d1_names_and_known_dates() -> None:
    groups = YamlGroupMap.load()
    with Path("config/universe/d1/tokens.csv").open(encoding="utf-8", newline="") as handle:
        known = {r["Symbol"] for r in csv.DictReader(handle)}

    assert groups.symbols() <= known
    assert len(groups.group_ids(date(2024, 6, 1))) == 14
    assert "AMBUJACEM" not in groups.members("adani", date(2022, 9, 15))
    assert "AMBUJACEM" in groups.members("adani", date(2022, 9, 16))
    assert "AWL" in groups.members("adani", date(2025, 7, 17))
    assert "AWL" not in groups.members("adani", date(2025, 7, 18))
    assert groups.peers("HDFCAMC", date(2024, 1, 2)) == ("HDFCBANK",)
    assert "JINDALSTEL" not in groups.symbols() and "IDEA" not in groups.symbols()
