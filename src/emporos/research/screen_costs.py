"""What a screened trade costs under a benchmark cost scenario (EM-191 F3).

The scenarios are the ones `config/robustness/benchmark.yaml` declares, not copies of them, so the
screener and a curation are priced by the same numbers. A scenario's slippage is charged per side
(the benchmark's own slippage plus the scenario's extra); the statutory charges come from the dated
fee schedule through `research.costs.TransactionCostModel`, scaled by the scenario's fee
multiplier, on the shares the declared size actually buys.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.costs import ScheduleSource
from emporos.backtest.robustness.benchmark import BenchmarkConfig
from emporos.domain.fees import FeeSchedule
from emporos.research.costs import TransactionCostModel
from emporos.research.screen_trades import ScreenTrade

__all__ = ["ScreenCostModel", "ScreenCostScenario"]

_BPS = Decimal(10_000)


@dataclass(frozen=True)
class ScreenCostScenario:
    name: str
    fee_multiplier: Decimal
    slippage_bps_per_side: Decimal

    def __post_init__(self) -> None:
        if self.fee_multiplier <= 0 or self.slippage_bps_per_side < 0:
            raise ValueError("a scenario needs a positive fee multiplier and non-negative slippage")

    @classmethod
    def from_benchmark(cls, config: BenchmarkConfig, name: str) -> ScreenCostScenario:
        declared = config.scenario(name)
        return cls(
            name,
            declared.fee_multiplier,
            config.slippage_bps + declared.extra_slippage_bps,
        )


class ScreenCostModel:
    def __init__(self, schedules: ScheduleSource) -> None:
        self._schedules = schedules
        self._statutory: dict[str, TransactionCostModel] = {}

    def round_trip_fraction(
        self, trade: ScreenTrade, quantity: int, scenario: ScreenCostScenario
    ) -> Decimal:
        """Charges and slippage of the round trip as a fraction of the position's value."""
        statutory = self._statutory_model(
            self._schedules.schedule_for(trade.day)
        ).round_trip_fraction(trade.exchange, quantity, trade.entry_price)
        return statutory * scenario.fee_multiplier + scenario.slippage_bps_per_side * 2 / _BPS

    def _statutory_model(self, schedule: FeeSchedule) -> TransactionCostModel:
        key = f"{schedule.name}@{schedule.effective_from}"
        if key not in self._statutory:
            self._statutory[key] = TransactionCostModel(schedule)  # no slippage: scenarios add it
        return self._statutory[key]
