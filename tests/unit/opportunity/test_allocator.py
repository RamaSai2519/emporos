from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.positions import Position
from emporos.opportunity.allocator import (
    Allocation,
    AllocationConstraints,
    PortfolioAllocator,
    SectorClassifier,
)
from emporos.opportunity.models import OpportunityCandidate
from emporos.risk.snapshot import AccountFacts
from emporos.strategies.regime import MarketRegime
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0, make_signal

THIRD_INSTRUMENT = "NSE:1003"


def _candidate(
    instrument_id: str = INSTRUMENT,
    strategy_name: str = "momentum_v1",
    entry: str = "100",
    stop: str = "98",
    target: str = "104",
    side: OrderSide = OrderSide.BUY,
    confidence: Decimal = Decimal(1),
) -> OpportunityCandidate:
    return OpportunityCandidate(
        strategy_name=strategy_name,
        instrument_id=instrument_id,
        signal=make_signal(instrument_id=instrument_id, price=entry, side=side),
        entry=Money.of(entry),
        stop=Money.of(stop),
        target=Money.of(target),
        regime=MarketRegime.TRENDING,
        generated_at=T0,
        confidence=confidence,
    )


def _constraints(**overrides: object) -> AllocationConstraints:
    defaults: dict[str, object] = {
        "production_capital": Money.of("50000"),
        "max_simultaneous_positions": 3,
        "max_risk_per_trade": Money.of("1000"),
        "max_portfolio_risk": Money.of("3000"),
    }
    defaults.update(overrides)
    return AllocationConstraints(**defaults)  # type: ignore[arg-type]


def test_production_capital_must_be_positive() -> None:
    with pytest.raises(ValueError, match="production capital"):
        _constraints(production_capital=Money.zero())


def test_max_simultaneous_positions_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_simultaneous_positions"):
        _constraints(max_simultaneous_positions=0)


def test_max_risk_per_trade_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_risk_per_trade"):
        _constraints(max_risk_per_trade=Money.zero())


def test_max_portfolio_risk_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_portfolio_risk"):
        _constraints(max_portfolio_risk=Money.zero())


def test_max_sector_exposure_must_be_positive_when_set() -> None:
    with pytest.raises(ValueError, match="max_sector_exposure"):
        _constraints(max_sector_exposure=Money.zero())


def test_an_allocation_needs_positive_quantity() -> None:
    with pytest.raises(ValueError, match="positive quantity"):
        Allocation(_candidate(), 0)


def test_no_slots_left_when_position_cap_is_already_met() -> None:
    allocator = PortfolioAllocator()
    held = Position(instrument_id=OTHER_INSTRUMENT, net_quantity=5, average_price=Money.of("50"))

    allocations = allocator.allocate(
        (_candidate(),),
        account=AccountFacts(positions={OTHER_INSTRUMENT: held}),
        constraints=_constraints(max_simultaneous_positions=1),
    )

    assert allocations == ()


def test_capital_already_overcommitted_leaves_nothing_available() -> None:
    allocator = PortfolioAllocator()
    held = Position(
        instrument_id=OTHER_INSTRUMENT, net_quantity=1000, average_price=Money.of("100")
    )

    allocations = allocator.allocate(
        (_candidate(instrument_id=INSTRUMENT),),
        account=AccountFacts(positions={OTHER_INSTRUMENT: held}),
        constraints=_constraints(max_simultaneous_positions=5),
    )

    assert allocations == ()


def test_default_sector_classifier_disables_the_sector_check() -> None:
    allocator = PortfolioAllocator()
    candidate = _candidate(entry="100", stop="98")

    allocations = allocator.allocate(
        (candidate,),
        account=AccountFacts(),
        constraints=_constraints(max_sector_exposure=Money.of("1")),
    )

    assert len(allocations) == 1  # unclassified instruments are never capped by sector


def test_risk_budget_exhausted_by_earlier_candidates_skips_the_rest() -> None:
    allocator = PortfolioAllocator()
    first = _candidate(instrument_id=INSTRUMENT, entry="100", stop="98")
    second = _candidate(instrument_id=OTHER_INSTRUMENT, entry="100", stop="98")

    allocations = allocator.allocate(
        (first, second),
        account=AccountFacts(),
        constraints=_constraints(
            max_risk_per_trade=Money.of("100"), max_portfolio_risk=Money.of("100")
        ),
    )

    assert len(allocations) == 1


def test_a_single_candidate_is_sized_by_risk_per_share() -> None:
    allocator = PortfolioAllocator()
    candidate = _candidate(entry="100", stop="98")  # risk = 2/share

    allocations = allocator.allocate(
        (candidate,),
        account=AccountFacts(),
        constraints=_constraints(max_risk_per_trade=Money.of("200")),
    )

    assert len(allocations) == 1
    assert allocations[0].quantity == 100  # 200 / 2


def test_capital_availability_can_cap_quantity_below_the_risk_budget() -> None:
    allocator = PortfolioAllocator()
    candidate = _candidate(entry="100", stop="98")

    allocations = allocator.allocate(
        (candidate,),
        account=AccountFacts(),
        constraints=_constraints(
            production_capital=Money.of("500"), max_risk_per_trade=Money.of("1000")
        ),
    )

    assert allocations[0].quantity == 5  # 500 / 100


def test_max_simultaneous_positions_limits_how_many_are_allocated() -> None:
    allocator = PortfolioAllocator()
    candidates = (
        _candidate(instrument_id=INSTRUMENT),
        _candidate(instrument_id=OTHER_INSTRUMENT),
        _candidate(instrument_id=THIRD_INSTRUMENT),
    )

    allocations = allocator.allocate(
        candidates,
        account=AccountFacts(),
        constraints=_constraints(max_simultaneous_positions=2, max_risk_per_trade=Money.of("100")),
    )

    assert len(allocations) == 2


def test_existing_open_positions_count_against_the_position_cap() -> None:
    allocator = PortfolioAllocator()
    held = Position(instrument_id=INSTRUMENT, net_quantity=10, average_price=Money.of("90"))
    candidates = (
        _candidate(instrument_id=OTHER_INSTRUMENT),
        _candidate(instrument_id=THIRD_INSTRUMENT),
    )

    allocations = allocator.allocate(
        candidates,
        account=AccountFacts(positions={INSTRUMENT: held}),
        constraints=_constraints(max_simultaneous_positions=2),
    )

    assert len(allocations) == 1


def test_duplicate_instrument_candidates_keep_only_the_best_ranked() -> None:
    allocator = PortfolioAllocator()
    strong = _candidate(instrument_id=INSTRUMENT, entry="100", stop="99", target="104")  # edge 4
    weak = _candidate(instrument_id=INSTRUMENT, entry="100", stop="95", target="102")  # edge 0.4

    allocations = allocator.allocate(
        (strong, weak), account=AccountFacts(), constraints=_constraints()
    )

    assert len(allocations) == 1
    assert allocations[0].candidate is strong


def test_candidates_below_the_minimum_edge_are_dropped() -> None:
    allocator = PortfolioAllocator()
    weak = _candidate(entry="100", stop="99", target="100.5")  # edge 0.5

    allocations = allocator.allocate(
        (weak,), account=AccountFacts(), constraints=_constraints(min_expected_edge=Decimal(1))
    )

    assert allocations == ()


def test_portfolio_risk_budget_is_shared_across_candidates() -> None:
    allocator = PortfolioAllocator()
    first = _candidate(instrument_id=INSTRUMENT, entry="100", stop="98")  # risk 2/share
    second = _candidate(instrument_id=OTHER_INSTRUMENT, entry="100", stop="98")

    allocations = allocator.allocate(
        (first, second),
        account=AccountFacts(),
        constraints=_constraints(
            max_risk_per_trade=Money.of("100"),
            max_portfolio_risk=Money.of("300"),  # shared budget, each trade capped at 100
        ),
    )

    total_risk = sum((a.committed_risk.amount for a in allocations), Decimal(0))
    assert total_risk <= Decimal("300")
    assert len(allocations) == 2


def test_a_zero_quantity_allocation_is_skipped_not_returned() -> None:
    allocator = PortfolioAllocator()
    candidate = _candidate(entry="100", stop="98")

    allocations = allocator.allocate(
        (candidate,),
        account=AccountFacts(),
        constraints=_constraints(production_capital=Money.of("1")),  # can't afford even 1 share
    )

    assert allocations == ()


def test_allocation_signal_carries_the_sized_quantity() -> None:
    allocator = PortfolioAllocator()
    candidate = _candidate(entry="100", stop="98")

    allocations = allocator.allocate(
        (candidate,),
        account=AccountFacts(),
        constraints=_constraints(max_risk_per_trade=Money.of("20")),
    )

    assert allocations[0].signal.quantity == 10
    assert allocations[0].signal.instrument_id == candidate.instrument_id


class _FixedSectorClassifier(SectorClassifier):
    def __init__(self, sector: str) -> None:
        self._sector = sector

    def sector_of(self, instrument_id: str) -> str | None:
        return self._sector


def test_sector_exposure_cap_limits_combined_size_within_a_sector() -> None:
    allocator = PortfolioAllocator(sectors=_FixedSectorClassifier("banking"))
    first = _candidate(instrument_id=INSTRUMENT, entry="100", stop="99")
    second = _candidate(instrument_id=OTHER_INSTRUMENT, entry="100", stop="99")

    allocations = allocator.allocate(
        (first, second),
        account=AccountFacts(),
        constraints=_constraints(
            max_risk_per_trade=Money.of("10000"),
            max_portfolio_risk=Money.of("10000"),
            max_sector_exposure=Money.of("150"),
        ),
    )

    total_capital = sum((a.committed_capital.amount for a in allocations), Decimal(0))
    assert total_capital <= Decimal("150")
