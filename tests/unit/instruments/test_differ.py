import pytest

from emporos.domain.money import Money
from emporos.instruments.differ import InstrumentDiffer
from tests.support.instrument_rows import instrument


def test_identical_masters_produce_an_empty_diff() -> None:
    current = [instrument("1"), instrument("2")]

    diff = InstrumentDiffer().diff(current, list(reversed(current)))

    assert diff.is_empty
    assert diff.summary() == "0 added, 0 changed, 0 removed"


def test_new_missing_and_modified_instruments_are_classified() -> None:
    kept, renamed, dropped, new = instrument("1"), instrument("2"), instrument("3"), instrument("4")
    renamed_after = instrument("2", tradingsymbol="NEWNAME-EQ")

    diff = InstrumentDiffer().diff([kept, renamed, dropped], [kept, renamed_after, new])

    assert diff.added == (new,)
    assert diff.removed == (dropped,)
    assert [(c.before, c.after) for c in diff.changed] == [(renamed, renamed_after)]
    assert not diff.is_empty
    assert diff.summary() == "1 added, 1 changed, 1 removed"


@pytest.mark.parametrize(
    "override",
    [
        {"tradingsymbol": "RENAMED-EQ"},
        {"name": "Renamed Co"},
        {"lot_size": 5},
        {"tick_size": Money.of("0.01")},
    ],
)
def test_a_change_to_any_tracked_field_is_a_change(override: dict[str, object]) -> None:
    diff = InstrumentDiffer().diff([instrument("1")], [instrument("1", **override)])

    assert len(diff.changed) == 1
    assert not diff.added and not diff.removed


def test_a_tick_size_that_differs_only_in_representation_is_not_a_change() -> None:
    diff = InstrumentDiffer().diff(
        [instrument("1", tick_size=Money.of("0.05"))],
        [instrument("1", tick_size=Money.of("0.050"))],
    )

    assert diff.is_empty


def test_results_are_ordered_by_instrument_id_for_deterministic_writes() -> None:
    diff = InstrumentDiffer().diff([], [instrument("3"), instrument("1"), instrument("2")])

    assert [i.token for i in diff.added] == ["1", "2", "3"]
