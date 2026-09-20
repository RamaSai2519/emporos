"""Guards about HOW MUCH: exposure, loss, size, price and rate (plan.md §11).

Boundaries are exact and are tested exactly: an order worth precisely the cap passes, one paisa
over is blocked. Loss caps stop new ENTRIES only — once a cap is hit the position that caused the
loss must still be closable, and a rule that blocked the exit would strand it. `PriceSanityGuard`
and `OrderRateGuard` apply to every order because a wild price or a runaway loop is dangerous in
either direction.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from emporos.domain.money import Money
from emporos.domain.signals import Signal
from emporos.risk.rules.base import added_exposure, is_entry, net_after
from emporos.risk.snapshot import RiskSnapshot
from emporos.risk.verdict import RuleVerdict

_ONE_SECOND = timedelta(seconds=1)
_ONE_MINUTE = timedelta(minutes=1)
_HUNDRED = Decimal(100)
_BPS = Decimal(10_000)


class DuplicateOrderGuard:
    """Blocks a second live order on the same instrument and side inside the window."""

    name = "DuplicateOrderGuard"

    def __init__(self, window: timedelta) -> None:
        if window <= timedelta(0):
            raise ValueError("the duplicate window must be positive")
        self._window = window

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        for order in snapshot.flow.working:
            same = order.instrument_id == signal.instrument_id and order.side is signal.side
            if same and snapshot.now - order.placed_at < self._window:
                return RuleVerdict.block(
                    "a live order on this instrument and side is already working",
                    placed_at=order.placed_at.isoformat(),
                )
        return RuleVerdict.allow()


class _LossGuard:
    name = ""

    def __init__(self, cap: Decimal) -> None:
        if cap <= 0:
            raise ValueError("a loss cap must be positive")
        self._cap = Money(cap)

    def _blocked(self, pnl: Money) -> bool:
        return -pnl > self._cap  # the loss must EXCEED the cap


class MaxDailyLossGuard(_LossGuard):
    """Blocks new entries, for every strategy, once the day's loss exceeds the cap."""

    name = "MaxDailyLossGuard"

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        if is_entry(signal) and self._blocked(snapshot.account.daily_pnl):
            return RuleVerdict.block(
                "the daily loss cap is exceeded",
                daily_pnl=snapshot.account.daily_pnl.amount, cap=self._cap.amount,
            )  # fmt: skip
        return RuleVerdict.allow()


class MaxStrategyLossGuard(_LossGuard):
    """Blocks new entries from one strategy run once that run's loss exceeds the cap."""

    name = "MaxStrategyLossGuard"

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        pnl = snapshot.account.strategy_pnl.get(signal.strategy_run_id, Money.zero())
        if is_entry(signal) and self._blocked(pnl):
            return RuleVerdict.block(
                "this strategy's loss cap is exceeded",
                strategy_run_id=signal.strategy_run_id, strategy_pnl=pnl.amount,
                cap=self._cap.amount,
            )  # fmt: skip
        return RuleVerdict.allow()


class MaxPositionValueGuard:
    """Blocks an order that would take one instrument's position above the cap.

    An order that only reduces the position is never blocked; one that flips it is measured by
    the new position it opens."""

    name = "MaxPositionValueGuard"

    def __init__(self, cap: Decimal) -> None:
        if cap <= 0:
            raise ValueError("the position value cap must be positive")
        self._cap = Money(cap)

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        if added_exposure(signal, snapshot) == 0:
            return RuleVerdict.allow()
        _, after = net_after(signal, snapshot)
        value = signal.limit_price.times(abs(after))
        if value > self._cap:
            return RuleVerdict.block(
                "the position in this instrument would exceed its value cap",
                position_value=value.amount, cap=self._cap.amount,
            )  # fmt: skip
        return RuleVerdict.allow()


class MaxOpenPositionsGuard:
    """Blocks an order that opens one position too many. Adding to a held position is fine."""

    name = "MaxOpenPositionsGuard"

    def __init__(self, cap: int) -> None:
        if cap <= 0:
            raise ValueError("the open positions cap must be positive")
        self._cap = cap

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        opens_new = snapshot.account.position(signal.instrument_id).is_flat
        held = snapshot.account.open_position_count
        if opens_new and held >= self._cap:
            return RuleVerdict.block(
                "the open positions cap is reached", open_positions=held, cap=self._cap
            )
        return RuleVerdict.allow()


class MaxCapitalDeployedGuard:
    """Blocks an order that would take total deployed capital above the cap."""

    name = "MaxCapitalDeployedGuard"

    def __init__(self, cap: Decimal) -> None:
        if cap <= 0:
            raise ValueError("the deployed capital cap must be positive")
        self._cap = Money(cap)

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        extra = signal.limit_price.times(added_exposure(signal, snapshot))
        deployed = snapshot.account.capital_deployed + extra
        if extra > Money.zero() and deployed > self._cap:
            return RuleVerdict.block(
                "total deployed capital would exceed its cap",
                deployed=deployed.amount, cap=self._cap.amount,
            )  # fmt: skip
        return RuleVerdict.allow()


class MaxOrderQuantityGuard:
    """Fat-finger protection: no single order above this many shares."""

    name = "MaxOrderQuantityGuard"

    def __init__(self, cap: int) -> None:
        if cap <= 0:
            raise ValueError("the order quantity cap must be positive")
        self._cap = cap

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        if signal.quantity > self._cap:
            return RuleVerdict.block(
                "the order quantity exceeds the fat-finger cap", quantity=signal.quantity,
                cap=self._cap,
            )  # fmt: skip
        return RuleVerdict.allow()


class PriceSanityGuard:
    """Blocks a limit price outside the circuit limits, or too far from the last price.

    With no last price there is nothing to judge the price against, so the order is blocked."""

    name = "PriceSanityGuard"

    def __init__(self, max_deviation_pct: Decimal) -> None:
        if not 0 < max_deviation_pct < _HUNDRED:
            raise ValueError("the price deviation must be between 0 and 100 percent")
        self._max = max_deviation_pct

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        market = snapshot.market(signal.instrument_id)
        price = signal.limit_price
        if market.ltp is None:
            return RuleVerdict.block("no last price to check the limit price against")
        lower, upper = market.lower_circuit, market.upper_circuit
        if lower is not None and price < lower:
            return RuleVerdict.block(
                "the limit price is below the lower circuit",
                price=price.amount, lower_circuit=lower.amount,
            )  # fmt: skip
        if upper is not None and price > upper:
            return RuleVerdict.block(
                "the limit price is above the upper circuit",
                price=price.amount, upper_circuit=upper.amount,
            )  # fmt: skip
        deviation = abs(price.amount - market.ltp.amount) / market.ltp.amount * _HUNDRED
        if deviation > self._max:
            return RuleVerdict.block(
                "the limit price is too far from the last price",
                price=price.amount, ltp=market.ltp.amount, deviation_pct=deviation,
                cap_pct=self._max,
            )  # fmt: skip
        return RuleVerdict.allow()


class AbnormalSpreadGuard:
    """Blocks ENTRIES when the bid/ask spread is wider than tolerated, or cannot be measured."""

    name = "AbnormalSpreadGuard"

    def __init__(self, max_spread_bps: Decimal) -> None:
        if max_spread_bps <= 0:
            raise ValueError("the spread cap must be positive")
        self._max = max_spread_bps

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        if not is_entry(signal):
            return RuleVerdict.allow()
        market = snapshot.market(signal.instrument_id)
        bid, ask = market.bid, market.ask
        if bid is None or ask is None or bid <= Money.zero() or ask < bid:
            return RuleVerdict.block("no valid bid/ask to measure the spread")
        mid = (bid.amount + ask.amount) / 2
        spread = (ask.amount - bid.amount) / mid * _BPS
        if spread > self._max:
            return RuleVerdict.block(
                "the bid/ask spread is abnormally wide", spread_bps=spread, cap_bps=self._max
            )
        return RuleVerdict.allow()


class OrderRateGuard:
    """The self-imposed OPS budget: blocks an order that would exceed it (well under the
    broker's own limit, so we never provoke its defective rate limiter)."""

    name = "OrderRateGuard"

    def __init__(self, per_second: int, per_minute: int) -> None:
        if per_second <= 0 or per_minute <= 0:
            raise ValueError("order rate budgets must be positive")
        self._per_second = per_second
        self._per_minute = per_minute

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        now = snapshot.now
        last_second = sum(1 for t in snapshot.flow.recent_order_times if now - t < _ONE_SECOND)
        last_minute = sum(1 for t in snapshot.flow.recent_order_times if now - t < _ONE_MINUTE)
        if last_second >= self._per_second:
            return RuleVerdict.block(
                "the per-second order budget is spent", sent=last_second, budget=self._per_second
            )
        if last_minute >= self._per_minute:
            return RuleVerdict.block(
                "the per-minute order budget is spent", sent=last_minute, budget=self._per_minute
            )
        return RuleVerdict.allow()
