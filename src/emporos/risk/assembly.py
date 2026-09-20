"""Builds the `RiskSnapshot` a signal is judged against, from small independent sources.

Each source answers one question (system state, account, one instrument's market, order flow), so
each can be wired, tested and replaced on its own. The defaults for the things nothing reports yet
are FAIL-SAFE: broker health and reconciliation read as not-ok until a real source says otherwise.
"""

from __future__ import annotations

from typing import Protocol

from emporos.core.clock import Clock
from emporos.domain.signals import Signal
from emporos.domain.trading_mode import TradingMode
from emporos.risk.kill_switch import KillSwitchMonitor
from emporos.risk.snapshot import (
    AccountFacts,
    InstrumentMarket,
    OrderFlowFacts,
    ReconciliationStatus,
    RiskSnapshot,
    SystemFacts,
)


class SystemFactsSource(Protocol):
    async def system_facts(self) -> SystemFacts: ...


class AccountFactsSource(Protocol):
    async def account_facts(self, signal: Signal) -> AccountFacts: ...


class MarketFactsSource(Protocol):
    async def market_facts(self, instrument_id: str) -> InstrumentMarket: ...


class OrderFlowSource(Protocol):
    async def order_flow(self) -> OrderFlowFacts: ...


class BrokerHealthSource(Protocol):
    def session_ok(self) -> bool: ...

    def order_feed_ok(self) -> bool: ...


class ReconciliationSource(Protocol):
    def status(self) -> ReconciliationStatus: ...


class UnreportedBrokerHealth:
    """Nothing reports broker health yet, so it reads as unhealthy: the guard blocks."""

    def session_ok(self) -> bool:
        return False

    def order_feed_ok(self) -> bool:
        return False


class UnreportedReconciliation:
    def status(self) -> ReconciliationStatus:
        return ReconciliationStatus.PENDING


class MonitoredSystemFacts:
    """System state: the mode and switch from configuration, the kill switch from its monitor."""

    def __init__(
        self,
        mode: TradingMode,
        live_trading_enabled: bool,
        kill_switch: KillSwitchMonitor,
        health: BrokerHealthSource,
        reconciliation: ReconciliationSource,
    ) -> None:
        self._mode = mode
        self._live_enabled = live_trading_enabled
        self._kill_switch = kill_switch
        self._health = health
        self._reconciliation = reconciliation

    async def system_facts(self) -> SystemFacts:
        return SystemFacts(
            mode=self._mode,
            live_trading_enabled=self._live_enabled,
            kill_switch=self._kill_switch.reading(),
            broker_session_ok=self._health.session_ok(),
            order_feed_ok=self._health.order_feed_ok(),
            reconciliation=self._reconciliation.status(),
        )


class SnapshotAssembler:
    """A `SnapshotProvider`: asks each source once and freezes the answers together."""

    def __init__(
        self,
        clock: Clock,
        system: SystemFactsSource,
        account: AccountFactsSource,
        markets: MarketFactsSource,
        flow: OrderFlowSource,
    ) -> None:
        self._clock = clock
        self._system = system
        self._account = account
        self._markets = markets
        self._flow = flow

    async def snapshot(self, signal: Signal) -> RiskSnapshot:
        instrument = signal.instrument_id
        return RiskSnapshot(
            now=self._clock.now(),
            system=await self._system.system_facts(),
            account=await self._account.account_facts(signal),
            flow=await self._flow.order_flow(),
            markets={instrument: await self._markets.market_facts(instrument)},
        )
