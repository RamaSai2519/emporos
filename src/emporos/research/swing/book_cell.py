"""A4 (config/experiments/a4-momentum-rotation-book.yaml) as buildable sleeves (EM-233).

The declaration fixes both components before A5 ran: the equity sleeve is the A1b arm
`rebalance=weekly vol_target=15`, the defensive sleeve is the A5 arm `score=blend top_k=2`, and the
grid is the split (equity/defensive). This recipe owns none of those numbers beyond the two named
arms; it reuses the A1b and A5 cells so a sleeve is exactly what those cells build.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar

from emporos.research.swing.benchmark import EqualWeightBenchmark
from emporos.research.swing.book_screen import SleeveWorld
from emporos.research.swing.cells import BookRiskMomentumCell, CellEnvironment, EtfRotationCell
from emporos.research.swing.costs import SwingCostModel
from emporos.research.swing.regime import YearlyCalendar
from emporos.research.swing.rules import Membership, SwingStrategy
from emporos.research.swing.simulator import SwingRun
from emporos.research.swing.weighted import WeightedBuyHold

__all__ = ["SleeveBookCell", "SleeveInputs"]

EQUITY = "equity"
DEFENSIVE = "defensive"


@dataclass(frozen=True)
class SleeveInputs:
    """What the defensive sleeve and both benchmarks need that the two environments do not carry."""

    etf_benchmark_weights: Mapping[str, Decimal]
    etf_cash_yield: Decimal
    capital: Decimal
    start_day: date
    etf_exempt: frozenset[str]
    stock_membership: Membership | None = None  # None: every D1 name, whenever it has bars


class SleeveBookCell:
    slug = "a4-momentum-rotation-book"
    EQUITY_ARM: ClassVar[Mapping[str, str]] = {"rebalance": "weekly", "vol_target": "15"}
    DEFENSIVE_ARM: ClassVar[Mapping[str, str]] = {"score": "blend", "top_k": "2"}

    @staticmethod
    def weights(point: Mapping[str, str]) -> tuple[Decimal, Decimal]:
        """`split=60/40` is (equity, defensive) = (0.60, 0.40)."""
        equity, defensive = (Decimal(p) / 100 for p in point["split"].split("/"))
        return equity, defensive

    def sleeves(
        self, stock: CellEnvironment, etf: CellEnvironment, inputs: SleeveInputs
    ) -> tuple[SleeveWorld, SleeveWorld]:
        equity_cell, defensive_cell = BookRiskMomentumCell(), EtfRotationCell()

        def equity_benchmark(costs: SwingCostModel) -> SwingRun:
            return EqualWeightBenchmark(
                stock.dataset, costs, inputs.stock_membership, start_day=inputs.start_day
            ).run()

        def defensive_benchmark(costs: SwingCostModel) -> SwingRun:
            return WeightedBuyHold(
                etf.dataset, inputs.etf_benchmark_weights, costs, YearlyCalendar(), inputs.capital,
                inputs.etf_cash_yield, inputs.start_day,
            ).run()  # fmt: skip

        return (
            SleeveWorld(
                EQUITY, stock.dataset,
                lambda: equity_cell.strategy(self.EQUITY_ARM, stock),
                equity_cell.max_positions(self.EQUITY_ARM), Decimal(0), equity_benchmark,
                membership=inputs.stock_membership,
            ),
            SleeveWorld(
                DEFENSIVE, etf.dataset,
                lambda: defensive_cell.strategy(self.DEFENSIVE_ARM, etf),
                defensive_cell.max_positions(self.DEFENSIVE_ARM), inputs.etf_cash_yield,
                defensive_benchmark, inputs.etf_exempt,
            ),
        )  # fmt: skip

    @staticmethod
    def sleeve_notes(strategies: Mapping[str, SwingStrategy]) -> str:
        """Each sleeve's own record: the equity sleeve's halts, kills and re-entries, the months the
        defensive sleeve held each asset."""
        return "; ".join(
            (
                f"{EQUITY}: {BookRiskMomentumCell().arm_notes(strategies[EQUITY])}",
                f"{DEFENSIVE}: {EtfRotationCell().arm_notes(strategies[DEFENSIVE])}",
            )
        )

    @staticmethod
    def effective_start(
        strategies: Mapping[str, SwingStrategy], env: CellEnvironment
    ) -> date | None:
        return BookRiskMomentumCell().effective_start(strategies[EQUITY], env)
