"""Each exposure/sanity guard, with the boundary tested exactly (EM-72): the amount that is
precisely the cap passes and the smallest step past it is blocked."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import Signal, SignalKind
from emporos.risk.rules.exposure import (
    AbnormalSpreadGuard,
    DuplicateOrderGuard,
    MaxCapitalDeployedGuard,
    MaxDailyLossGuard,
    MaxOpenPositionsGuard,
    MaxOrderQuantityGuard,
    MaxPositionValueGuard,
    MaxStrategyLossGuard,
    OrderRateGuard,
    PriceSanityGuard,
)
from emporos.risk.snapshot import AccountFacts, OrderFlowFacts, RiskSnapshot
from tests.support.risk import NOW, calm_market, healthy, long_position, working
from tests.support.strategies import INSTRUMENT, RUN_ID, make_signal

OTHER = "NSE:2002"
EXIT = SignalKind.EXIT


def account(**facts: Any) -> AccountFacts:
    return AccountFacts(**facts)


class TestDuplicateOrderGuard:
    guard = DuplicateOrderGuard(timedelta(seconds=5))

    def test_nothing_working_allows(self) -> None:
        assert self.guard.evaluate(make_signal(), healthy()).allowed

    def test_a_working_order_on_the_same_instrument_and_side_inside_the_window_blocks(self) -> None:
        flow = OrderFlowFacts(working=(working(OrderSide.BUY, seconds_ago=4.999),))
        assert not self.guard.evaluate(make_signal(), healthy(flow=flow)).allowed

    def test_one_placed_exactly_a_window_ago_no_longer_counts(self) -> None:
        flow = OrderFlowFacts(working=(working(OrderSide.BUY, seconds_ago=5),))
        assert self.guard.evaluate(make_signal(), healthy(flow=flow)).allowed

    def test_the_opposite_side_is_not_a_duplicate(self) -> None:
        flow = OrderFlowFacts(working=(working(OrderSide.SELL, seconds_ago=1),))
        assert self.guard.evaluate(make_signal(side=OrderSide.BUY), healthy(flow=flow)).allowed

    def test_another_instrument_is_not_a_duplicate(self) -> None:
        flow = OrderFlowFacts(working=(working(OrderSide.BUY, 1, instrument_id=OTHER),))
        assert self.guard.evaluate(make_signal(), healthy(flow=flow)).allowed

    def test_an_exit_is_checked_too(self) -> None:
        flow = OrderFlowFacts(working=(working(OrderSide.SELL, seconds_ago=1),))
        signal = make_signal(kind=EXIT, side=OrderSide.SELL)
        assert not self.guard.evaluate(signal, healthy(flow=flow)).allowed

    @pytest.mark.parametrize("window", [timedelta(0), timedelta(seconds=-1)])
    def test_the_window_must_be_positive(self, window: timedelta) -> None:
        with pytest.raises(ValueError, match="positive"):
            DuplicateOrderGuard(window)


class TestLossGuards:
    """The loss must EXCEED the cap; entries stop, exits never do."""

    def daily(self, pnl: str) -> RiskSnapshot:
        return healthy(account=account(daily_pnl=Money.of(pnl)))

    def test_a_loss_exactly_at_the_daily_cap_still_allows(self) -> None:
        assert (
            MaxDailyLossGuard(Decimal("2000")).evaluate(make_signal(), self.daily("-2000")).allowed
        )

    def test_one_paisa_past_the_daily_cap_blocks_an_entry(self) -> None:
        verdict = MaxDailyLossGuard(Decimal("2000")).evaluate(make_signal(), self.daily("-2000.01"))
        assert not verdict.allowed and verdict.details["cap"] == "2000"

    def test_a_profit_or_a_small_loss_allows(self) -> None:
        guard = MaxDailyLossGuard(Decimal("2000"))
        assert guard.evaluate(make_signal(), self.daily("500")).allowed
        assert guard.evaluate(make_signal(), self.daily("-1")).allowed

    def test_an_exit_is_allowed_after_the_daily_cap_is_breached(self) -> None:
        signal = make_signal(kind=EXIT, side=OrderSide.SELL)
        assert MaxDailyLossGuard(Decimal("2000")).evaluate(signal, self.daily("-9000")).allowed

    def strategy(self, pnl: str, run: str = RUN_ID) -> RiskSnapshot:
        return healthy(account=account(strategy_pnl={run: Money.of(pnl)}))

    def test_a_strategy_loss_exactly_at_its_cap_allows_and_past_it_blocks(self) -> None:
        guard = MaxStrategyLossGuard(Decimal("1000"))
        assert guard.evaluate(make_signal(), self.strategy("-1000")).allowed
        assert not guard.evaluate(make_signal(), self.strategy("-1000.01")).allowed

    def test_another_strategys_loss_does_not_block_this_one(self) -> None:
        guard = MaxStrategyLossGuard(Decimal("1000"))
        assert guard.evaluate(make_signal(), self.strategy("-5000", run="other-run")).allowed

    def test_a_strategy_with_no_recorded_pnl_allows(self) -> None:
        assert MaxStrategyLossGuard(Decimal("1000")).evaluate(make_signal(), healthy()).allowed

    def test_a_strategys_exit_is_allowed_after_its_cap_is_breached(self) -> None:
        signal = make_signal(kind=EXIT, side=OrderSide.SELL)
        assert (
            MaxStrategyLossGuard(Decimal("1000")).evaluate(signal, self.strategy("-9000")).allowed
        )

    @pytest.mark.parametrize("guard", [MaxDailyLossGuard, MaxStrategyLossGuard])
    @pytest.mark.parametrize("cap", [Decimal(0), Decimal(-1)])
    def test_a_loss_cap_must_be_positive(self, guard: type, cap: Decimal) -> None:
        with pytest.raises(ValueError, match="positive"):
            guard(cap)


class TestMaxPositionValueGuard:
    guard = MaxPositionValueGuard(Decimal("25000"))

    def test_a_position_worth_exactly_the_cap_passes(self) -> None:
        assert self.guard.evaluate(make_signal(quantity=250, price="100"), healthy()).allowed

    def test_one_rupee_over_the_cap_is_blocked(self) -> None:
        verdict = self.guard.evaluate(make_signal(quantity=250, price="100.004"), healthy())
        assert not verdict.allowed  # 250 x 100.004 = 25001.00
        assert verdict.details["cap"] == "25000"

    def test_adding_to_a_held_position_counts_the_whole_resulting_position(self) -> None:
        held = healthy(
            account=account(positions={INSTRUMENT: long_position(INSTRUMENT, 200, "100")})
        )
        assert self.guard.evaluate(make_signal(quantity=50, price="100"), held).allowed
        assert not self.guard.evaluate(make_signal(quantity=51, price="100"), held).allowed

    def test_a_pure_reduction_is_never_blocked(self) -> None:
        held = healthy(
            account=account(positions={INSTRUMENT: long_position(INSTRUMENT, 900, "100")})
        )
        sell = make_signal(kind=EXIT, side=OrderSide.SELL, quantity=400, price="100")
        assert self.guard.evaluate(sell, held).allowed

    def test_a_flip_is_measured_by_the_new_position_it_opens(self) -> None:
        held = healthy(
            account=account(positions={INSTRUMENT: long_position(INSTRUMENT, 10, "100")})
        )
        flip = make_signal(side=OrderSide.SELL, quantity=270, price="100")  # long 10 -> short 260
        assert not self.guard.evaluate(flip, held).allowed
        assert self.guard.evaluate(
            make_signal(side=OrderSide.SELL, quantity=260, price="100"), held
        ).allowed

    def test_the_cap_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            MaxPositionValueGuard(Decimal(0))


class TestMaxOpenPositionsGuard:
    guard = MaxOpenPositionsGuard(2)

    def held(self, *instruments: str) -> RiskSnapshot:
        positions = {i: long_position(i, 1, "100") for i in instruments}
        return healthy(account=account(positions=positions))

    def test_a_new_position_is_allowed_below_the_cap(self) -> None:
        assert self.guard.evaluate(make_signal(), self.held(OTHER)).allowed

    def test_a_new_position_is_blocked_at_the_cap(self) -> None:
        verdict = self.guard.evaluate(make_signal(), self.held(OTHER, "NSE:3003"))
        assert not verdict.allowed and verdict.details["open_positions"] == "2"

    def test_adding_to_a_position_already_held_is_not_a_new_position(self) -> None:
        assert self.guard.evaluate(make_signal(), self.held(INSTRUMENT, OTHER)).allowed

    def test_a_flat_row_does_not_count_as_open(self) -> None:
        positions = {
            OTHER: long_position(OTHER, 0, "0"),
            "NSE:3003": long_position("NSE:3003", 1, "1"),
        }
        assert self.guard.evaluate(
            make_signal(), healthy(account=account(positions=positions))
        ).allowed

    def test_the_cap_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            MaxOpenPositionsGuard(0)


class TestMaxCapitalDeployedGuard:
    guard = MaxCapitalDeployedGuard(Decimal("60000"))

    def deployed(self, quantity: int, price: str) -> RiskSnapshot:
        return healthy(account=account(positions={OTHER: long_position(OTHER, quantity, price)}))

    def test_total_capital_exactly_at_the_cap_passes(self) -> None:
        signal = make_signal(quantity=100, price="100")  # 10,000 more
        assert self.guard.evaluate(signal, self.deployed(500, "100")).allowed

    def test_one_share_over_the_cap_blocks(self) -> None:
        signal = make_signal(quantity=101, price="100")  # 50,000 + 10,100
        verdict = self.guard.evaluate(signal, self.deployed(500, "100"))
        assert not verdict.allowed and verdict.details["deployed"] == "60100"

    def test_a_short_position_deploys_capital_too(self) -> None:
        held = healthy(account=account(positions={OTHER: long_position(OTHER, -500, "100")}))
        assert not self.guard.evaluate(make_signal(quantity=101, price="100"), held).allowed

    def test_a_reduction_never_blocks_even_when_already_over_the_cap(self) -> None:
        over = healthy(
            account=account(positions={INSTRUMENT: long_position(INSTRUMENT, 900, "100")})
        )
        sell = make_signal(kind=EXIT, side=OrderSide.SELL, quantity=100, price="100")
        assert self.guard.evaluate(sell, over).allowed

    def test_the_cap_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            MaxCapitalDeployedGuard(Decimal(-1))


class TestMaxOrderQuantityGuard:
    guard = MaxOrderQuantityGuard(500)

    def test_exactly_the_cap_passes(self) -> None:
        assert self.guard.evaluate(make_signal(quantity=500), healthy()).allowed

    def test_one_share_over_blocks(self) -> None:
        verdict = self.guard.evaluate(make_signal(quantity=501), healthy())
        assert not verdict.allowed and verdict.details["quantity"] == "501"

    def test_it_applies_to_exits(self) -> None:
        signal = make_signal(kind=EXIT, side=OrderSide.SELL, quantity=501)
        assert not self.guard.evaluate(signal, healthy()).allowed

    def test_the_cap_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            MaxOrderQuantityGuard(0)


class TestPriceSanityGuard:
    guard = PriceSanityGuard(Decimal("2"))

    @pytest.mark.parametrize("price", ["98", "102", "100", "99.5"])
    def test_a_price_within_the_band_passes_including_exactly_at_the_edge(self, price: str) -> None:
        assert self.guard.evaluate(make_signal(price=price), healthy()).allowed

    @pytest.mark.parametrize("price", ["97.99", "102.01"])
    def test_a_price_just_outside_the_band_blocks(self, price: str) -> None:
        verdict = self.guard.evaluate(make_signal(price=price), healthy())
        assert not verdict.allowed and "too far" in verdict.reason

    def test_a_price_below_the_lower_circuit_blocks_even_inside_the_band(self) -> None:
        wide = PriceSanityGuard(Decimal("50"))
        market = calm_market(lower_circuit=Money.of("99"))
        verdict = wide.evaluate(make_signal(price="98.9"), healthy(markets={INSTRUMENT: market}))
        assert not verdict.allowed and "lower circuit" in verdict.reason

    def test_a_price_above_the_upper_circuit_blocks(self) -> None:
        wide = PriceSanityGuard(Decimal("50"))
        market = calm_market(upper_circuit=Money.of("101"))
        verdict = wide.evaluate(make_signal(price="101.1"), healthy(markets={INSTRUMENT: market}))
        assert not verdict.allowed and "upper circuit" in verdict.reason

    def test_a_price_exactly_on_a_circuit_passes(self) -> None:
        wide = PriceSanityGuard(Decimal("50"))
        market = calm_market(lower_circuit=Money.of("99"), upper_circuit=Money.of("101"))
        snapshot = healthy(markets={INSTRUMENT: market})
        assert wide.evaluate(make_signal(price="99"), snapshot).allowed
        assert wide.evaluate(make_signal(price="101"), snapshot).allowed

    def test_missing_circuit_limits_are_simply_not_checked(self) -> None:
        market = calm_market(lower_circuit=None, upper_circuit=None)
        assert self.guard.evaluate(make_signal(), healthy(markets={INSTRUMENT: market})).allowed

    def test_no_last_price_blocks(self) -> None:
        market = calm_market(ltp=None)
        assert not self.guard.evaluate(make_signal(), healthy(markets={INSTRUMENT: market})).allowed

    @pytest.mark.parametrize("pct", [Decimal(0), Decimal(100), Decimal(-1)])
    def test_the_band_must_be_between_zero_and_a_hundred_percent(self, pct: Decimal) -> None:
        with pytest.raises(ValueError, match="between"):
            PriceSanityGuard(pct)


class TestAbnormalSpreadGuard:
    guard = AbnormalSpreadGuard(Decimal("20"))

    def quoted(self, bid: str | None, ask: str | None) -> RiskSnapshot:
        market = calm_market(bid=None if bid is None else Money.of(bid),
                             ask=None if ask is None else Money.of(ask))  # fmt: skip
        return healthy(markets={INSTRUMENT: market})

    def test_a_tight_spread_allows_an_entry(self) -> None:
        assert self.guard.evaluate(make_signal(), self.quoted("99.95", "100.05")).allowed

    def test_a_spread_of_exactly_the_cap_passes(self) -> None:
        # (100.10 - 99.90) / 100 = 20 bps exactly
        assert self.guard.evaluate(make_signal(), self.quoted("99.9", "100.1")).allowed

    def test_a_spread_just_past_the_cap_blocks(self) -> None:
        verdict = self.guard.evaluate(make_signal(), self.quoted("99.89", "100.11"))
        assert not verdict.allowed and "wide" in verdict.reason

    @pytest.mark.parametrize(
        ("bid", "ask"), [(None, "100"), ("100", None), ("0", "1"), ("101", "100")]
    )
    def test_a_quote_that_cannot_be_measured_blocks(self, bid: str | None, ask: str | None) -> None:
        assert not self.guard.evaluate(make_signal(), self.quoted(bid, ask)).allowed

    def test_an_exit_is_allowed_whatever_the_spread(self) -> None:
        signal = make_signal(kind=EXIT, side=OrderSide.SELL)
        assert self.guard.evaluate(signal, self.quoted("50", "150")).allowed

    def test_the_cap_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            AbnormalSpreadGuard(Decimal(0))


class TestShortsFlowThroughTheGuards:
    """EM-127: the guards are direction-neutral, but they must be proven so for SELL-to-open
    entries and BUY-to-cover exits — a short entry is an ENTRY that deploys exposure, a cover is an
    EXIT that must never be stranded."""

    def short_entry(self, **kw: Any) -> Signal:
        return make_signal(side=OrderSide.SELL, **kw)

    def cover(self, **kw: Any) -> Signal:
        return make_signal(kind=EXIT, side=OrderSide.BUY, **kw)

    def short_held(self, quantity: int) -> RiskSnapshot:
        positions = {INSTRUMENT: long_position(INSTRUMENT, -quantity, "100")}
        return healthy(account=account(positions=positions))

    def held(self, *instruments: str) -> RiskSnapshot:
        positions = {i: long_position(i, 1, "100") for i in instruments}
        return healthy(account=account(positions=positions))

    def test_a_short_entry_is_measured_exactly_like_a_long_entry(self) -> None:
        guard = MaxPositionValueGuard(Decimal("25000"))
        assert guard.evaluate(self.short_entry(quantity=250, price="100"), healthy()).allowed
        verdict = guard.evaluate(self.short_entry(quantity=250, price="100.004"), healthy())
        assert not verdict.allowed  # -250 x 100.004 = 25001.00

    def test_a_cover_that_only_reduces_the_short_is_never_blocked(self) -> None:
        guard = MaxPositionValueGuard(Decimal("25000"))
        over = self.short_held(900)
        assert guard.evaluate(self.cover(quantity=400, price="100"), over).allowed

    def test_a_short_entry_opens_a_position_and_counts_against_the_cap(self) -> None:
        guard = MaxOpenPositionsGuard(2)
        held = self.held(OTHER)
        assert guard.evaluate(self.short_entry(), held).allowed
        blocked = self.held(OTHER, "NSE:3003")
        assert not guard.evaluate(self.short_entry(), blocked).allowed

    def test_adding_to_an_open_short_is_not_a_new_position(self) -> None:
        guard = MaxOpenPositionsGuard(2)
        assert guard.evaluate(self.short_entry(), self.held(INSTRUMENT, OTHER)).allowed

    def test_a_short_entry_deploys_capital_like_a_long_entry(self) -> None:
        guard = MaxCapitalDeployedGuard(Decimal("60000"))
        # -500 @ 100 on OTHER (50,000) + a fresh -100 short entry on INSTRUMENT (10,000)
        held = healthy(account=account(positions={OTHER: long_position(OTHER, -500, "100")}))
        assert guard.evaluate(self.short_entry(quantity=100, price="100"), held).allowed
        verdict = guard.evaluate(self.short_entry(quantity=101, price="100"), held)
        assert not verdict.allowed and verdict.details["deployed"] == "60100"

    def test_a_short_cover_is_allowed_after_the_loss_caps_are_breached(self) -> None:
        daily = account(daily_pnl=Money.of("-9000"))
        verdict = MaxDailyLossGuard(Decimal("2000")).evaluate(self.cover(), healthy(account=daily))
        assert verdict.allowed
        strategy = account(strategy_pnl={RUN_ID: Money.of("-9000")})
        assert (
            MaxStrategyLossGuard(Decimal("1000"))
            .evaluate(self.cover(), healthy(account=strategy))
            .allowed
        )

    def test_a_short_entry_is_blocked_after_the_daily_loss_cap_is_breached(self) -> None:
        pnl = account(daily_pnl=Money.of("-2000.01"))
        assert (
            not MaxDailyLossGuard(Decimal("2000"))
            .evaluate(self.short_entry(), healthy(account=pnl))
            .allowed
        )

    def test_a_working_sell_blocks_a_second_short_entry_but_not_a_cover(self) -> None:
        guard = DuplicateOrderGuard(timedelta(seconds=5))
        flow = OrderFlowFacts(working=(working(OrderSide.SELL, seconds_ago=1),))
        assert not guard.evaluate(self.short_entry(), healthy(flow=flow)).allowed
        assert guard.evaluate(self.cover(), healthy(flow=flow)).allowed

    def test_an_open_short_is_checked_for_spread_but_a_cover_never_is(self) -> None:
        guard = AbnormalSpreadGuard(Decimal("20"))
        wide = {
            INSTRUMENT: calm_market(bid=Money.of("99.89"), ask=Money.of("100.11")),
        }
        assert not guard.evaluate(self.short_entry(), healthy(markets=wide)).allowed
        assert guard.evaluate(self.cover(), healthy(markets=wide)).allowed

    def test_a_short_exit_is_not_frozen_by_the_sanity_guards(self) -> None:
        over = self.short_held(900)
        assert MaxOrderQuantityGuard(500).evaluate(self.cover(quantity=400), over).allowed
        assert PriceSanityGuard(Decimal("2")).evaluate(self.cover(price="100"), over).allowed


class TestOrderRateGuard:
    guard = OrderRateGuard(per_second=2, per_minute=5)

    def sent(self, *seconds_ago: float) -> RiskSnapshot:
        times = tuple(NOW - timedelta(seconds=s) for s in seconds_ago)
        return healthy(flow=OrderFlowFacts(recent_order_times=times))

    def test_nothing_sent_recently_allows(self) -> None:
        assert self.guard.evaluate(make_signal(), healthy()).allowed

    def test_below_the_per_second_budget_allows(self) -> None:
        assert self.guard.evaluate(make_signal(), self.sent(0.5)).allowed

    def test_a_spent_per_second_budget_blocks(self) -> None:
        verdict = self.guard.evaluate(make_signal(), self.sent(0.1, 0.9))
        assert not verdict.allowed and "per-second" in verdict.reason

    def test_an_order_a_full_second_old_no_longer_counts_against_the_second(self) -> None:
        assert self.guard.evaluate(make_signal(), self.sent(0.5, 1.0)).allowed

    def test_a_spent_per_minute_budget_blocks(self) -> None:
        verdict = self.guard.evaluate(make_signal(), self.sent(2, 15, 30, 45, 59.9))
        assert not verdict.allowed and "per-minute" in verdict.reason

    def test_an_order_a_full_minute_old_no_longer_counts(self) -> None:
        assert self.guard.evaluate(make_signal(), self.sent(2, 15, 30, 45, 60)).allowed

    @pytest.mark.parametrize(("second", "minute"), [(0, 5), (2, 0), (-1, 5)])
    def test_the_budgets_must_be_positive(self, second: int, minute: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            OrderRateGuard(second, minute)
