"""EM-185: latency is read off existing order records; slippage is signed adverse-positive."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.parity.latency import LatencyProfiler
from emporos.parity.models import ReferenceKind
from emporos.parity.slippage import SlippageMeasure
from emporos.signals.quotes import DecisionQuote
from tests.support.parity import event, execution, order_record
from tests.support.strategies import T0

S = timedelta(seconds=1)


def test_latency_splits_decision_placement_and_fill() -> None:
    order = order_record(created_at=T0 + 2 * S)
    events = {
        order.id: [
            event(order.id, 1, T0 + 2 * S, "PENDING_NEW"),
            event(order.id, 2, T0 + 3 * S, "OPEN"),
        ]
    }
    profile = LatencyProfiler().profile(T0, [order], events, [execution("t1", ts=T0 + 10 * S)])
    assert profile is not None
    assert (profile.decision, profile.placement, profile.fill, profile.reprices) == (
        2 * S, S, 7 * S, 0,
    )  # fmt: skip


def test_reprices_are_the_extra_orders_and_unacked_orders_have_no_placement() -> None:
    root, child = order_record("o1"), order_record("o2", parent="o1", created_at=T0 + 30 * S)
    profile = LatencyProfiler().profile(T0, [child, root], {}, [])
    assert profile is not None
    assert (profile.placement, profile.fill, profile.reprices) == (None, None, 1)


def test_no_orders_means_no_latency() -> None:
    assert LatencyProfiler().profile(T0, [], {}, []) is None


def test_slippage_uses_mid_then_ltp_then_the_signal_price() -> None:
    measure = SlippageMeasure()
    both = DecisionQuote(T0, "q", Money.of("100"), Money.of("99"), Money.of("101"))
    only_ltp = DecisionQuote(T0, "q", Money.of("100"))
    assert measure.reference(both, Money.of("50")) == (Money.of("100"), ReferenceKind.MID)
    assert measure.reference(only_ltp, Money.of("50")) == (Money.of("100"), ReferenceKind.LTP)
    assert measure.reference(None, Money.of("50")) == (Money.of("50"), ReferenceKind.SIGNAL_PRICE)


@pytest.mark.parametrize(
    ("side", "fill", "bps"),
    [
        (OrderSide.BUY, "100.10", "10"),  # paid above the reference: adverse
        (OrderSide.BUY, "99.90", "-10"),
        (OrderSide.SELL, "99.90", "10"),  # sold below: adverse
        (OrderSide.SELL, "100.10", "-10"),
    ],
)
def test_slippage_is_positive_when_the_fill_is_worse_for_us(
    side: OrderSide, fill: str, bps: str
) -> None:
    slip = SlippageMeasure().measure(side, Money.of(fill), None, Money.of("100"))
    assert slip.bps == Decimal(bps)


def test_a_non_positive_reference_is_refused() -> None:
    with pytest.raises(ValueError):
        SlippageMeasure().measure(
            OrderSide.BUY, Money.of("1"), DecisionQuote(T0, "q", Money.of("0")), Money.of("1")
        )
