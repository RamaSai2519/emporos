"""Guards about the state of the SYSTEM rather than the size of the order (plan.md §11).

Every one of them fails safe: an absent or unread fact is a reason to block, never to allow.
`StaleDataGuard` is the only one that lets exits through — an exit reduces exposure, and a feed
going quiet is exactly when a position most needs closing (plan.md §7).
"""

from __future__ import annotations

from emporos.domain.signals import Signal
from emporos.domain.trading_mode import TradingMode
from emporos.marketdata.session import SessionWindow
from emporos.risk.rules.base import is_entry
from emporos.risk.snapshot import ReconciliationStatus, RiskSnapshot
from emporos.risk.verdict import RuleVerdict


class TradingModeGuard:
    """Live orders need `LIVE_TRADING_ENABLED=true`. Paper orders are never affected."""

    name = "TradingModeGuard"

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        system = snapshot.system
        if system.mode is TradingMode.LIVE and not system.live_trading_enabled:
            return RuleVerdict.block(
                "live trading is not enabled (LIVE_TRADING_ENABLED is not true)",
                mode=system.mode.value,
            )
        return RuleVerdict.allow()


class KillSwitchGuard:
    """Blocks everything while the kill switch is set — and while its state cannot be read."""

    name = "KillSwitchGuard"

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        switch = snapshot.system.kill_switch
        if not switch.known:
            return RuleVerdict.block(
                "the kill switch state is unknown, so it is treated as set", source=switch.source
            )
        if switch.halted:
            return RuleVerdict.block(
                "the kill switch is set", source=switch.source, reason=switch.reason
            )
        return RuleVerdict.allow()


class MarketSessionGuard:
    """Blocks orders outside the trading session and on non-trading days."""

    name = "MarketSessionGuard"

    def __init__(self, window: SessionWindow) -> None:
        self._window = window

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        if self._window.contains(snapshot.now):
            return RuleVerdict.allow()
        return RuleVerdict.block("outside the trading session", now=snapshot.now.isoformat())


class StaleDataGuard:
    """Blocks ENTRIES on an instrument whose feed is stale; exits are always allowed."""

    name = "StaleDataGuard"

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        if not is_entry(signal):
            return RuleVerdict.allow()
        if snapshot.market(signal.instrument_id).stale:
            return RuleVerdict.block(
                "the market data feed for this instrument is stale",
                instrument_id=signal.instrument_id,
            )
        return RuleVerdict.allow()


class BrokerHealthGuard:
    name = "BrokerHealthGuard"

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        system = snapshot.system
        if not system.broker_session_ok:
            return RuleVerdict.block("the broker session is not healthy")
        if not system.order_feed_ok:
            return RuleVerdict.block("the broker order-update feed is not healthy")
        return RuleVerdict.allow()


class ReconciliationGuard:
    """Blocks orders unless the last reconciliation finished clean."""

    name = "ReconciliationGuard"

    def evaluate(self, signal: Signal, snapshot: RiskSnapshot) -> RuleVerdict:
        status = snapshot.system.reconciliation
        if status is ReconciliationStatus.CLEAN:
            return RuleVerdict.allow()
        return RuleVerdict.block(f"reconciliation is {status.value.lower()}", status=status.value)
