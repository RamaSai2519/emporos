"""EM-224: as-of membership, rebuilt backwards from current constituents and the changes."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from emporos.core.errors import ConfigurationError
from emporos.research.index_membership import (
    AsOfMembership,
    IndexChange,
    MembershipTimeline,
    load_changes,
)

N100 = "NIFTY 100"
D1, D2, D3 = date(2020, 3, 31), date(2021, 3, 31), date(2022, 3, 31)


def change(day: date, added: str = "", removed: str = "", index: str = N100) -> IndexChange:
    return IndexChange(
        index, day, frozenset(added.split()), frozenset(removed.split()), "https://example fetched"
    )


class TestChange:
    def test_a_change_needs_a_source_and_something_to_change(self) -> None:
        with pytest.raises(ValueError, match="source"):
            IndexChange(N100, D1, frozenset({"A"}), frozenset(), " ")
        with pytest.raises(ValueError, match="adds or removes"):
            IndexChange(N100, D1, frozenset(), frozenset(), "s")

    def test_a_name_cannot_be_added_and_removed_at_once(self) -> None:
        with pytest.raises(ValueError, match="both"):
            IndexChange(N100, D1, frozenset({"A"}), frozenset({"A"}), "s")


class TestTimeline:
    # today: {A, B, C}. 2022-03-31: C added, D removed. 2021-03-31: B added, E removed.
    TIMELINE = MembershipTimeline(
        N100, {"A", "B", "C"}, [change(D3, "C", "D"), change(D2, "B", "E")]
    )

    def test_today_is_the_current_set(self) -> None:
        assert self.TIMELINE.members_on(date(2026, 1, 1)) == {"A", "B", "C"}
        assert self.TIMELINE.members_on(D3) == {"A", "B", "C"}  # the change applies from its date

    def test_between_the_changes_it_is_the_set_before_the_later_one(self) -> None:
        assert self.TIMELINE.members_on(date(2022, 3, 30)) == {"A", "B", "D"}
        assert self.TIMELINE.members_on(D2) == {"A", "B", "D"}

    def test_before_every_change_names_added_later_are_out_and_names_removed_later_are_in(
        self,
    ) -> None:
        assert self.TIMELINE.members_on(date(2021, 3, 30)) == {"A", "D", "E"}
        assert self.TIMELINE.members_on(date(2010, 1, 1)) == {"A", "D", "E"}

    def test_it_reports_where_it_starts_to_know(self) -> None:
        assert self.TIMELINE.coverage_start == D2
        assert MembershipTimeline(N100, {"A"}, []).coverage_start is None

    def test_a_consistent_history_has_no_inconsistencies(self) -> None:
        assert self.TIMELINE.inconsistencies == []

    def test_an_added_name_that_is_not_a_member_afterwards_is_flagged(self) -> None:
        timeline = MembershipTimeline(N100, {"A"}, [change(D1, added="Z")])

        assert timeline.inconsistencies == [
            f"{N100} {D1}: Z was added but is not a member after it"
        ]

    def test_a_removed_name_that_is_still_a_member_is_flagged(self) -> None:
        timeline = MembershipTimeline(N100, {"A", "Z"}, [change(D1, removed="Z")])

        assert timeline.inconsistencies == [f"{N100} {D1}: Z was removed but is a member after it"]

    def test_changes_to_another_index_are_ignored(self) -> None:
        timeline = MembershipTimeline(N100, {"A"}, [change(D1, "B", index="NIFTY MIDCAP 150")])

        assert timeline.members_on(date(2019, 1, 1)) == {"A"}
        assert timeline.coverage_start is None

    def test_changes_may_be_given_in_any_order(self) -> None:
        shuffled = MembershipTimeline(
            N100, {"A", "B", "C"}, [change(D2, "B", "E"), change(D3, "C", "D")]
        )

        assert shuffled.members_on(date(2021, 3, 30)) == self.TIMELINE.members_on(date(2021, 3, 30))


class TestAsOfMembership:
    def build(self) -> AsOfMembership:
        n100 = MembershipTimeline(N100, {"A", "B"}, [change(D2, added="B")])
        mid = MembershipTimeline(
            "NIFTY MIDCAP 150", {"C", "D"}, [change(D1, added="D", index="NIFTY MIDCAP 150")]
        )
        return AsOfMembership([n100, mid], {"A": "NSE:1", "B": "NSE:2", "C": "NSE:3", "D": "NSE:4"})

    def test_membership_is_the_union_of_the_indices_by_instrument_id(self) -> None:
        m = self.build()

        assert m.is_member("NSE:2", date(2026, 1, 1))
        assert not m.is_member("NSE:2", date(2020, 6, 1))  # B joined NIFTY 100 in 2021
        assert m.is_member("NSE:4", date(2020, 6, 1))
        assert not m.is_member("NSE:4", date(2020, 1, 1))  # D joined the midcap index in 2020
        assert m.is_member("NSE:1", date(2010, 1, 1))

    def test_a_symbol_with_no_instrument_is_never_tradable(self) -> None:
        m = AsOfMembership([MembershipTimeline(N100, {"A", "X"}, [])], {"A": "NSE:1"})

        assert m.is_member("NSE:1", date(2026, 1, 1))
        assert not m.is_member("NSE:9", date(2026, 1, 1))

    def test_coverage_starts_at_the_latest_first_record_and_is_unknown_without_one(self) -> None:
        assert self.build().coverage_start == D2
        empty = AsOfMembership([MembershipTimeline(N100, {"A"}, [])], {})
        assert empty.coverage_start is None

    def test_it_gathers_every_indexs_inconsistencies(self) -> None:
        bad = MembershipTimeline(N100, {"A"}, [change(D1, added="Z")])
        good = MembershipTimeline("NIFTY MIDCAP 150", {"C"}, [])

        assert len(AsOfMembership([bad, good], {}).inconsistencies) == 1


class TestFile:
    def test_it_loads_changes_with_their_sources(self, tmp_path: Path) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(
            "changes:\n- {index: NIFTY 100, effective: 2021-03-31, added: [A], removed: [B],"
            " source: 'https://x fetched 2026-09-25'}\n",
            encoding="utf-8",
        )

        (loaded,) = load_changes(path)

        assert loaded.added == {"A"}
        assert loaded.removed == {"B"}
        assert loaded.effective == D2
        assert "fetched" in loaded.source

    @pytest.mark.parametrize(
        "body",
        [
            "changes:\n- {index: X, effective: 2021-03-31, added: [A]}",
            "changes:\n- {index: X, effective: 2021-03-31, added: [A], source: s, extra: 1}",
            "change: []",
            "changes:\n- {index: X, effective: 2021-03-31, source: s}",
        ],
    )
    def test_a_malformed_file_is_refused(self, tmp_path: Path, body: str) -> None:
        path = tmp_path / "c.yaml"
        path.write_text(body + "\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="index-change file"):
            load_changes(path)

    def test_the_shipped_file_is_empty_and_loads(self) -> None:
        assert load_changes() == []


def test_it_gates_what_the_swing_simulator_is_offered() -> None:
    from decimal import Decimal

    from tests.unit.research.swing.support import Scripted, dataset, free_costs, series, sessions

    from emporos.research.swing.simulator import SwingConfig, SwingSimulator

    days = sessions(4)
    data = dataset(series("NSE:1", days, [("1", "1")] * 4), series("NSE:2", days, [("1", "1")] * 4))
    membership = AsOfMembership(
        [MembershipTimeline(N100, {"A", "B"}, [change(days[2], added="B")])],
        {"A": "NSE:1", "B": "NSE:2"},
    )
    strategy = Scripted(lambda c: [])

    SwingSimulator(data, strategy, SwingConfig(Decimal(1000), 1), free_costs(), membership).run()

    assert [set(c.tradable) for c in strategy.seen] == [{"NSE:1"}, {"NSE:1"}, {"NSE:1", "NSE:2"}]
