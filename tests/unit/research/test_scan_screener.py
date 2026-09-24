"""EM-191 F3b: a screen is advisory unless its scan is one the parity test vouches for."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from tests.unit.research.test_scan_base import bar
from tests.unit.research.test_screen import FixedClock, evaluator

from emporos.domain.candles import Candle
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.research.scans.proven import PARITY_PROVEN, is_parity_proven
from emporos.research.scans.scan_screener import ScanScreener
from emporos.research.screen_ledger import InMemoryScreenLedger, ScreenIdentity
from emporos.research.screen_trades import ScreenTrade


class CannedScan:
    """A `SignalScan` that trades one fixed round trip per instrument it is given."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.asked: list[str] = []

    def scan(self, instrument_id: str, bars: Sequence[Candle]) -> list[ScreenTrade]:
        self.asked.append(instrument_id)
        return [
            ScreenTrade(
                instrument_id, date(2026, 9, 21), OrderSide.BUY, Money.of(1000), Money.of(1010)
            )
        ]


def identity() -> ScreenIdentity:
    return ScreenIdentity(
        "canned", {"k": "1"}, "two names", date(2026, 9, 21), date(2026, 9, 21), Decimal(25_000)
    )


def test_a_proven_scan_yields_a_result_that_is_not_advisory() -> None:
    scan = CannedScan("orb_v1")

    result = ScanScreener(scan, evaluator(), InMemoryScreenLedger(), FixedClock()).screen(
        identity(), {"NSE:2": [bar(0, "1000")], "NSE:1": [bar(0, "1000")]}
    )

    assert is_parity_proven("orb_v1") and not result.advisory
    assert scan.asked == ["NSE:1", "NSE:2"]  # in a fixed order, so a screen is reproducible
    assert result.trades == 2


def test_an_unproven_scan_stays_advisory_however_good_it_looks() -> None:
    result = ScanScreener(
        CannedScan("brand_new_idea"), evaluator(), InMemoryScreenLedger(), FixedClock()
    ).screen(identity(), {"NSE:1": [bar(0, "1000")]})

    assert not is_parity_proven("brand_new_idea") and result.advisory


def test_the_screen_is_counted_in_the_ledger_once() -> None:
    ledger = InMemoryScreenLedger()
    screener = ScanScreener(CannedScan("orb_v1"), evaluator(), ledger, FixedClock())

    screener.screen(identity(), {"NSE:1": [bar(0, "1000")]})
    screener.screen(identity(), {"NSE:1": [bar(0, "1000")]})

    assert ledger.count() == 1


def test_the_registry_holds_the_three_strategies_the_plan_names() -> None:
    assert set(PARITY_PROVEN) == {"orb_v1", "vwap_reversion_v1", "rsi_pullback_v1"}
