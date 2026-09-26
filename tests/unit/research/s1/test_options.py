"""E2 options book: contract choice (amendment A), the modelled premium, the engine on hand-built
index sessions with a fake prior settle, and the model-error hook against recorded quotes."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from emporos.eventtrader.replay.records import Scenario
from emporos.options.fo_costs import FoFeeSchedule, FoFeeScheduleLibrary
from emporos.research.iv.black76 import Right, black76_price
from emporos.research.iv.dataset import IvRow
from emporos.research.s1.arms import Arm, option_arms
from emporos.research.s1.bars import DayBars, MemoryBarStore
from emporos.research.s1.engine import BookLimits
from emporos.research.s1.exit_policies import ExitReason
from emporos.research.s1.model_error import (
    ModelErrorMeter,
    RecordedQuote,
    load_recorded_quotes,
    pooled,
    quote_price,
)
from emporos.research.s1.option_contracts import (
    Contract,
    ContractPicker,
    ExpiryKind,
    ListedExpiry,
)
from emporos.research.s1.option_engine import OptionBookEngine, ProgramOptionCosts
from emporos.research.s1.option_model import ModelInputs, PremiumModel
from emporos.research.s1.option_prior import IvPriorSession
from emporos.research.s1.signals import Signal

DAY = date(2024, 3, 4)  # a Monday
NIFTY = "NSE:99926000"
WEEKLY = ListedExpiry(date(2024, 3, 7), ExpiryKind.WEEKLY)
MONTHLY = ListedExpiry(date(2024, 3, 28), ExpiryKind.MONTHLY)
INPUTS = ModelInputs(spot=22_000.0, forward=22_020.0, atm_iv=0.14, rate=0.065)
ARM = Arm("options", "one_to_one", 1, 0.010, "lock_fixed")
ZERO = Decimal(0)


class Calendar:
    def __init__(self, listed: Sequence[ListedExpiry]) -> None:
        self._listed = list(listed)

    def listed(self, underlying: str, day: date) -> Sequence[ListedExpiry]:
        return self._listed


class Lots:
    def lot(self, underlying: str, day: date) -> int | None:
        return {"NIFTY": 25, "BANKNIFTY": 15}.get(underlying)


class Prior:
    def __init__(self, inputs: ModelInputs | None = INPUTS) -> None:
        self._inputs = inputs

    def inputs(self, contract: Contract, day: date) -> ModelInputs | None:
        return self._inputs


class FlatOptionCosts:
    """Rs 10 a side and a tenth of a percent of the premium turnover; adverse doubles it."""

    def cost(
        self, contract: Contract, units: int, entry: float, exit_: float, day: date,
        scenario: Scenario,
    ) -> float:  # fmt: skip
        base = 20.0 + 0.001 * units * (entry + exit_)
        return base if scenario is Scenario.BENCHMARK else 2 * base


def picker(*listed: ListedExpiry) -> ContractPicker:
    return ContractPicker(Calendar(listed), Lots())


def spot_day(closes: list[float], spread: float = 3.0) -> DayBars:
    n = len(closes)
    opens = [closes[0], *closes[:-1]]
    return DayBars(
        np.array([9 * 60 + 20 + 5 * i for i in range(n)]),
        np.array(opens), np.array([max(o, c) + spread for o, c in zip(opens, closes, strict=True)]),
        np.array([min(o, c) - spread for o, c in zip(opens, closes, strict=True)]),
        np.array(closes), np.full(n, 1e12),
    )  # fmt: skip


class TestContractPicker:
    def test_the_nearest_weekly_at_the_money_call_for_an_up_move(self) -> None:
        c = picker(MONTHLY, WEEKLY).pick("NIFTY", DAY, 22_013.0, 1)

        assert isinstance(c, Contract)
        assert (c.expiry, c.kind, c.strike, c.right, c.lot) == (
            WEEKLY.expiry, ExpiryKind.WEEKLY, 22_000.0, Right.CALL, 25,
        )  # fmt: skip

    def test_a_down_move_buys_the_put_and_strikes_round_to_the_index_step(self) -> None:
        c = picker(WEEKLY).pick("BANKNIFTY", DAY, 46_949.0, -1)

        assert isinstance(c, Contract) and (c.strike, c.right) == (46_900.0, Right.PUT)

    def test_an_expiry_on_the_day_has_no_full_session_left_and_is_passed_over(self) -> None:
        on_the_day = ListedExpiry(DAY, ExpiryKind.WEEKLY)
        c = picker(on_the_day, WEEKLY).pick("NIFTY", DAY, 22_000.0, 1)

        assert isinstance(c, Contract) and c.expiry == WEEKLY.expiry

    def test_amendment_a_no_weekly_takes_the_nearest_monthly(self) -> None:
        c = picker(MONTHLY).pick("BANKNIFTY", DAY, 46_900.0, 1)

        assert isinstance(c, Contract) and c.kind is ExpiryKind.MONTHLY

    def test_reasons_when_there_is_no_contract(self) -> None:
        assert picker().pick("NIFTY", DAY, 22_000.0, 1) == "no_expiry"
        assert picker(WEEKLY).pick("FINNIFTY", DAY, 22_000.0, 1) == "no_step"
        assert (
            ContractPicker(Calendar([WEEKLY]), Lots(), {"X": 50.0}).pick("X", DAY, 100.0, 1)
            == "no_lot"
        )


def contract(right: Right = Right.CALL) -> Contract:
    return Contract("NIFTY", WEEKLY.expiry, ExpiryKind.WEEKLY, 22_000.0, right, 25)


class TestPremiumModel:
    def test_it_is_black76_on_the_spot_with_the_prior_basis_and_minutes_to_expiry(self) -> None:
        model = PremiumModel(contract(), INPUTS)
        minutes = 3 * 24 * 60 + 15 * 60 + 30 - 10 * 60
        expected = black76_price(
            22_000.0 * 22_020.0 / 22_000.0, 22_000.0, minutes / (365 * 24 * 60), 0.065, 0.14,
            Right.CALL,
        )  # fmt: skip

        assert abs(model.premium(22_000.0, DAY, 10 * 60) - expected) < 1e-9

    def test_a_call_rises_and_a_put_falls_with_the_spot_and_decay_shrinks_both(self) -> None:
        call, put = PremiumModel(contract(), INPUTS), PremiumModel(contract(Right.PUT), INPUTS)

        assert call.premium(22_100.0, DAY, 600) > call.premium(22_000.0, DAY, 600)
        assert put.premium(22_100.0, DAY, 600) < put.premium(22_000.0, DAY, 600)
        assert call.premium(22_000.0, DAY, 900) < call.premium(22_000.0, DAY, 600)

    def test_bars_keep_the_high_above_and_the_low_below_for_calls_and_puts(self) -> None:
        spot = spot_day([22_000.0, 22_030.0, 21_990.0, 22_010.0])
        for right in (Right.CALL, Right.PUT):
            bars = PremiumModel(contract(right), INPUTS).bars(spot, DAY)
            assert (bars.high >= np.maximum(bars.open, bars.close)).all()
            assert (bars.low <= np.minimum(bars.open, bars.close)).all()
            assert (bars.volume > 1e9).all()  # a model has no volume to run out of

    def test_at_expiry_the_premium_is_the_discounted_intrinsic_value(self) -> None:
        model = PremiumModel(contract(), INPUTS)
        forward = 22_100.0 * 22_020.0 / 22_000.0

        assert (
            abs(model.premium(22_100.0, WEEKLY.expiry, 15 * 60 + 30) - (forward - 22_000.0)) < 1e-6
        )
        assert model.premium(21_900.0, WEEKLY.expiry, 15 * 60 + 30) == 0.0


def book(
    closes: list[float],
    prior: Prior | None = None,
    listed: Sequence[ListedExpiry] = (WEEKLY,),
    limits: BookLimits | None = None,
) -> OptionBookEngine:
    store = MemoryBarStore({(NIFTY, DAY): spot_day(closes)})
    return OptionBookEngine(store, picker(*listed), prior or Prior(), FlatOptionCosts(), limits)


SIGNAL = Signal(NIFTY, "NIFTY", DAY, 9 * 60 + 20 + 5 * 10, 1)  # decided at the 11th bar


def drift(n: int = 40, per_bar: float = 0.0) -> list[float]:
    return [22_000.0 + per_bar * i for i in range(n)]


class TestOptionBookEngine:
    def test_an_up_signal_buys_the_call_and_a_rising_index_reaches_the_target(self) -> None:
        result = book(drift(per_bar=12.0)).run(ARM, [DAY], {DAY: [SIGNAL]})

        [t] = result.trades
        assert t.reason in (ExitReason.TRAIL, ExitReason.SQUARE_OFF) and t.trailed
        assert t.gross > 0 and t.expiry_kind == "weekly"
        assert t.quantity % 25 == 0 and t.quantity >= 25  # whole lots

    def test_size_is_the_smaller_of_the_premium_budget_and_the_risk_over_the_stop(self) -> None:
        [t] = book(drift()).run(ARM, [DAY], {DAY: [SIGNAL]}).trades

        lot_cost = t.entry * 25
        cap = int(80_000.0 // lot_cost)
        probe = FlatOptionCosts().cost(
            contract(), cap * 25, t.entry, t.entry, DAY, Scenario.BENCHMARK
        )
        stop = probe / (cap * 25 * t.entry) + ARM.target_p  # one-to-one: the stop is the target
        expected = int(min(80_000.0, 5_000.0 / stop) // lot_cost)

        assert t.quantity == expected * 25 and expected >= 1

    def test_a_falling_index_stops_the_call_out_at_a_loss(self) -> None:
        result = book(drift(per_bar=-15.0)).run(ARM, [DAY], {DAY: [SIGNAL]})

        [t] = result.trades
        assert t.reason is ExitReason.STOP and t.gross < 0
        assert t.gross > -5_000.0 * 1.6  # the stop holds the loss to about the Rs 5,000 limit

    def test_a_down_signal_buys_a_put_that_gains_when_the_index_falls(self) -> None:
        down = Signal(NIFTY, "NIFTY", DAY, SIGNAL.minute, -1)
        result = book(drift(per_bar=-12.0)).run(ARM, [DAY], {DAY: [down]})

        [t] = result.trades
        assert t.direction == -1 and t.gross > 0

    def test_no_weekly_uses_the_monthly_and_the_trade_says_so(self) -> None:
        result = book(drift(per_bar=12.0), listed=(MONTHLY,)).run(ARM, [DAY], {DAY: [SIGNAL]})

        assert [t.expiry_kind for t in result.trades] == ["monthly"]

    def test_skips_are_counted_by_reason(self) -> None:
        no_prior = book(drift(), prior=Prior(None)).run(ARM, [DAY], {DAY: [SIGNAL]})
        no_contract = book(drift(), listed=()).run(ARM, [DAY], {DAY: [SIGNAL]})
        late = Signal(NIFTY, "NIFTY", DAY, 14 * 60 + 50, 1)
        after = OptionBookEngine(
            MemoryBarStore({(NIFTY, DAY): spot_day(drift(75))}),
            picker(WEEKLY),
            Prior(),
            FlatOptionCosts(),
        ).run(ARM, [DAY], {DAY: [late]})

        assert no_prior.skipped["no_prior_settle"] == 1
        assert no_contract.skipped["no_expiry"] == 1
        assert after.skipped["late"] == 1 and not after.trades

    def test_all_eighteen_option_arms_run_and_the_loss_limits_apply(self) -> None:
        engine = book(drift(per_bar=-15.0), limits=BookLimits(daily_loss=1.0))
        signals = {DAY: [SIGNAL, Signal(NIFTY, "NIFTY", DAY, SIGNAL.minute + 100, 1)]}

        for arm in option_arms():
            result = engine.run(arm, [DAY], signals)
            assert result.signals == 2
            assert len(result.trades) <= 1  # after a loss past the daily limit, no new entry

    def test_the_program_costs_price_an_option_round_trip_with_slippage_as_a_cost(self) -> None:
        schedule = FoFeeSchedule(
            "flat", date(2020, 1, 1), True, Decimal(20), Decimal("0.1"), Decimal("0.125"),
            Decimal("0.03503"), Decimal(10), Decimal("0.003"), ZERO, Decimal(18),
        )  # fmt: skip
        costs = ProgramOptionCosts(FoFeeScheduleLibrary([schedule]))
        bench = costs.cost(contract(), 250, 100.0, 100.0, DAY, Scenario.BENCHMARK)
        adverse = costs.cost(contract(), 250, 100.0, 100.0, DAY, Scenario.ADVERSE)

        assert 0 < bench < adverse
        assert bench > 250 * (0.05 + 0.005 * 100.0) * 2 * 0.9  # both fills slip a tick and 0.5%


class TestModelError:
    def quotes(self, factor: float) -> list[RecordedQuote]:
        model = PremiumModel(contract(), INPUTS)
        return [
            RecordedQuote(contract(), DAY, minute, spot, model.premium(spot, DAY, minute) * factor)
            for minute, spot in ((600, 22_000.0), (660, 22_040.0), (720, 21_980.0))
        ]

    def test_a_model_that_matches_the_quotes_has_no_error_and_the_verdict_stands(self) -> None:
        error = ModelErrorMeter(Prior()).measure(self.quotes(1.0))

        assert error is not None and error.n == 3 and error.mean_abs < 1e-9
        assert not error.voids_verdict

    def test_an_error_larger_than_the_benchmark_slippage_voids_the_verdict(self) -> None:
        error = ModelErrorMeter(Prior()).measure(self.quotes(1.25))

        assert error is not None and error.mean_abs_fraction > 0.15
        assert error.voids_verdict and "VOID" in "\n".join(error.lines())

    def test_a_small_error_inside_one_tick_plus_half_a_percent_does_not_void(self) -> None:
        error = ModelErrorMeter(Prior()).measure(self.quotes(1.002))

        assert error is not None and not error.voids_verdict

    def test_quotes_with_no_prior_settle_are_counted_not_measured(self) -> None:
        meter = ModelErrorMeter(Prior(None))

        assert meter.measure(self.quotes(1.0)) is None
        assert ModelErrorMeter(Prior()).measure([]) is None

    def test_the_price_is_the_mid_when_both_sides_are_quoted_else_the_last_price(self) -> None:
        assert quote_price(99.0, 101.0, 90.0) == 100.0
        assert quote_price(None, 101.0, 90.0) == 90.0
        assert quote_price(101.0, 99.0, 90.0) == 90.0  # a crossed book is not a mid
        assert quote_price(None, None, None) is None

    def test_days_pool_by_their_number_of_quotes(self) -> None:
        meter = ModelErrorMeter(Prior())
        a, b = meter.measure(self.quotes(1.0)), meter.measure(self.quotes(1.25)[:1])

        assert a is not None and b is not None
        both = pooled([a, b])
        assert both is not None and both.n == 4 and pooled([]) is None

    def test_the_recorder_parts_are_read_and_priced(self, tmp_path: Path) -> None:
        model = PremiumModel(contract(), INPUTS)
        when = datetime(2024, 3, 4, 5, 0, tzinfo=UTC)  # 10:30 IST
        price = round(model.premium(22_010.0, DAY, 10 * 60 + 30), 2)
        folder = tmp_path / f"date={DAY.isoformat()}"
        folder.mkdir()
        table = pa.table(
            {
                "underlying": ["NIFTY", "NIFTY"],
                "expiry": [WEEKLY.expiry, date(2024, 3, 14)],
                "strike": [Decimal("22000"), Decimal("22000")],
                "right": ["CE", "CE"],
                "spot": [Decimal("22010"), Decimal("22010")],
                "exchange_ts": [when, when],
                "ltp": [Decimal(str(price)), Decimal("50")],
                "bid": [Decimal(str(price - 0.1)), None],
                "ask": [Decimal(str(price + 0.1)), None],
            }
        )
        pq.write_table(table, folder / "part-1.parquet")

        [quote] = list(load_recorded_quotes(tmp_path, DAY, Calendar([WEEKLY]), Lots()))
        error = ModelErrorMeter(Prior()).measure([quote])

        assert (quote.minute, quote.contract.lot) == (10 * 60 + 30, 25)
        assert error is not None and error.mean_abs < 0.02 and not error.voids_verdict


class TestIvPriorSession:
    def row(self, day: date, expiry: date, iv: float | None = 0.14) -> IvRow:
        return IvRow(
            day, "NIFTY", expiry, "weekly", 1, 3, 22_020.0, "future", 0.065, 22_000.0, iv, None,
            None, None, None, None,
        )  # fmt: skip

    def prior(self, rows: list[IvRow], close: tuple[date, float] | None) -> IvPriorSession:
        class Rows:
            def latest_before(self, symbol: str, day: date) -> list[IvRow]:
                return rows

        class Spots:
            def close_before(self, symbol: str, day: date) -> tuple[date, float] | None:
                return close

        return IvPriorSession(Rows(), Spots())

    def test_the_prior_sessions_row_for_the_contracts_expiry_gives_the_inputs(self) -> None:
        friday = date(2024, 3, 1)
        prior = self.prior([self.row(friday, date(2024, 3, 14)), self.row(friday, WEEKLY.expiry)],
                           (friday, 22_000.0))  # fmt: skip

        assert prior.inputs(contract(), DAY) == ModelInputs(22_000.0, 22_020.0, 0.14, 0.065)

    def test_no_row_for_the_expiry_or_no_volatility_or_a_different_day_gives_none(self) -> None:
        friday = date(2024, 3, 1)

        assert (
            self.prior([self.row(friday, date(2024, 3, 14))], (friday, 22_000.0)).inputs(
                contract(), DAY
            )
            is None
        )
        assert (
            self.prior([self.row(friday, WEEKLY.expiry, None)], (friday, 22_000.0)).inputs(
                contract(), DAY
            )
            is None
        )
        assert (
            self.prior([self.row(date(2024, 2, 29), WEEKLY.expiry)], (friday, 22_000.0)).inputs(
                contract(), DAY
            )
            is None
        )  # the row is not of the spot's session
        assert self.prior([self.row(friday, WEEKLY.expiry)], None).inputs(contract(), DAY) is None
