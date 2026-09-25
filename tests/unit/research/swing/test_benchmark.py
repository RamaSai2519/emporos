"""EM-223: the same-universe equal-weight buy-and-hold, through the same simulator and costs."""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.unit.research.swing.support import dataset, free_costs, series, sessions

from emporos.research.swing.benchmark import EqualWeightBenchmark
from emporos.research.swing.costs import SwingCostModel
from emporos.research.swing.rules import Membership

DAYS = sessions(5)
X, Y = "NSE:1", "NSE:2"


def flat_then(last: str) -> list[tuple[str, str]]:
    return [("100", "100")] * 4 + [("100", last)]


def test_it_holds_every_name_equally_and_never_sells_until_the_end() -> None:
    data = dataset(series(X, DAYS, flat_then("110")), series(Y, DAYS, flat_then("90")))

    run = EqualWeightBenchmark(data, free_costs(), notional_per_name=Decimal(10_000)).run()

    assert run.capital == 20_000
    assert sorted(t.instrument_id for t in run.trades) == [X, Y]
    assert all(t.quantity == 100 for t in run.trades)
    assert run.equity[-1] == 20_000 + 1_000 - 1_000
    assert all(t.exit_day == DAYS[-1] for t in run.trades)  # sold only at the final liquidation


def test_a_name_that_starts_later_is_bought_when_it_does_at_an_equal_slot() -> None:
    late = series(Y, DAYS[2:], [("100", "100")] * 3)
    data = dataset(series(X, DAYS, [("100", "100")] * 5), late)

    run = EqualWeightBenchmark(data, free_costs(), notional_per_name=Decimal(10_000)).run()

    by_name = {t.instrument_id: t for t in run.trades}
    assert by_name[X].entry_day == DAYS[1]
    assert by_name[Y].entry_day == DAYS[3]  # decided at its first close, filled the next open


def test_it_pays_the_same_costs_as_an_arm_would() -> None:
    from tests.unit.research.swing.test_simulator import _delivery

    from emporos.research.swing.costs import BENCHMARK

    data = dataset(series(X, DAYS, [("500", "500")] * 5))
    cheap = EqualWeightBenchmark(data, free_costs(), notional_per_name=Decimal(50_000)).run()
    costly = EqualWeightBenchmark(
        data, SwingCostModel(_delivery(), BENCHMARK), notional_per_name=Decimal(50_000)
    ).run()

    assert cheap.equity[-1] == 50_000
    assert costly.equity[-1] < 50_000
    assert costly.fees > 0


def test_membership_keeps_a_name_out_of_the_benchmark_too() -> None:
    class OnlyX:
        def is_member(self, instrument_id: str, day: object) -> bool:
            return instrument_id == X

    membership: Membership = OnlyX()  # type: ignore[assignment]
    data = dataset(series(X, DAYS, [("100", "100")] * 5), series(Y, DAYS, [("100", "100")] * 5))

    run = EqualWeightBenchmark(data, free_costs(), membership, Decimal(10_000)).run()

    assert [t.instrument_id for t in run.trades] == [X]


def test_a_slot_is_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        EqualWeightBenchmark(
            dataset(series(X, DAYS, flat_then("100"))), free_costs(), None, Decimal(0)
        )


def test_an_empty_dataset_has_no_benchmark() -> None:
    with pytest.raises(ValueError, match="at least one name"):
        EqualWeightBenchmark(dataset(), free_costs()).run()
