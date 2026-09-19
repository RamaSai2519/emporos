"""Reject rules: each one alone, then the screen that orders them."""

from __future__ import annotations

from decimal import Decimal

import pytest

from emporos.broker.models import PlaceOrderRequest, ProductType
from emporos.broker.paper.chance import NeverChance, SeededChance
from emporos.broker.paper.faults import RandomReplyLoss, ReliableReplies, ScriptedReplyLoss
from emporos.broker.paper.rejects import (
    OrderContext,
    OrderScreen,
    ProbabilisticRejectRule,
    RejectionStage,
    StopPlacementRule,
    SufficientFundsRule,
    SupportedProductRule,
    TickSizeRule,
    default_rules,
)
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide, OrderType
from tests.support.paper_rig import ID, SBIN, order


def context(
    request: PlaceOrderRequest,
    last: str | None = None,
    cash: str = "1000000",
    margin: str = "0",
) -> OrderContext:
    return OrderContext(
        request, SBIN, None if last is None else Money.of(last), Money.of(cash), Money.of(margin)
    )


def stop(side: OrderSide, price: str, trigger: str) -> PlaceOrderRequest:
    return PlaceOrderRequest(
        ID,
        side,
        OrderType.STOPLOSS_LIMIT,
        1,
        Money.of(price),
        "SL0001",
        trigger_price=Money.of(trigger),
    )


def test_only_intraday_is_simulated() -> None:
    rule = SupportedProductRule()

    assert rule.reason(context(order())) is None
    assert "DELIVERY" in (rule.reason(context(order(product=ProductType.DELIVERY))) or "")


def test_prices_must_sit_on_the_tick_grid_including_the_trigger() -> None:
    rule = TickSizeRule()  # SBIN's tick size is 0.05

    assert rule.reason(context(order(price="100.05"))) is None
    assert "price" in (rule.reason(context(order(price="100.03"))) or "")
    assert "trigger" in (rule.reason(context(stop(OrderSide.SELL, "99.00", "99.53"))) or "")


@pytest.mark.parametrize(
    ("side", "price", "trigger", "last", "objection"),
    [
        (OrderSide.SELL, "99.00", "100.00", "101.00", None),  # sell: trigger under the market
        (OrderSide.SELL, "100.50", "100.00", "101.00", "wrong side"),  # limit above its trigger
        (OrderSide.SELL, "99.00", "100.00", "99.50", "already been reached"),
        (OrderSide.SELL, "99.00", "100.00", None, None),  # no market yet: cannot judge that part
        (OrderSide.BUY, "101.00", "100.50", "100.00", None),  # buy: trigger above the market
        (OrderSide.BUY, "100.00", "100.50", "100.00", "wrong side"),
        (OrderSide.BUY, "101.00", "100.50", "100.60", "already been reached"),
    ],
)
def test_a_stop_order_is_placed_on_the_right_side_of_its_trigger_and_the_market(
    side: OrderSide, price: str, trigger: str, last: str | None, objection: str | None
) -> None:
    reason = StopPlacementRule().reason(context(stop(side, price, trigger), last=last))

    assert (reason is None) if objection is None else (objection in (reason or ""))


def test_a_plain_limit_order_has_no_stop_placement_to_get_wrong() -> None:
    assert StopPlacementRule().reason(context(order())) is None


def test_funds_are_refused_when_the_margin_exceeds_the_cash_that_is_free() -> None:
    rule = SufficientFundsRule()

    assert rule.reason(context(order(), cash="1000", margin="1000")) is None
    assert "insufficient" in (rule.reason(context(order(), cash="999.99", margin="1000")) or "")


def test_rejection_stages_say_where_each_rule_acts() -> None:
    assert [r.stage for r in default_rules()] == [
        RejectionStage.AT_ACCEPTANCE,
        RejectionStage.AT_ACCEPTANCE,
        RejectionStage.AT_ACCEPTANCE,
        RejectionStage.AT_EXCHANGE,
    ]


def test_the_screen_returns_the_first_objection_in_rule_order() -> None:
    screen = OrderScreen(default_rules())
    bad = context(order(price="100.03"), cash="0", margin="1000")  # off-grid AND unaffordable

    rejection = screen.first_rejection(bad)

    assert rejection is not None and rejection.stage is RejectionStage.AT_ACCEPTANCE
    assert "tick size" in rejection.reason
    assert screen.first_rejection(context(order())) is None


def test_amendments_face_only_the_request_validation_rules() -> None:
    screen = OrderScreen([*default_rules(), ProbabilisticRejectRule(Decimal(1), NeverChance())])
    unaffordable = context(order(), cash="0", margin="1000")

    assert screen.first_rejection(unaffordable) is not None
    assert screen.first_acceptance_rejection(unaffordable) is None  # margin is not re-judged
    assert screen.first_acceptance_rejection(context(order(price="100.03"))) is not None


def test_random_rejects_follow_the_chance_source_and_are_reproducible_from_a_seed() -> None:
    def outcomes(seed: int) -> list[bool]:
        rule = ProbabilisticRejectRule(Decimal("0.5"), SeededChance(seed))
        return [rule.reason(context(order())) is not None for _ in range(40)]

    assert outcomes(7) == outcomes(7)
    assert 5 < sum(outcomes(7)) < 35  # roughly half, not all or none
    always = ProbabilisticRejectRule(Decimal(1), SeededChance(1), "exchange said no")
    assert always.reason(context(order())) == "exchange said no"
    never = ProbabilisticRejectRule(Decimal(0), SeededChance(1))
    assert never.reason(context(order())) is None


@pytest.mark.parametrize("rate", ["-0.1", "1.1"])
def test_rates_are_probabilities(rate: str) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        ProbabilisticRejectRule(Decimal(rate), NeverChance())
    with pytest.raises(ValueError, match="between 0 and 1"):
        RandomReplyLoss(Decimal(rate), NeverChance())
    with pytest.raises(ValueError, match="probability"):
        SeededChance(1).hits(Decimal(rate))


def test_reply_loss_policies() -> None:
    request = order()
    scripted = ScriptedReplyLoss()

    assert ReliableReplies().loses_reply(request) is False
    assert scripted.loses_reply(request) is False
    scripted.lose_next()
    assert [scripted.loses_reply(request), scripted.loses_reply(request)] == [True, False]
    assert RandomReplyLoss(Decimal(1), SeededChance(3)).loses_reply(request) is True
    assert RandomReplyLoss(Decimal(0), SeededChance(3)).loses_reply(request) is False
