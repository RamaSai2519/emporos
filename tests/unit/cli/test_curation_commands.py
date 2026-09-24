"""EM-141: `backtest curate --only <name not in the plan>` is refused, not silently empty."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from emporos.backtest.robustness.benchmark import BenchmarkLoader
from emporos.backtest.robustness.holdout import FinalHoldoutReservation
from emporos.backtest.robustness.portfolio_economics import PortfolioCostModel
from emporos.cli.curation_commands import PlanCostModel, PlanHoldout
from emporos.cli.main import app
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.portfolio.fee_schedules import FeeScheduleLibrary

runner = CliRunner()


def invoke(*args: str):  # type: ignore[no-untyped-def]
    return runner.invoke(app, list(args))


def test_only_with_a_name_not_in_the_plan_is_refused_before_anything_runs() -> None:
    result = invoke(
        "backtest", "curate", "--from", "2026-01-05", "--to", "2026-01-09",
        "--only", "not_a_real_strategy",
    )  # fmt: skip

    assert result.exit_code == 1 and "curation failed" in result.output
    assert "not_a_real_strategy" in result.output
    assert "orb_v1" in result.output  # the available names are listed


def test_only_with_one_real_and_one_unknown_name_is_still_refused() -> None:
    result = invoke(
        "backtest", "curate", "--from", "2026-01-05", "--to", "2026-01-09",
        "--only", "orb_v1", "--only", "not_a_real_strategy",
    )  # fmt: skip

    assert result.exit_code == 1 and "not_a_real_strategy" in result.output


class TestPlanHoldout:
    def test_the_shipped_plan_declares_a_holdout(self) -> None:
        plan = yaml.safe_load(Path("config/curation/plan.yaml").read_text(encoding="utf-8"))

        reservation = PlanHoldout.reservation(plan)

        assert reservation == FinalHoldoutReservation(timedelta(days=42))

    def test_a_plan_without_holdout_days_reserves_nothing(self) -> None:
        assert PlanHoldout.reservation({"train_days": 130}) is None

    @pytest.mark.parametrize("bad", [0, -3, "42", 4.5, True])
    def test_a_holdout_that_is_not_a_positive_whole_number_of_days_is_refused(
        self, bad: object
    ) -> None:
        with pytest.raises(ValueError, match="holdout_days"):
            PlanHoldout.reservation({"holdout_days": bad})


class TestPlanCostModel:
    def test_the_model_uses_the_benchmarks_spread_and_slippage(self) -> None:
        benchmark = BenchmarkLoader().load()

        model = PlanCostModel.of(FeeScheduleLibrary.from_directory(), benchmark, date(2026, 9, 18))

        cost = model.components_for(Exchange.NSE, 10, Money.of("100"))
        assert cost.slippage.amount == Decimal(1000) * benchmark.slippage_bps * 2 / Decimal(10_000)
        assert cost.spread.amount == Decimal(1000) * benchmark.spread_bps * 2 / Decimal(10_000)

    def test_a_run_before_every_schedule_is_priced_with_the_oldest(self) -> None:
        library = FeeScheduleLibrary.from_directory()

        model = PlanCostModel.of(library, BenchmarkLoader().load(), date(2001, 1, 1))

        oldest = PortfolioCostModel(library.earliest, Decimal(0), Decimal(0))
        assert model.components_for(Exchange.NSE, 10, Money.of("100")).brokerage == (
            oldest.components_for(Exchange.NSE, 10, Money.of("100")).brokerage
        )
