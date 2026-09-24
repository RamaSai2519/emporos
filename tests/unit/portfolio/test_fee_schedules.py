"""Fee schedules load from dated YAML, exactly, and the right one is in force on each day."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.broker.models import BrokerTrade
from emporos.broker.paper.costs import ScheduledCosts
from emporos.domain.fees import IntradayCharges, TradeProduct
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.portfolio.fee_schedules import (
    FeeScheduleError,
    FeeScheduleLibrary,
    FeeScheduleParser,
)

VALID = """
name: {name}
effective_from: "{day}"
verified: false
brokerage: {{flat: "20", percent: "0.1", minimum: "5"}}
stt_sell_percent: "{stt}"
exchange_transaction_percent: {{NSE: "0.0030699"}}
sebi_per_crore: "10"
stamp_duty_buy_percent: "0.003"
gst_percent: "18"
"""


def write(directory: Path, filename: str, day: str, stt: str = "0.025") -> Path:
    path = directory / filename
    path.write_text(VALID.format(name=filename, day=day, stt=stt), encoding="utf-8")
    return path


def test_the_shipped_schedule_loads_exactly_and_is_flagged_verified() -> None:
    schedule = FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21))

    assert schedule.name == "angelone-equity-intraday"
    assert schedule.stt_sell_percent == Decimal("0.025")
    assert schedule.exchange_transaction_percent[Exchange.NSE] == Decimal("0.0030699")
    assert schedule.brokerage_flat == Money.of("20")
    assert schedule.verified is True  # operator reconciled it against a real contract note (EM-197)


def test_the_schedule_in_force_on_a_day_is_the_latest_that_had_started(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", "2026-01-01", stt="0.025")
    write(tmp_path, "b.yaml", "2026-07-01", stt="0.03")
    library = FeeScheduleLibrary.from_directory(tmp_path)

    assert library.for_date(date(2026, 6, 30)).stt_sell_percent == Decimal("0.025")
    assert library.for_date(date(2026, 7, 1)).stt_sell_percent == Decimal("0.03")
    with pytest.raises(FeeScheduleError, match="in force"):
        library.for_date(date(2025, 12, 31))


def test_a_directory_with_no_schedules_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FeeScheduleError, match="no fee schedules"):
        FeeScheduleLibrary.from_directory(tmp_path)


def test_a_rate_written_as_a_yaml_float_is_refused_because_it_is_already_inexact(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "f.yaml", "2026-01-01", stt="0.025")
    path.write_text(path.read_text().replace('"0.025"', "0.025"), encoding="utf-8")

    with pytest.raises(FeeScheduleError, match="quoted string"):
        FeeScheduleParser().parse(path)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda s: s.replace('"18"', '"eighteen"'), "not a number"),
        (lambda s: s.replace("gst_percent", "vat_percent"), "gst_percent"),
        (lambda s: s.replace("NSE:", "MARS:"), "MARS"),
    ],
)
def test_malformed_files_name_the_file_and_the_problem(tmp_path: Path, edit, message: str) -> None:  # type: ignore[no-untyped-def]
    path = write(tmp_path, "bad.yaml", "2026-01-01")
    path.write_text(edit(path.read_text()), encoding="utf-8")

    with pytest.raises(FeeScheduleError, match=message):
        FeeScheduleParser().parse(path)


def test_scheduled_costs_charge_a_paper_trade_from_its_instrument_and_side() -> None:
    schedule = FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 21))
    costs = ScheduledCosts(IntradayCharges(schedule))
    at = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)

    buy = BrokerTrade("T1", "O1", "NSE:3045", OrderSide.BUY, 100, Money.of("500"), at)
    sell = BrokerTrade("T2", "O2", "NSE:3045", OrderSide.SELL, 100, Money.of("510"), at)

    assert costs.charges(buy) == Money.of("26.96")
    assert costs.charges(sell) == Money.of("38.26")


DELIVERY_FILE = VALID.replace("stt_sell_percent", "product: delivery\nstt_buy_percent: \"0.1\"\n"
                              "dp_charge_per_sale: \"20\"\nstt_sell_percent")  # fmt: skip


def test_the_shipped_delivery_schedule_loads_unverified_and_stays_out_of_the_intraday_library() -> (
    None
):
    delivery = FeeScheduleLibrary.from_directory(product=TradeProduct.DELIVERY).for_date(
        date(2026, 9, 25)
    )
    intraday = FeeScheduleLibrary.from_directory().for_date(date(2026, 9, 25))

    assert delivery.name == "angelone-equity-delivery"
    assert delivery.verified is False
    assert (delivery.stt_buy_percent, delivery.stt_sell_percent) == (Decimal("0.1"), Decimal("0.1"))
    assert delivery.stamp_duty_buy_percent == Decimal("0.015")
    assert delivery.dp_charge_per_sale == Money.of("20")
    assert intraday.name == "angelone-equity-intraday"  # the newer delivery file did not take over


def test_a_directory_with_only_the_other_products_files_has_no_schedule(tmp_path: Path) -> None:
    (tmp_path / "d.yaml").write_text(
        DELIVERY_FILE.format(name="d", day="2026-01-01", stt="0.1"), encoding="utf-8"
    )

    with pytest.raises(FeeScheduleError, match="no fee schedules"):
        FeeScheduleLibrary.from_directory(tmp_path)
    assert FeeScheduleLibrary.from_directory(tmp_path, TradeProduct.DELIVERY).earliest.name == "d"


def test_an_unknown_product_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text(
        VALID.format(name="x", day="2026-01-01", stt="0.025") + "product: futures\n",
        encoding="utf-8",
    )

    with pytest.raises(FeeScheduleError, match="x.yaml"):
        FeeScheduleParser().parse(path)
