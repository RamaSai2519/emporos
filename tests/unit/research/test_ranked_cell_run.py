"""EM-216: a ranked cell keeps only the day's top signals across the universe, from signals before
fills, never on a quarantined day, and screens what it kept through the ordinary ledger."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from emporos.backtest.costs import EarliestBeforeFirst
from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.sizing import DeclaredSize
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.daily_selection import DailyTopKSelection
from emporos.research.partition import DataSplit
from emporos.research.ranked_cell_run import RankedArmResult, RankedCellScreenRun, RankedScan
from emporos.research.scans.base import ScanExecution
from emporos.research.scans.top_shock import ShockCandidate
from emporos.research.screen_costs import ScreenCostModel, ScreenCostScenario
from emporos.research.screen_evaluator import ScreenEvaluator
from emporos.research.screen_ledger import InMemoryScreenLedger
from emporos.research.screen_trades import ScreenTrade

MONDAY, TUESDAY = date(2024, 1, 8), date(2024, 1, 9)
SPLIT = DataSplit("test", date(2024, 1, 1), date(2024, 12, 31))


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 25, tzinfo=UTC)


class Universe:
    instrument_ids = ("NSE:1", "NSE:2", "NSE:3")
    universe_label = "test-3"

    def is_quarantined(self, instrument_id: str, day: date) -> bool:
        return (instrument_id, day) == ("NSE:3", TUESDAY)


class NoBars:
    def __init__(self) -> None:
        self.asked: list[str] = []

    def bars(self, instrument_id: str, split: DataSplit) -> Sequence[Candle]:
        self.asked.append(instrument_id)
        return []


def trade(instrument: str, day: date, side: OrderSide = OrderSide.SELL) -> ScreenTrade:
    return ScreenTrade(instrument, day, side, Money.of(100), Money.of(99))


class ScriptedScan:
    """Per instrument: the trades it fills and the signals it gives (filled or not)."""

    name = "scripted"

    def __init__(
        self,
        trades: Mapping[str, list[ScreenTrade]],
        signals: Mapping[str, list[ShockCandidate]],
    ) -> None:
        self._trades = trades
        self._signals = signals
        self._seen: list[ShockCandidate] = []

    @property
    def candidates(self) -> Sequence[ShockCandidate]:
        return tuple(self._seen)

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        self._seen.extend(self._signals.get(instrument_id, []))
        return list(self._trades.get(instrument_id, []))


def evaluator() -> ScreenEvaluator:
    config = BenchmarkLoader().load()
    return ScreenEvaluator(
        ScreenCostModel(EarliestBeforeFirst(FeeScheduleLibrary.from_directory())),
        ScreenCostScenario.from_benchmark(config, "benchmark"),
        ScreenCostScenario.from_benchmark(config, config.adverse_scenario),
    )


def run(scan: ScriptedScan, ledger: InMemoryScreenLedger | None = None) -> RankedArmResult:
    def factory(point: Mapping[str, str], execution: ScanExecution) -> RankedScan:
        return scan

    runner = RankedCellScreenRun(
        factory, DailyTopKSelection(1), evaluator(), ledger or InMemoryScreenLedger(),
        FixedClock(), Universe(), SPLIT,
    )  # fmt: skip
    size = DeclaredSize(Decimal(50_000))
    (result,) = runner.run("l17-test", [{"arm": "a"}], NoBars(), size)
    return result


def signal(instrument: str, day: date, score: str) -> ShockCandidate:
    return ShockCandidate(instrument, day, Decimal(score))


def test_only_the_days_top_signal_is_screened() -> None:
    scan = ScriptedScan(
        {"NSE:1": [trade("NSE:1", MONDAY)], "NSE:2": [trade("NSE:2", MONDAY)]},
        {"NSE:1": [signal("NSE:1", MONDAY, "0.02")], "NSE:2": [signal("NSE:2", MONDAY, "0.04")]},
    )

    result = run(scan)

    assert result.arm.result.trades == 1
    assert (result.candidate_days, result.chosen) == (1, 1)


def test_a_chosen_signal_that_did_not_fill_leaves_the_day_empty() -> None:
    scan = ScriptedScan(
        {"NSE:1": [trade("NSE:1", MONDAY)]},  # NSE:2's bigger signal never filled
        {"NSE:1": [signal("NSE:1", MONDAY, "0.02")], "NSE:2": [signal("NSE:2", MONDAY, "0.04")]},
    )

    assert run(scan).arm.result.trades == 0


def test_a_quarantined_day_never_competes() -> None:
    """NSE:3's Tuesday is an unadjusted action: its huge 'gap' must not take the slot."""
    scan = ScriptedScan(
        {"NSE:1": [trade("NSE:1", TUESDAY)], "NSE:3": [trade("NSE:3", TUESDAY)]},
        {"NSE:1": [signal("NSE:1", TUESDAY, "0.02")], "NSE:3": [signal("NSE:3", TUESDAY, "0.14")]},
    )

    result = run(scan)

    assert result.arm.result.trades == 1
    assert result.arm.quarantined_dropped == 1


def test_sides_are_counted_so_a_long_only_pass_is_visible() -> None:
    scan = ScriptedScan(
        {"NSE:1": [trade("NSE:1", MONDAY, OrderSide.BUY), trade("NSE:1", TUESDAY)]},
        {"NSE:1": [signal("NSE:1", MONDAY, "0.02"), signal("NSE:1", TUESDAY, "0.02")]},
    )

    result = run(scan)

    assert (result.long_trades, result.short_trades) == (1, 1)


def test_every_arm_is_one_counted_look() -> None:
    ledger = InMemoryScreenLedger()
    run(ScriptedScan({}, {}), ledger)

    assert ledger.count() == 1
