"""The real risk rules, in the backtest's signal path (EM-99 I3: "risk gates every order").

A backtest without risk limits reports what a strategy WOULD have done with unlimited freedom, which
is not what it will be allowed to do. `RiskRuleGate` runs the platform's own rule set — the same
classes, the same limits from `config/risk.yaml` — over a snapshot built from the simulated book, so
position, exposure, loss and price-sanity limits bind a backtest exactly as they bind a live order.

What a backtest cannot know is stated, not hidden, and set so it never BLOCKS spuriously:

* the kill switch, broker health and reconciliation are healthy (nothing can be unhealthy in a
  replay); the mode is PAPER;
* there is no order book, so bid and ask are the last price: the spread rule measures zero. The cost
  of crossing the spread is in the marketable-limit buffer and the charges, not here;
* there are no circuit limits in the bar data, so the NSE's common 20% band around the last price
  is assumed. It only bounds the limit price; the deviation rule is far tighter.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from emporos.backtest.pricing import GateContext, GateRejection
from emporos.domain.money import Money
from emporos.domain.signals import Signal
from emporos.domain.trading_mode import TradingMode
from emporos.marketdata.session import SessionWindow
from emporos.risk.limits import RiskLimits
from emporos.risk.rules.base import RiskRule
from emporos.risk.snapshot import (
    AccountFacts,
    InstrumentMarket,
    KillSwitchReading,
    OrderFlowFacts,
    ReconciliationStatus,
    RiskSnapshot,
    SystemFacts,
    WorkingOrder,
)
from emporos.risk.standard import StandardRuleSet

CIRCUIT_BAND = Decimal("0.20")
_MINUTE = timedelta(minutes=1)


class RiskRuleGate:
    """A `SignalGate` that applies the platform's rules. Build one per run (it keeps a short memory
    of when it let orders through, for the order-rate rule)."""

    name = "risk_rules"

    def __init__(self, rules: Sequence[RiskRule], context: GateContext) -> None:
        self._rules = tuple(rules)
        self._context = context
        self._sent: deque[datetime] = deque()

    def review(self, signal: Signal) -> Signal | GateRejection:
        try:
            snapshot = self._snapshot(signal)
        except LookupError:
            # A signal for an instrument no bar has priced yet cannot be judged, so it is refused
            # (the same as risk's "no last price" rule) rather than crashing the run.
            return GateRejection(f"no price has been seen for {signal.instrument_id}")
        for rule in self._rules:
            verdict = rule.evaluate(signal, snapshot)
            if not verdict.allowed:
                return GateRejection(f"{rule.name}: {verdict.reason}")
        self._sent.append(snapshot.now)
        return signal

    def _snapshot(self, signal: Signal) -> RiskSnapshot:
        context = self._context
        now = context.clock.now()
        while self._sent and now - self._sent[0] > _MINUTE:
            self._sent.popleft()
        portfolio = context.portfolio
        pnl = portfolio.realised - portfolio.fees + portfolio.unrealised
        held = {p.instrument_id: p for p in portfolio.open_positions()}
        last = portfolio.last_price(signal.instrument_id)
        band = last.amount * CIRCUIT_BAND
        return RiskSnapshot(
            now=now,
            system=SystemFacts(
                mode=TradingMode.PAPER,
                live_trading_enabled=False,
                kill_switch=KillSwitchReading(halted=False, known=True, source="backtest"),
                broker_session_ok=True,
                order_feed_ok=True,
                reconciliation=ReconciliationStatus.CLEAN,
            ),
            account=AccountFacts(
                positions=held,
                daily_pnl=Money(pnl.amount),
                strategy_pnl={signal.strategy_run_id: Money(pnl.amount)},
            ),
            flow=OrderFlowFacts(
                working=tuple(
                    WorkingOrder(o.request.instrument_id, o.request.side, now)
                    for o in context.broker.open_orders()
                ),
                recent_order_times=tuple(self._sent),
            ),
            markets={
                signal.instrument_id: InstrumentMarket(
                    ltp=last,
                    bid=last,
                    ask=last,
                    lower_circuit=Money(last.amount - band),
                    upper_circuit=Money(last.amount + band),
                    stale=False,
                )
            },
        )


class RiskGateFactory:
    """What the engine takes: a callable that builds one gate per run from that run's context."""

    def __init__(self, limits: RiskLimits, window: SessionWindow | None = None) -> None:
        self._rules = StandardRuleSet(limits, window or SessionWindow())

    def __call__(self, context: GateContext) -> RiskRuleGate:
        return RiskRuleGate(self._rules.rules(), context)
