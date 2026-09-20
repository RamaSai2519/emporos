"""EM-103: the backtest's costs — the dated schedule in force on each fill's day, and a contract
note worked by hand against the SHIPPED schedule file."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from emporos.backtest.costs import BacktestCosts, EarliestBeforeFirst, StrictSchedules
from emporos.backtest.orders import Fill
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.portfolio.fee_schedules import FeeScheduleError, FeeScheduleLibrary
from tests.support.strategies import INSTRUMENT

SHIPPED = FeeScheduleLibrary.from_directory()

TEMPLATE = """
name: {name}
effective_from: "{day}"
verified: {verified}
brokerage: {{flat: "20", percent: "0.1", minimum: "5"}}
stt_sell_percent: "{stt}"
exchange_transaction_percent: {{NSE: "0.0030699"}}
sebi_per_crore: "10"
stamp_duty_buy_percent: "0.003"
gst_percent: "18"
"""


def fill(side: OrderSide, quantity: int, price: str, when: datetime, sequence: int = 1) -> Fill:
    return Fill(sequence, "SIM-1", "t", INSTRUMENT, side, quantity, Money.of(price), when)


MON_1015_IST = datetime(2026, 9, 21, 4, 45, tzinfo=UTC)  # Monday 21 Sep 2026, 10:15 IST


class TestContractNoteByHand:
    """One intraday round trip in one NSE stock, priced with config/fees/angelone-2026-09-20.yaml:
    brokerage flat 20 / 0.1% / min 5, STT 0.025% on the sell, NSE transaction 0.0030699%, SEBI
    Rs 10 per crore, stamp duty 0.003% on the buy, GST 18% on brokerage + transaction + SEBI.
    Every figure is rounded half-up to the paisa, as charged."""

    def test_the_buy_leg(self) -> None:
        # BUY 100 @ 500.00 -> turnover 50,000.00
        #   brokerage  min(20, 0.1% * 50,000 = 50.00)            = 20.00
        #   STT        none on a buy                              =  0.00
        #   exchange   50,000 * 0.0030699% = 1.53495              =  1.53
        #   SEBI       50,000 * 10 / 10,000,000 = 0.05            =  0.05
        #   stamp      50,000 * 0.003% = 1.50                     =  1.50
        #   GST        18% * (20.00 + 1.53 + 0.05 = 21.58) = 3.8844 = 3.88
        #   total      20.00 + 0.00 + 1.53 + 0.05 + 1.50 + 3.88   = 26.96
        breakdown = BacktestCosts(StrictSchedules(SHIPPED)).charges(
            fill(OrderSide.BUY, 100, "500", MON_1015_IST)
        )

        assert (breakdown.brokerage, breakdown.stt, breakdown.exchange_transaction) == (
            Money.of("20.00"), Money.of("0.00"), Money.of("1.53"),
        )  # fmt: skip
        assert (breakdown.sebi, breakdown.stamp_duty, breakdown.gst) == (
            Money.of("0.05"), Money.of("1.50"), Money.of("3.88"),
        )  # fmt: skip
        assert breakdown.total == Money.of("26.96")

    def test_the_sell_leg(self) -> None:
        # SELL 100 @ 505.00 -> turnover 50,500.00
        #   brokerage  min(20, 0.1% * 50,500 = 50.50)             = 20.00
        #   STT        50,500 * 0.025% = 12.625 -> half up        = 12.63
        #   exchange   50,500 * 0.0030699% = 1.5502995            =  1.55
        #   SEBI       50,500 * 10 / 10,000,000 = 0.0505          =  0.05
        #   stamp      none on a sell                             =  0.00
        #   GST        18% * (20.00 + 1.55 + 0.05 = 21.60) = 3.888 = 3.89
        #   total      20.00 + 12.63 + 1.55 + 0.05 + 0.00 + 3.89  = 38.12
        breakdown = BacktestCosts(StrictSchedules(SHIPPED)).charges(
            fill(OrderSide.SELL, 100, "505", MON_1015_IST)
        )

        assert (breakdown.brokerage, breakdown.stt, breakdown.exchange_transaction) == (
            Money.of("20.00"), Money.of("12.63"), Money.of("1.55"),
        )  # fmt: skip
        assert (breakdown.sebi, breakdown.stamp_duty, breakdown.gst) == (
            Money.of("0.05"), Money.of("0.00"), Money.of("3.89"),
        )  # fmt: skip
        assert breakdown.total == Money.of("38.12")

    def test_the_round_trip(self) -> None:
        # gross (505 - 500) * 100 = 500.00; charges 26.96 + 38.12 = 65.08; net 434.92
        costs = BacktestCosts(StrictSchedules(SHIPPED))
        total = (
            costs.charges(fill(OrderSide.BUY, 100, "500", MON_1015_IST)).total
            + costs.charges(fill(OrderSide.SELL, 100, "505", MON_1015_IST, 2)).total
        )

        assert total == Money.of("65.08")
        assert Money.of("500.00") - total == Money.of("434.92")

    def test_a_small_trade_pays_the_minimum_brokerage_not_a_fraction_of_a_rupee(self) -> None:
        # BUY 2 @ 100 = 200 turnover: 0.1% = 0.20 < the 5.00 minimum -> 5.00 brokerage
        #   exchange 200 * 0.0030699% = 0.00614 -> 0.01; SEBI 200*10/1e7 = 0.0002 -> 0.00
        #   stamp 200 * 0.003% = 0.006 -> 0.01; GST 18% * (5.00 + 0.01 + 0.00) = 0.9018 -> 0.90
        breakdown = BacktestCosts(StrictSchedules(SHIPPED)).charges(
            fill(OrderSide.BUY, 2, "100", MON_1015_IST)
        )

        assert breakdown.brokerage == Money.of("5.00")
        assert (breakdown.exchange_transaction, breakdown.sebi) == (
            Money.of("0.01"),
            Money.of("0.00"),
        )
        assert (breakdown.stamp_duty, breakdown.gst) == (Money.of("0.01"), Money.of("0.90"))
        assert breakdown.total == Money.of("5.92")


class TestTheScheduleInForceOnTheDay:
    def library(self, tmp_path: Path) -> FeeScheduleLibrary:
        (tmp_path / "a.yaml").write_text(
            TEMPLATE.format(name="early", day="2026-09-01", stt="0.025", verified="true"),
            encoding="utf-8",
        )
        (tmp_path / "b.yaml").write_text(
            TEMPLATE.format(name="late", day="2026-09-22", stt="0.05", verified="false"),
            encoding="utf-8",
        )
        return FeeScheduleLibrary.from_directory(tmp_path)

    def test_each_fill_is_priced_with_the_schedule_of_its_own_day(self, tmp_path: Path) -> None:
        costs = BacktestCosts(StrictSchedules(self.library(tmp_path)))

        def stt_on(when: datetime) -> Money:
            return costs.charges(fill(OrderSide.SELL, 100, "500", when)).stt

        # STT on 50,000: 0.025% = 12.50 under "early", 0.05% = 25.00 under "late"
        assert stt_on(datetime(2026, 9, 21, 5, 0, tzinfo=UTC)) == Money.of("12.50")
        assert stt_on(datetime(2026, 9, 22, 5, 0, tzinfo=UTC)) == Money.of("25.00")

    def test_the_day_is_the_indian_trading_day_not_the_utc_date(self, tmp_path: Path) -> None:
        costs = BacktestCosts(StrictSchedules(self.library(tmp_path)))
        # 21 Sep 20:00 UTC is 22 Sep 01:30 IST: already the "late" schedule's first day
        late_night = datetime(2026, 9, 21, 20, 0, tzinfo=UTC)

        assert costs.charges(fill(OrderSide.SELL, 100, "500", late_night)).stt == Money.of("25.00")

    def test_the_summary_names_every_schedule_used_and_whether_all_were_verified(
        self, tmp_path: Path
    ) -> None:
        costs = BacktestCosts(StrictSchedules(self.library(tmp_path)))
        costs.charges(fill(OrderSide.BUY, 1, "500", datetime(2026, 9, 21, 5, 0, tzinfo=UTC)))
        assert costs.summary().all_verified is True

        costs.charges(fill(OrderSide.BUY, 1, "500", datetime(2026, 9, 23, 5, 0, tzinfo=UTC)))
        summary = costs.summary()

        assert summary.schedules == (
            ("early", date(2026, 9, 1), True),
            ("late", date(2026, 9, 22), False),
        )
        assert summary.all_verified is False and summary.assumed_days == 0

    def test_with_no_fills_nothing_is_claimed_verified(self) -> None:
        summary = BacktestCosts(StrictSchedules(SHIPPED)).summary()
        assert summary.schedules == () and summary.all_verified is False


class TestDaysBeforeTheFirstSchedule:
    BEFORE = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)  # the shipped schedule starts 2026-09-20

    def test_the_strict_source_refuses_loudly(self) -> None:
        costs = BacktestCosts(StrictSchedules(SHIPPED))
        with pytest.raises(FeeScheduleError, match="2026-09-18"):
            costs.charges(fill(OrderSide.BUY, 1, "500", self.BEFORE))

    def test_the_opt_in_source_uses_the_oldest_schedule_and_counts_the_days(self) -> None:
        source = EarliestBeforeFirst(SHIPPED)
        costs = BacktestCosts(source)

        costs.charges(fill(OrderSide.BUY, 100, "500", self.BEFORE))
        costs.charges(fill(OrderSide.SELL, 100, "500", self.BEFORE))  # the same day again
        costs.charges(fill(OrderSide.BUY, 100, "500", datetime(2026, 9, 17, 5, 0, tzinfo=UTC)))

        assert source.assumed_days == {date(2026, 9, 18), date(2026, 9, 17)}
        assert costs.summary().assumed_days == 2
        assert costs.summary().schedules[0][1] == date(2026, 9, 20)

    def test_the_opt_in_source_prices_a_covered_day_normally_and_assumes_nothing(self) -> None:
        source = EarliestBeforeFirst(SHIPPED)
        BacktestCosts(source).charges(fill(OrderSide.BUY, 100, "500", MON_1015_IST))
        assert source.assumed_days == frozenset()

    def test_the_earliest_schedule_of_a_library_is_the_oldest_by_date(self, tmp_path: Path) -> None:
        for name, day in (("z", "2026-09-22"), ("a", "2026-09-01")):
            (tmp_path / f"{name}.yaml").write_text(
                TEMPLATE.format(name=name, day=day, stt="0.025", verified="false"), encoding="utf-8"
            )
        assert FeeScheduleLibrary.from_directory(tmp_path).earliest.name == "a"
