"""EM-228/229: real gaps are traded through, and the report says what they did."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.unit.research.swing.support import (
    ArtifactSet,
    dataset,
    free_costs,
    hold,
    series,
    sessions,
)

from emporos.research.discontinuities import DiscontinuityStatus, Finding
from emporos.research.gap_classes import GapClass, GapVerdict
from emporos.research.swing.exposure import real_gap_exposure
from emporos.research.swing.simulator import SwingConfig, SwingSimulator

X = "NSE:1"
DAYS = sessions(8)
# bought at the DAYS[1] open at 100; a REAL -20% gap into DAYS[4]
PRICES = [("100", "100"), ("100", "100"), ("100", "100"), ("100", "100"), ("80", "82"),
          ("82", "82"), ("82", "82"), ("82", "82")]  # fmt: skip


def real(day: date, ratio: str) -> GapVerdict:
    finding = Finding(X, day, DAYS[DAYS.index(day) - 1], Decimal(ratio), Decimal(ratio),
                      DiscontinuityStatus.UNEXPLAINED, None)  # fmt: skip
    return GapVerdict(finding, GapClass.REAL, "a large single-name move (-20.0%)", None)


def run(strategy_until: int = 6, artifacts: ArtifactSet | None = None):  # type: ignore[no-untyped-def]
    data = dataset(series(X, DAYS, PRICES, artifacts=artifacts))
    return data, SwingSimulator(
        data, hold(X, DAYS[0], DAYS[strategy_until]), SwingConfig(Decimal(10000), 1), free_costs()
    ).run()


def test_a_real_gap_is_in_the_pnl_and_the_exposure_lists_it() -> None:
    data, result = run()

    exposure = real_gap_exposure(result, data, [real(DAYS[4], "0.8")])

    (e,) = exposure
    assert (e.instrument_id, e.day, e.gap) == (X, DAYS[4], Decimal("-0.2"))
    assert e.pnl == Decimal(-2000)  # 100 shares x (80 - 100)
    assert result.equity[4] < result.equity[3]  # the loss is really in the equity curve


def test_a_position_that_was_not_held_into_the_gap_is_not_listed() -> None:
    data = dataset(series(X, DAYS, PRICES))
    result = SwingSimulator(
        data, hold(X, DAYS[0], DAYS[2]), SwingConfig(Decimal(10000), 1), free_costs()
    ).run()  # sold at the DAYS[3] open, before the gap

    assert real_gap_exposure(result, data, [real(DAYS[4], "0.8")]) == ()


def test_a_sale_at_the_open_of_the_gap_session_takes_the_gap() -> None:
    data = dataset(series(X, DAYS, PRICES))
    result = SwingSimulator(
        data, hold(X, DAYS[0], DAYS[3]), SwingConfig(Decimal(10000), 1), free_costs()
    ).run()  # decided at the DAYS[3]... wants nothing at DAYS[3] close -> sold at the DAYS[4] open

    (e,) = real_gap_exposure(result, data, [real(DAYS[4], "0.8")])

    assert e.pnl == Decimal(-2000)
    assert result.trades[0].exit_price == Decimal(80)


def test_a_gap_on_another_name_is_ignored() -> None:
    data, result = run()
    other = real(DAYS[4], "0.8")
    other = GapVerdict(
        Finding("NSE:9", other.finding.day, other.finding.previous_day, other.finding.raw_ratio,
                Decimal(1), DiscontinuityStatus.UNEXPLAINED, None),
        GapClass.REAL, "x", None,
    )  # fmt: skip

    assert real_gap_exposure(result, data, [other]) == ()


def test_an_artifact_gap_is_flattened_so_it_never_reaches_the_pnl() -> None:
    data, result = run(artifacts=ArtifactSet({X: [DAYS[4]]}))

    # the flattened series has no gap into DAYS[4]: only the day's own move (80 -> 82, +2.5%) counts
    assert result.equity[3] == Decimal(10000)
    assert result.equity[4] == Decimal(10250)
