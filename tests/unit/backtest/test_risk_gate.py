"""The platform's own risk rules bind a backtest exactly as they bind a live order."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.broker import SimulatedBroker
from emporos.backtest.clock import BarClock
from emporos.backtest.orders import Fill, SimOrderRequest
from emporos.backtest.portfolio import BacktestPortfolio
from emporos.backtest.pricing import GateContext, GateRejection
from emporos.backtest.risk_gate import RiskGateFactory
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from emporos.domain.signals import Signal, SignalKind
from tests.support.risk import generous_limits

# 09:30 IST on a trading Monday, inside the session.
NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
ID = "NSE:1001"


def context(at: datetime = NOW) -> GateContext:
    clock = BarClock(at)
    portfolio = BacktestPortfolio(Money.of("100000"))
    portfolio.mark(ID, Money.of("100"))
    return GateContext(clock.view(), portfolio, SimulatedBroker(clock))


def signal(quantity: int = 100, price: str = "100", kind: SignalKind = SignalKind.ENTRY,
           side: OrderSide = OrderSide.BUY, instrument: str = ID) -> Signal:  # fmt: skip
    return Signal(
        "run", instrument, kind, side, OrderType.LIMIT, quantity, Money.of(price), NOW, "t"
    )


def gate(ctx: GateContext, **limits: object):  # type: ignore[no-untyped-def]
    return RiskGateFactory(generous_limits(**limits))(ctx)


def test_a_sane_signal_passes_through_unchanged() -> None:
    sig = signal()
    assert gate(context()).review(sig) is sig
    assert gate(context()).name == "risk_rules"


@pytest.mark.parametrize(
    ("limits", "sig", "rule"),
    [
        ({}, signal(quantity=501), "MaxPositionValueGuard"),  # 501 * 100 > the 25,000 cap first
        ({"max_position_value": Decimal(500_000)}, signal(quantity=501), "MaxOrderQuantityGuard"),
        ({}, signal(quantity=251), "MaxPositionValueGuard"),
        ({}, signal(price="103"), "PriceSanityGuard"),  # 3% from the last price; the cap is 2%
    ],
)
def test_size_and_price_limits_refuse(limits: dict, sig: Signal, rule: str) -> None:  # type: ignore[type-arg]
    verdict = gate(context(), **limits).review(sig)
    assert isinstance(verdict, GateRejection) and verdict.reason.startswith(rule)


def test_an_order_outside_the_session_is_refused() -> None:
    after_close = datetime(2026, 1, 5, 11, 0, tzinfo=UTC)  # 16:30 IST
    verdict = gate(context(after_close)).review(signal())
    assert isinstance(verdict, GateRejection) and verdict.reason.startswith("MarketSessionGuard")


def test_the_positions_the_strategy_holds_count_against_the_limits() -> None:
    ctx = context()
    for n in range(3):
        instrument = f"NSE:200{n}"
        ctx.portfolio.mark(instrument, Money.of("100"))
        ctx.portfolio.apply(_fill(instrument), Money.zero())
    verdict = gate(ctx).review(signal())  # a fourth position with three open: the cap is 3
    assert isinstance(verdict, GateRejection) and verdict.reason.startswith("MaxOpenPositionsGuard")


def test_a_realised_loss_beyond_the_cap_stops_entries_but_never_exits() -> None:
    ctx = context()
    ctx.portfolio.apply(
        _fill(ID), Money.of("2500")
    )  # 2,500 of charges on the books: past the 2,000 cap
    ctx.portfolio.mark("NSE:2001", Money.of("100"))
    entry = gate(ctx).review(signal(instrument="NSE:2001"))
    assert isinstance(entry, GateRejection) and "Loss" in entry.reason
    exit_ = signal(kind=SignalKind.EXIT, side=OrderSide.SELL, quantity=10)
    assert gate(ctx).review(exit_) is exit_


def test_a_working_order_on_the_same_side_blocks_a_duplicate() -> None:
    ctx = context()
    ctx.broker.submit(
        SimOrderRequest(ID, OrderSide.BUY, OrderType.LIMIT, 10, Money.of("100"), "t1", "run-1")
    )
    verdict = gate(ctx).review(signal())
    assert isinstance(verdict, GateRejection) and verdict.reason.startswith("DuplicateOrderGuard")


def test_the_gates_own_order_rate_is_remembered_and_forgotten() -> None:
    clock = BarClock(NOW)
    portfolio = BacktestPortfolio(Money.of("100000"))
    portfolio.mark(ID, Money.of("100"))
    ctx = GateContext(clock.view(), portfolio, SimulatedBroker(clock))
    g = gate(ctx, max_orders_per_second=100, max_orders_per_minute=2, duplicate_window_seconds=1)
    assert g.review(signal(instrument=ID)) is not None
    for other in ("NSE:3001", "NSE:3002"):
        portfolio.mark(other, Money.of("100"))
    assert isinstance(g.review(signal(instrument="NSE:3001")), Signal)
    third = g.review(signal(instrument="NSE:3002"))
    assert isinstance(third, GateRejection) and third.reason.startswith("OrderRateGuard")
    clock.set(NOW + timedelta(minutes=2))
    assert isinstance(g.review(signal(instrument="NSE:3002")), Signal)


def _fill(instrument: str) -> Fill:
    return Fill(1, "o1", "t", instrument, OrderSide.BUY, 100, Money.of("100"), NOW, "run-1")


def test_a_signal_for_an_instrument_with_no_price_is_refused_not_a_crash() -> None:
    verdict = gate(context()).review(signal(instrument="NSE:9999"))
    assert isinstance(verdict, GateRejection) and "no price" in verdict.reason
