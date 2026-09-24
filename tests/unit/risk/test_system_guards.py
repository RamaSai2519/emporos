"""Each system guard: an explicit allow case and an explicit block case (EM-71)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from emporos.domain.signals import SignalKind
from emporos.domain.trading_mode import TradingMode
from emporos.marketdata.session import SessionWindow
from emporos.risk.rules.system import (
    BrokerHealthGuard,
    KillSwitchGuard,
    MarketSessionGuard,
    ReconciliationGuard,
    StaleDataGuard,
    TradingModeGuard,
)
from emporos.risk.snapshot import (
    InstrumentMarket,
    KillSwitchReading,
    ReconciliationStatus,
    RiskSnapshot,
    SystemFacts,
)
from tests.support.risk import NOW, calm_market, healthy, healthy_system
from tests.support.strategies import INSTRUMENT, make_signal


class TestTradingModeGuard:
    def test_a_live_order_is_allowed_when_live_trading_is_enabled(self) -> None:
        assert TradingModeGuard().evaluate(make_signal(), healthy()).allowed

    def test_a_live_order_is_blocked_when_live_trading_is_not_enabled(self) -> None:
        snapshot = healthy(system=healthy_system(live_trading_enabled=False))
        verdict = TradingModeGuard().evaluate(make_signal(), snapshot)
        assert not verdict.allowed and "LIVE_TRADING_ENABLED" in verdict.reason

    def test_a_paper_order_is_allowed_whether_or_not_live_trading_is_enabled(self) -> None:
        snapshot = healthy(
            system=healthy_system(mode=TradingMode.PAPER, live_trading_enabled=False)
        )
        assert TradingModeGuard().evaluate(make_signal(), snapshot).allowed

    def test_it_blocks_by_default_because_a_bare_snapshot_is_live_and_disabled(self) -> None:
        assert not TradingModeGuard().evaluate(make_signal(), RiskSnapshot(now=NOW)).allowed
        assert SystemFacts().live_trading_enabled is False


class TestKillSwitchGuard:
    def test_it_allows_when_the_switch_is_known_to_be_clear(self) -> None:
        assert KillSwitchGuard().evaluate(make_signal(), healthy()).allowed

    def test_it_blocks_when_the_switch_is_set(self) -> None:
        reading = KillSwitchReading(halted=True, known=True, source="mongo", reason="operator")
        verdict = KillSwitchGuard().evaluate(
            make_signal(), healthy(system=healthy_system(kill_switch=reading))
        )
        assert not verdict.allowed
        assert verdict.details["source"] == "mongo" and verdict.details["reason"] == "operator"

    def test_it_blocks_exits_too(self) -> None:
        reading = KillSwitchReading(halted=True, known=True)
        snapshot = healthy(system=healthy_system(kill_switch=reading))
        assert not KillSwitchGuard().evaluate(make_signal(kind=SignalKind.EXIT), snapshot).allowed

    def test_a_tripwire_halt_blocks_entries_but_lets_an_exit_through(self) -> None:
        reading = KillSwitchReading(halted=True, known=True, exits_permitted=True)
        snapshot = healthy(system=healthy_system(kill_switch=reading))
        guard = KillSwitchGuard()
        assert not guard.evaluate(make_signal(kind=SignalKind.ENTRY), snapshot).allowed
        assert guard.evaluate(make_signal(kind=SignalKind.EXIT), snapshot).allowed

    def test_an_unreadable_switch_blocks_exits_even_if_a_tripwire_flag_is_set(self) -> None:
        reading = KillSwitchReading(halted=True, known=False, exits_permitted=True)
        snapshot = healthy(system=healthy_system(kill_switch=reading))
        assert not KillSwitchGuard().evaluate(make_signal(kind=SignalKind.EXIT), snapshot).allowed

    def test_it_blocks_when_the_state_has_never_been_read(self) -> None:
        unread = KillSwitchReading()  # the default: halted, unknown
        verdict = KillSwitchGuard().evaluate(
            make_signal(), healthy(system=healthy_system(kill_switch=unread))
        )
        assert not verdict.allowed and "unknown" in verdict.reason

    def test_an_unknown_reading_blocks_even_if_it_claims_not_halted(self) -> None:
        reading = KillSwitchReading(halted=False, known=False)
        snapshot = healthy(system=healthy_system(kill_switch=reading))
        assert not KillSwitchGuard().evaluate(make_signal(), snapshot).allowed


class TestMarketSessionGuard:
    window = SessionWindow()

    @pytest.mark.parametrize(
        ("moment", "allowed"),
        [
            (datetime(2026, 1, 5, 3, 45, tzinfo=UTC), True),  # 09:15 IST Monday, the open
            (datetime(2026, 1, 5, 9, 59, 59, tzinfo=UTC), True),  # 15:29:59 IST
            (datetime(2026, 1, 5, 3, 44, 59, tzinfo=UTC), False),  # 09:14:59 IST
            (datetime(2026, 1, 5, 10, 0, tzinfo=UTC), False),  # 15:30:00 IST, the close
            (datetime(2026, 1, 3, 5, 0, tzinfo=UTC), False),  # a Saturday
        ],
    )
    def test_only_the_session_window_on_a_trading_day_is_allowed(
        self, moment: datetime, allowed: bool
    ) -> None:
        snapshot = healthy()
        snapshot = replace(snapshot, now=moment)
        assert MarketSessionGuard(self.window).evaluate(make_signal(), snapshot).allowed is allowed

    def test_a_holiday_is_blocked_even_inside_the_hours(self) -> None:
        class Holiday:
            def is_trading_day(self, day: object) -> bool:
                return False

        window = SessionWindow(calendar=Holiday())  # type: ignore[arg-type]
        assert not MarketSessionGuard(window).evaluate(make_signal(), healthy()).allowed


class TestStaleDataGuard:
    def test_a_fresh_feed_allows_an_entry(self) -> None:
        assert StaleDataGuard().evaluate(make_signal(), healthy()).allowed

    def test_a_stale_feed_blocks_an_entry(self) -> None:
        snapshot = healthy(markets={INSTRUMENT: calm_market(stale=True)})
        verdict = StaleDataGuard().evaluate(make_signal(), snapshot)
        assert not verdict.allowed and verdict.details["instrument_id"] == INSTRUMENT

    def test_a_stale_feed_still_allows_an_exit(self) -> None:
        snapshot = healthy(markets={INSTRUMENT: calm_market(stale=True)})
        assert StaleDataGuard().evaluate(make_signal(kind=SignalKind.EXIT), snapshot).allowed

    def test_an_instrument_the_snapshot_knows_nothing_about_reads_as_stale(self) -> None:
        snapshot = healthy(markets={})
        assert not StaleDataGuard().evaluate(make_signal(), snapshot).allowed
        assert InstrumentMarket().stale is True


class TestBrokerHealthGuard:
    def test_a_healthy_session_and_feed_are_allowed(self) -> None:
        assert BrokerHealthGuard().evaluate(make_signal(), healthy()).allowed

    def test_an_unhealthy_broker_session_blocks(self) -> None:
        snapshot = healthy(system=healthy_system(broker_session_ok=False))
        verdict = BrokerHealthGuard().evaluate(make_signal(), snapshot)
        assert not verdict.allowed and "session" in verdict.reason

    def test_an_unhealthy_order_feed_blocks(self) -> None:
        snapshot = healthy(system=healthy_system(order_feed_ok=False))
        verdict = BrokerHealthGuard().evaluate(make_signal(), snapshot)
        assert not verdict.allowed and "order-update feed" in verdict.reason


class TestReconciliationGuard:
    def test_a_clean_reconciliation_allows(self) -> None:
        assert ReconciliationGuard().evaluate(make_signal(), healthy()).allowed

    @pytest.mark.parametrize("status", [ReconciliationStatus.PENDING, ReconciliationStatus.FAILED])
    def test_anything_but_clean_blocks(self, status: ReconciliationStatus) -> None:
        snapshot = healthy(system=healthy_system(reconciliation=status))
        verdict = ReconciliationGuard().evaluate(make_signal(), snapshot)
        assert not verdict.allowed and verdict.details["status"] == status.value
