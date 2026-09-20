"""Exact P&L examples including a long-to-short reversal and unknown market marks."""

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.portfolio.ledger import PortfolioValuator, PositionCalculator
from tests.support.records import RecordFactory


class TestPositionCalculator:
    def test_legacy_position_preserves_booked_profit_without_gross_field(self):
        factory = RecordFactory()
        before = factory.position(
            net_quantity=0,
            average_price=Money.zero(),
            realised_pnl=Money.of("20"),
            fees=Money.of("2"),
        )
        fill = factory.execution(
            instrument_id=before.instrument_id,
            account_id=before.account_id,
            fees=Money.of("1"),
        )
        after = PositionCalculator().apply(before, fill)
        assert after.gross_realised_pnl == Money.of("22")
        assert after.realised_pnl == Money.of("19")

    @pytest.mark.parametrize(
        "held,side,quantity,price,net,average,gross",
        [
            (0, OrderSide.BUY, 10, "100", 10, "100", "0"),
            (10, OrderSide.BUY, 10, "120", 20, "110", "0"),
            (10, OrderSide.SELL, 4, "120", 6, "100", "80"),
            (10, OrderSide.SELL, 10, "120", 0, "0", "200"),
            (10, OrderSide.SELL, 15, "120", -5, "120", "200"),
            (-10, OrderSide.BUY, 15, "80", 5, "80", "200"),
            (-10, OrderSide.SELL, 10, "120", -20, "110", "0"),
        ],
    )
    def test_average_cost_and_reversal(self, held, side, quantity, price, net, average, gross):
        factory = RecordFactory()
        before = factory.position(net_quantity=held, average_price=Money.of("100"))
        fill = factory.execution(
            instrument_id=before.instrument_id,
            account_id=before.account_id,
            side=side,
            quantity=quantity,
            price=Money.of(price),
            fees=Money.of("2"),
        )
        after = PositionCalculator().apply(before, fill)
        assert after.net_quantity == net
        assert after.average_price == Money.of(average)
        assert after.gross_realised_pnl == Money.of(gross)
        assert after.realised_pnl == Money.of(gross) - Money.of("2")
        assert before.net_quantity == held

    def test_refuses_unrelated_or_invalid_fills(self):
        factory = RecordFactory()
        before = factory.position()
        with pytest.raises(ValueError, match="belong"):
            PositionCalculator().apply(before, factory.execution())
        for changes in ({"quantity": 0}, {"price": Money.zero()}, {"fees": Money.of("-1")}):
            fill = factory.execution(
                instrument_id=before.instrument_id, account_id=before.account_id, **changes
            )
            with pytest.raises(ValueError):
                PositionCalculator().apply(before, fill)


class TestPortfolioValuation:
    def test_missing_mark_is_unknown_then_long_and_short_are_valued(self):
        factory = RecordFactory()
        long = factory.position(
            instrument_id="long",
            net_quantity=10,
            average_price=Money.of("100"),
            realised_pnl=Money.of("20"),
            fees=Money.of("2"),
        )
        short = factory.position(
            instrument_id="short", net_quantity=-5, average_price=Money.of("100")
        )
        flat = factory.position(instrument_id="flat", net_quantity=0)
        valuator = PortfolioValuator()
        unknown = valuator.value([long, short, flat], {"long": Money.of("110")})
        assert unknown.unrealised is None and unknown.missing_marks == ("short",)
        known = valuator.value(
            [long, short, flat], {"long": Money.of("110"), "short": Money.of("80")}
        )
        assert known.unrealised == Money.of("200")
        assert known.realised == Money.of("20") and known.fees == Money.of("2")
