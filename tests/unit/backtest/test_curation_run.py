"""EM-188: a curation run reserves its holdout BEFORE anything is planned or read, and records what
its numbers were produced from (dataset, chosen behaviour hashes, cost breakdown)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.cost_breakdown import CostBreakdownCalculator
from emporos.backtest.curation import CurationRecord, SelectionCriteria, StrategyCurator
from emporos.backtest.curation_run import CurationRun, PlannedStrategy, WindowPlan
from emporos.backtest.pricing import PassThroughGate
from emporos.backtest.robustness.assessment import RobustnessAssessor
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.robustness.holdout import FinalHoldoutReservation
from emporos.backtest.robustness.portfolio_economics import PortfolioCostModel
from emporos.backtest.robustness.trials import TrialStatistics
from emporos.backtest.tuning import NET_PNL, ParameterCandidate
from emporos.backtest.universe import AsOfInstruments, InstrumentEra
from emporos.domain.instruments import Exchange, Instrument
from emporos.domain.money import Money
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import WORKED_DAY, FixedSchedule, bars, config, registry
from tests.support.strategies import T0

FIRST_DAY, LAST_DAY = date(2026, 1, 5), date(2026, 1, 22)  # 18 sessions of data
HOLDOUT_DAYS = 4
CANDIDATES = (
    ParameterCandidate("early", {"buy_at": 2, "sell_at": 5}),
    ParameterCandidate("later", {"buy_at": 3, "sell_at": 5}),
)
PLAN = WindowPlan(
    FIRST_DAY, LAST_DAY, timedelta(days=4), timedelta(days=2), embargo=timedelta(days=1)
)


async def trial_statistics() -> TrialStatistics:
    return TrialStatistics(35, 0, None)


def instruments() -> AsOfInstruments:
    instrument = Instrument(Exchange.NSE, "1001", "TEST-EQ", "Test", 1, Money.of("0.05"))
    return AsOfInstruments([InstrumentEra(instrument, T0 - timedelta(days=400), None)])


def candles() -> InMemoryCandles:
    sessions = (FIRST_DAY - date(2026, 1, 5)).days, (LAST_DAY - date(2026, 1, 5)).days
    return InMemoryCandles(
        [bar for day in range(sessions[0], sessions[1] + 1) for bar in bars(WORKED_DAY, day=day)]
    )


def run_of(
    reader: InMemoryCandles,
    holdout: FinalHoldoutReservation | None,
    costs: CostBreakdownCalculator | None = None,
) -> CurationRun:
    return CurationRun(
        reader, registry(), instruments(), FixedSchedule.as_source, PassThroughGate,
        StrategyCurator(SelectionCriteria()), NET_PNL,
        assessors=lambda _backtester, _resolver: RobustnessAssessor(
            BenchmarkLoader().load(), trial_statistics
        ),
        holdout=holdout, costs=costs,
    )  # fmt: skip


async def curate(
    holdout: FinalHoldoutReservation | None, costs: CostBreakdownCalculator | None = None
) -> tuple[CurationRecord, InMemoryCandles]:
    reader = candles()
    planned = PlannedStrategy("buy_then_sell", lambda _resolver: config(), CANDIDATES)
    [record] = await run_of(reader, holdout, costs).run([planned], PLAN, Money.of("100000"))
    return record, reader


def reservation() -> FinalHoldoutReservation:
    return FinalHoldoutReservation(timedelta(days=HOLDOUT_DAYS))


class TestReservedHoldout:
    async def test_the_robustness_provenance_records_the_holdout(self) -> None:
        record, _ = await curate(reservation())

        assert record.robustness is not None
        provenance = record.robustness.provenance
        assert provenance is not None
        _, plan_end = PLAN.bounds()
        assert provenance.holdout.end == plan_end
        assert provenance.holdout.end - provenance.holdout.start == timedelta(days=HOLDOUT_DAYS)

    async def test_no_walk_forward_window_reaches_the_holdout(self) -> None:
        record, _ = await curate(reservation())

        assert record.robustness is not None and record.robustness.provenance is not None
        provenance = record.robustness.provenance
        assert provenance.validation.end <= provenance.holdout.start
        assert provenance.research.end <= provenance.holdout.start

    async def test_no_bar_of_the_holdout_is_ever_read(self) -> None:
        record, reader = await curate(reservation())

        assert record.robustness is not None and record.robustness.provenance is not None
        holdout_start = record.robustness.provenance.holdout.start
        assert reader.reads, "the run should have read bars"
        assert all(end <= holdout_start for _, _, _, end in reader.reads)

    async def test_the_dataset_stops_at_the_last_walkable_day(self) -> None:
        record, _ = await curate(reservation())

        assert record.provenance is not None
        assert record.provenance.dataset_first == FIRST_DAY
        assert record.provenance.dataset_last == LAST_DAY - timedelta(days=HOLDOUT_DAYS)

    async def test_a_reservation_that_leaves_nothing_to_walk_is_refused(self) -> None:
        with pytest.raises(ValueError):
            await curate(FinalHoldoutReservation(timedelta(days=60)))


class TestNoHoldout:
    async def test_provenance_is_absent_and_the_whole_range_is_walkable(self) -> None:
        record, reader = await curate(None)

        assert record.robustness is not None
        assert record.robustness.provenance is None
        assert record.provenance is not None and record.provenance.dataset_last == LAST_DAY
        assert max(end for _, _, _, end in reader.reads) > PLAN.bounds()[1] - timedelta(
            days=HOLDOUT_DAYS
        )


class TestWhatTheRecordCarries:
    async def test_a_behaviour_hash_for_every_chosen_candidate(self) -> None:
        record, _ = await curate(reservation())

        chosen = {name for _, _, name in record.windows}
        assert set(record.behaviour_hashes) == chosen
        assert all(h.startswith("sha256:") for h in record.behaviour_hashes.values())

    async def test_costs_are_absent_without_a_cost_model(self) -> None:
        record, _ = await curate(reservation())

        assert record.costs is None

    async def test_the_cost_breakdown_adds_up(self) -> None:
        model = PortfolioCostModel(FixedSchedule().schedule_for(FIRST_DAY), Decimal(2), Decimal(5))

        record, _ = await curate(reservation(), CostBreakdownCalculator(model))

        costs = record.costs
        assert costs is not None and record.pooled.trades
        assert costs.total == costs.brokerage + costs.statutory + costs.spread + costs.slippage
        assert costs.total > 0
        assert costs.per_trade_inr == costs.total / len(record.pooled.trades)
