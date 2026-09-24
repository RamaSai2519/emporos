"""EM-191 F3: the fast screener: sizing, costs, the fixed S2 bar, and the ledger that counts it."""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.backtest.costs import EarliestBeforeFirst, ScheduleSource
from emporos.backtest.metrics.trades import TradeAnalyzer
from emporos.backtest.portfolio import ClosedTrade, TradeDirection
from emporos.backtest.robustness.benchmark import BenchmarkConfig, BenchmarkLoader
from emporos.backtest.robustness.cost_sensitivity import CostSensitivity
from emporos.core.errors import ConfigurationError
from emporos.domain.fees import IntradayCharges
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.portfolio.fee_schedules import FeeScheduleLibrary
from emporos.research.screen_costs import ScreenCostModel, ScreenCostScenario
from emporos.research.screen_evaluator import (
    ScreenBar,
    ScreenEvaluator,
    ScreenResult,
    ScreenVerdict,
)
from emporos.research.screen_ledger import (
    InMemoryScreenLedger,
    JsonlScreenLedger,
    ScreenIdentity,
    ScreenLedgerCounter,
    ScreenRecord,
)
from emporos.research.screen_trades import DeclaredValueSizer, ScreenTrade
from emporos.research.screener import Screener

SIZE = Decimal(25_000)
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def benchmark() -> BenchmarkConfig:
    return BenchmarkLoader().load()


def schedules() -> ScheduleSource:
    return EarliestBeforeFirst(FeeScheduleLibrary.from_directory())


def evaluator(bar: ScreenBar | None = None) -> ScreenEvaluator:
    config = benchmark()
    return ScreenEvaluator(
        ScreenCostModel(schedules()),
        ScreenCostScenario.from_benchmark(config, "benchmark"),
        ScreenCostScenario.from_benchmark(config, config.adverse_scenario),
        bar,
    )


def trade(
    move: str,
    *,
    day: date = date(2026, 9, 21),
    instrument: str = "NSE:2885",
    side: OrderSide = OrderSide.BUY,
    entry: str = "1000",
) -> ScreenTrade:
    entry_price = Decimal(entry)
    exit_price = entry_price * (1 + Decimal(move) * (1 if side is OrderSide.BUY else -1))
    return ScreenTrade(instrument, day, side, Money.of(entry_price), Money.of(exit_price))


def winners(count: int = 400) -> list[ScreenTrade]:
    """Big, varied wins spread over three years and four names: clears every S2 check."""
    moves = ["0.012", "0.018", "0.024"]
    return [
        trade(
            moves[i % 3],
            day=date(2024 + i % 3, 3, 4),
            instrument=f"NSE:{100 + i % 4}",
        )
        for i in range(count)
    ]


# --- a trade and its size -----------------------------------------------------------------------


def test_a_long_and_a_short_capture_the_move_in_their_own_direction() -> None:
    assert trade("0.01").gross_return == Decimal("0.01")
    assert trade("0.01", side=OrderSide.SELL).gross_return == Decimal("0.01")
    down = ScreenTrade("NSE:1", date(2026, 9, 21), OrderSide.SELL, Money.of(100), Money.of(101))
    assert down.gross_return == Decimal("-0.01")


def test_a_trade_needs_positive_prices() -> None:
    with pytest.raises(ValueError, match="positive"):
        ScreenTrade("NSE:1", date(2026, 9, 21), OrderSide.BUY, Money.of(0), Money.of(1))


def test_the_sizer_rounds_down_and_skips_what_one_share_overshoots() -> None:
    sizer = DeclaredValueSizer(SIZE)

    assert sizer.quantity(Money.of("1000")) == 25
    assert sizer.quantity(Money.of("1001")) == 24
    assert sizer.quantity(Money.of("30000")) == 0
    with pytest.raises(ValueError, match="positive"):
        DeclaredValueSizer(Decimal(0))


# --- costs come from the benchmark --------------------------------------------------------------


def test_scenarios_are_the_benchmarks_own_numbers() -> None:
    config = benchmark()

    base = ScreenCostScenario.from_benchmark(config, "benchmark")
    adverse = ScreenCostScenario.from_benchmark(config, config.adverse_scenario)

    assert (base.fee_multiplier, base.slippage_bps_per_side) == (Decimal(1), Decimal(5))
    assert (adverse.fee_multiplier, adverse.slippage_bps_per_side) == (Decimal("1.5"), Decimal(15))


def test_the_adverse_scenario_costs_more_than_the_benchmark_on_the_same_trade() -> None:
    config = benchmark()
    costs = ScreenCostModel(schedules())
    one = trade("0.01")

    base = costs.round_trip_fraction(
        one, 25, ScreenCostScenario.from_benchmark(config, "benchmark")
    )
    adverse = costs.round_trip_fraction(
        one, 25, ScreenCostScenario.from_benchmark(config, config.adverse_scenario)
    )

    assert Decimal("0.0030") < base < Decimal("0.0045")  # plan §1.1: 0.324% at 25k
    assert adverse > base


def test_a_scenario_cannot_have_a_zero_fee_multiplier() -> None:
    with pytest.raises(ValueError, match="fee multiplier"):
        ScreenCostScenario("x", Decimal(0), Decimal(5))


# --- the S2 bar ---------------------------------------------------------------------------------


def test_the_s2_bar_is_the_plans_numbers() -> None:
    bar = ScreenBar()

    assert bar.feasibility_multiple == Decimal(2)  # §3.5
    assert bar.min_net_t == Decimal(3)
    assert bar.min_trades == 300
    assert bar.min_positive_year_share == Decimal("0.6")
    assert bar.max_top_instrument_share == Decimal("0.5")


def screen(trades: list[ScreenTrade]) -> ScreenResult:
    return evaluator().evaluate(trades, DeclaredValueSizer(SIZE), advisory=False)


def test_a_strong_broad_scan_clears_every_check() -> None:
    result = screen(winners())

    assert result.passed, result.failed_checks
    assert result.trades == 400
    assert result.gross_mean is not None and result.adverse_break_even is not None
    assert result.gross_mean >= result.adverse_break_even


def test_a_scan_that_only_beats_the_benchmark_but_not_the_adverse_breakeven_fails() -> None:
    result = screen(
        [
            trade(
                "0.004" if i % 2 else "0.006",
                day=date(2024 + i % 3, 3, 4),
                instrument=f"NSE:{i % 4}",
            )
            for i in range(600)
        ]
    )

    assert result.net_mean is not None and result.net_mean > 0
    assert "gross >= adverse break-even" in result.failed_checks


def test_a_losing_scan_fails_on_net_expectancy() -> None:
    result = screen([trade("0.001", instrument=f"NSE:{i % 4}") for i in range(400)])

    assert "net expectancy > 0" in result.failed_checks
    assert not result.passed


def test_too_few_trades_fail_however_good_they_are() -> None:
    assert "trades >= 300" in screen(winners(299)).failed_checks


def test_one_instrument_carrying_the_profit_fails() -> None:
    lopsided = [trade("0.02", day=date(2024 + i % 3, 3, 4), instrument="NSE:1") for i in range(390)]
    lopsided += [
        trade("0.0001", day=date(2024, 3, 4), instrument=f"NSE:{2 + i}") for i in range(10)
    ]

    assert any("instrument" in name for name in screen(lopsided).failed_checks)


def test_a_profit_from_one_year_only_fails() -> None:
    years = [trade("0.03", day=date(2024, 3, 4), instrument=f"NSE:{i % 4}") for i in range(350)]
    years += [trade("-0.001", day=date(2025, 3, 4), instrument=f"NSE:{i % 4}") for i in range(40)]
    years += [trade("-0.001", day=date(2026, 3, 4), instrument=f"NSE:{i % 4}") for i in range(40)]

    assert any("years" in name for name in screen(years).failed_checks)


def test_no_trades_is_a_result_that_cannot_pass() -> None:
    result = screen([])

    assert (result.trades, result.checks, result.passed) == (0, (), False)


def test_unaffordable_trades_are_counted_and_not_taken() -> None:
    dear = trade("0.02", entry="40000")

    result = screen([dear, *winners()])

    assert result.skipped_unaffordable == 1
    assert result.trades == 400


def test_the_screen_is_advisory_until_it_is_verified_against_curate() -> None:
    ledger = InMemoryScreenLedger()
    identity = ScreenIdentity("h", {}, "u", date(2024, 1, 1), date(2026, 1, 1), SIZE)

    unverified = Screener(evaluator(), ledger, FixedClock()).screen(identity, winners())
    verified = Screener(evaluator(), ledger, FixedClock(), verified_by_parity=True).screen(
        identity, winners()
    )

    assert unverified.advisory and not verified.advisory


# --- every screen is a look ---------------------------------------------------------------------


def identity(**changes: object) -> ScreenIdentity:
    base: dict[str, object] = {
        "hypothesis": "gap-fade", "parameters": {"k": "1"}, "universe": "u1",
        "first_day": date(2024, 1, 1), "last_day": date(2026, 1, 1), "position_value": SIZE,
    }  # fmt: skip
    return ScreenIdentity(**{**base, **changes})  # type: ignore[arg-type]


def test_a_screen_is_identified_by_what_it_looked_at() -> None:
    assert identity().screen_id == identity().screen_id
    assert identity().screen_id.startswith("SCR-")
    for change in (
        {"hypothesis": "other"}, {"parameters": {"k": "2"}}, {"universe": "u2"},
        {"first_day": date(2024, 2, 1)}, {"last_day": date(2026, 2, 1)},
        {"position_value": Decimal(50_000)},
    ):  # fmt: skip
        assert identity(**change).screen_id != identity().screen_id


def test_the_same_screen_twice_is_one_look(tmp_path: Path) -> None:
    ledger = JsonlScreenLedger(tmp_path / "screens.jsonl")
    screener = Screener(evaluator(), ledger, FixedClock())

    screener.screen(identity(), winners())
    screener.screen(identity(), winners())
    screener.screen(identity(parameters={"k": "2"}), winners())

    assert ledger.count() == 2
    assert len((tmp_path / "screens.jsonl").read_text().splitlines()) == 2


def test_a_screen_is_recorded_win_or_lose_and_survives_a_reopen(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "screens.jsonl"
    Screener(evaluator(), JsonlScreenLedger(path), FixedClock()).screen(
        identity(), [trade("0.0001") for _ in range(5)]
    )

    reopened = JsonlScreenLedger(path)

    assert reopened.count() == 1
    assert reopened.record(ScreenRecord(identity(), screen([]), NOW)) is False


def test_a_malformed_ledger_line_is_refused_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "screens.jsonl"
    path.write_text('{"screen_id": "SCR-1"}\nnot json\n', encoding="utf-8")

    with pytest.raises(ConfigurationError, match=":2:"):
        JsonlScreenLedger(path).count()


def test_no_ledger_file_yet_counts_zero(tmp_path: Path) -> None:
    assert JsonlScreenLedger(tmp_path / "absent.jsonl").count() == 0


async def test_the_counter_reports_the_screens_to_program_n() -> None:
    ledger = InMemoryScreenLedger()
    Screener(evaluator(), ledger, FixedClock()).screen(identity(), winners())

    counter = ScreenLedgerCounter(ledger)

    assert counter.name == "screens"
    assert await counter.count() == 1


# --- parity with `backtest curate` (evaluator level) --------------------------------------------


def curate_view(
    references: list[ScreenTrade], config: BenchmarkConfig
) -> tuple[list[ClosedTrade], Decimal]:
    """The trades as a backtest books them: fills at the reference price moved by the run's own
    slippage buffer, real charges on both fills, P&L and fees per round trip."""
    buffer = config.slippage_bps / Decimal(10_000)
    library = schedules()
    closed: list[ClosedTrade] = []
    for ref in references:
        quantity = int(SIZE // ref.entry_price.amount)
        long = ref.side is OrderSide.BUY
        entry = ref.entry_price.amount * (1 + buffer if long else 1 - buffer)
        exit_ = ref.exit_price.amount * (1 - buffer if long else 1 + buffer)
        entry, exit_ = entry.quantize(Decimal("0.05")), exit_.quantize(Decimal("0.05"))
        calculator = IntradayCharges(library.schedule_for(ref.day))
        buy, sell = (OrderSide.BUY, OrderSide.SELL) if long else (OrderSide.SELL, OrderSide.BUY)
        fees = (
            calculator.for_trade(
                ref.exchange, buy, quantity, Money.of(entry if long else exit_)
            ).total
            + calculator.for_trade(
                ref.exchange, sell, quantity, Money.of(exit_ if long else entry)
            ).total
        )
        move = (exit_ - entry) if long else (entry - exit_)
        opened = datetime.combine(ref.day, datetime.min.time(), tzinfo=UTC)
        closed.append(
            ClosedTrade(
                ref.instrument_id, TradeDirection.LONG if long else TradeDirection.SHORT, quantity,
                opened, opened + timedelta(hours=1), Money.of(entry), Money.of(exit_),
                Money(move * quantity), fees, "run",
            )
        )  # fmt: skip
    expectancy = TradeAnalyzer().analyze(closed).expectancy
    assert expectancy is not None
    return closed, expectancy


def references() -> list[ScreenTrade]:
    rng = random.Random(20260924)
    out: list[ScreenTrade] = []
    for i in range(300):
        price = Decimal(rng.randrange(200, 3000))
        move = Decimal(rng.randrange(-150, 250)) / Decimal(10_000)
        out.append(
            trade(
                str(move),
                day=date(2026, 9, 21 + i % 5),
                instrument=f"NSE:{i % 7}",
                side=OrderSide.BUY if i % 2 else OrderSide.SELL,
                entry=str(price),
            )
        )
    return out


def test_the_screener_agrees_with_curate_on_the_same_trades() -> None:
    """The plan's F3 parity bar, at the evaluator: net expectancy per trade within 10% relative
    (or 0.02% absolute when it is near zero) of what `backtest curate` reports for the same trades.
    Signal-level parity against the real engine is F3b."""
    config = benchmark()
    refs = references()
    _, curate_expectancy = curate_view(refs, config)

    screened = evaluator().evaluate(refs, DeclaredValueSizer(SIZE), advisory=False)

    assert screened.net_mean is not None
    tolerance = max(abs(curate_expectancy) * Decimal("0.10"), Decimal("0.0002"))
    assert abs(screened.net_mean - curate_expectancy) <= tolerance


def test_the_screener_agrees_with_curates_adverse_repricing_too() -> None:
    config = benchmark()
    refs = references()
    closed, _ = curate_view(refs, config)
    (adverse,) = CostSensitivity().evaluate(closed, [config.scenario(config.adverse_scenario)])
    notional = sum((t.entry_notional.amount for t in closed), Decimal(0))
    curate_adverse = adverse.net_pnl / notional

    adverse_scenario = ScreenCostScenario.from_benchmark(config, config.adverse_scenario)
    screened = ScreenEvaluator(
        ScreenCostModel(schedules()), adverse_scenario, adverse_scenario
    ).evaluate(refs, DeclaredValueSizer(SIZE), advisory=False)

    assert screened.net_mean is not None
    tolerance = max(abs(curate_adverse) * Decimal("0.10"), Decimal("0.0002"))
    assert abs(screened.net_mean - curate_adverse) <= tolerance


# --- S1 feasibility (§3.5) ----------------------------------------------------------------------


def test_a_scan_whose_typical_move_is_below_twice_the_adverse_cost_is_infeasible() -> None:
    small = [
        trade("0.006", day=date(2024 + i % 3, 3, 4), instrument=f"NSE:{i % 4}") for i in range(600)
    ]

    result = screen(small)

    assert result.verdict is ScreenVerdict.INFEASIBLE
    assert result.checks[0].name.startswith("S1")
    assert result.median_abs_move == Decimal("0.006")
    assert result.feasibility_bar is not None and result.feasibility_bar > Decimal(
        "0.012"
    )  # ~1.27%


def test_the_median_not_the_mean_decides_feasibility() -> None:
    """A few huge moves must not make a set feasible whose typical move is tiny."""
    trades = [trade("0.002", instrument=f"NSE:{i % 4}") for i in range(500)]
    trades += [trade("0.5", instrument=f"NSE:{i % 4}") for i in range(20)]

    assert screen(trades).verdict is ScreenVerdict.INFEASIBLE


def test_a_feasible_scan_that_fails_a_later_check_is_a_screen_reject_not_infeasible() -> None:
    lopsided = [trade("0.02", day=date(2024 + i % 3, 3, 4), instrument="NSE:1") for i in range(390)]
    lopsided += [trade("0.0001", instrument=f"NSE:{2 + i}") for i in range(10)]

    result = screen(lopsided)

    assert result.checks[0].passed and result.verdict is ScreenVerdict.SCREEN_REJECT


def test_a_broad_strong_scan_passes_both_stages() -> None:
    assert screen(winners()).verdict is ScreenVerdict.PASS


def test_no_trades_is_a_reject_with_no_feasibility_numbers() -> None:
    result = screen([])

    assert result.verdict is ScreenVerdict.SCREEN_REJECT and result.median_abs_move is None


def test_the_median_of_an_even_count_is_the_middle_pair_mean() -> None:
    trades = [trade(m) for m in ("0.01", "0.02", "0.03", "0.04")]

    assert screen(trades).median_abs_move == Decimal("0.025")


def test_the_ledger_line_carries_the_verdict_and_the_s1_numbers(tmp_path: Path) -> None:
    path = tmp_path / "screens.jsonl"
    Screener(evaluator(), JsonlScreenLedger(path), FixedClock()).screen(identity(), winners())

    line = json.loads(path.read_text().splitlines()[0])

    assert line["verdict"] == "PASS" and line["median_abs_move"] is not None
    assert line["gross_mean"] is not None
