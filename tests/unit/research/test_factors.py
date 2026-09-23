"""EM-179: cross-sectional factor construction (`AlignedUniverse`, `MarketFactor`,
`SectorFactor`, `BetaEstimator`, `ResidualSignalCalculator`)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.unit.research.conftest import bar, bars

from emporos.research.factors import (
    AlignedUniverse,
    BetaEstimator,
    MarketFactor,
    ResidualSignalCalculator,
    SectorFactor,
    bar_returns,
    compounded_return,
)
from emporos.research.horizons import Horizon

D = Decimal
MARKET: list[Decimal | None] = [
    None, D("0.01"), D("0.02"), D("0.01"), D("0.02"), D("0.01"), D("0.02"),
]  # fmt: skip
SECTOR: list[Decimal | None] = [
    None, D("0.02"), D("0.01"), D("0.03"), D("0.01"), D("0.02"), D("0.03"),
]  # fmt: skip


def test_aligned_universe_needs_at_least_one_instrument() -> None:
    with pytest.raises(ValueError, match="at least one instrument"):
        AlignedUniverse({})


def test_aligned_universe_rejects_mismatched_bar_counts() -> None:
    with pytest.raises(ValueError, match="same number of bars"):
        AlignedUniverse({
            "NSE:1": bars([10, 11, 12], instrument_id="NSE:1"),
            "NSE:2": bars([10, 11], instrument_id="NSE:2"),
        })  # fmt: skip


def test_aligned_universe_rejects_misaligned_timestamps() -> None:
    off_by_one = [bar(i + 1, 10, instrument_id="NSE:2") for i in range(3)]
    with pytest.raises(ValueError, match="not position-aligned"):
        AlignedUniverse({"NSE:1": bars([10, 11, 12], instrument_id="NSE:1"), "NSE:2": off_by_one})


def test_aligned_universe_exposes_instruments_length_and_bars() -> None:
    universe = AlignedUniverse({
        "NSE:2": bars([10, 11, 12], instrument_id="NSE:2"),
        "NSE:1": bars([20, 21, 22], instrument_id="NSE:1"),
    })  # fmt: skip

    assert universe.instrument_ids == ("NSE:1", "NSE:2")
    assert universe.length == 3
    assert [c.close.amount for c in universe.bars_of("NSE:1")] == [D(20), D(21), D(22)]


def test_bar_returns_is_none_at_index_zero_and_after_a_zero_close() -> None:
    series = bars([0, 10, 15])

    returns = bar_returns(series)

    assert returns[0] is None
    assert returns[1] is None  # previous close was zero
    assert returns[2] == D("0.5")


def test_compounded_return_compounds_across_the_span() -> None:
    returns: list[Decimal | None] = [None, D("0.10"), D("0.10")]

    # two consecutive +10% periods compound to 21%, not 20%
    assert compounded_return(returns, 0, 2) == D("0.21")


def test_compounded_return_is_none_outside_the_series_or_across_a_gap() -> None:
    returns: list[Decimal | None] = [None, D("0.10"), None, D("0.10")]

    assert compounded_return(returns, -1, 1) is None
    assert compounded_return(returns, 0, 10) is None
    assert compounded_return(returns, 0, 3) is None  # index 2 is undefined


def test_market_factor_is_the_equal_weighted_average_return() -> None:
    universe = AlignedUniverse({
        "NSE:1": bars([100, 110, 121], instrument_id="NSE:1"),  # +10%, +10%
        "NSE:2": bars([100, 90, 90], instrument_id="NSE:2"),  # -10%, 0%
    })  # fmt: skip

    market = MarketFactor(universe)

    assert market.returns[0] is None
    assert market.returns[1] == D("0.00")  # mean(+0.10, -0.10)
    assert market.returns[2] == D("0.05")  # mean(+0.10, 0.00)


def test_sector_factor_groups_only_classified_instruments() -> None:
    class TwoSectors:
        def sector_of(self, instrument_id: str) -> str | None:
            return {"NSE:1": "IT", "NSE:2": "IT"}.get(instrument_id)

    universe = AlignedUniverse({
        "NSE:1": bars([100, 110], instrument_id="NSE:1"),
        "NSE:2": bars([100, 120], instrument_id="NSE:2"),
        "NSE:3": bars([100, 100], instrument_id="NSE:3"),  # unclassified
    })  # fmt: skip

    sector = SectorFactor(universe, TwoSectors())

    assert sector.returns_for("NSE:1") == [None, D("0.15")]  # mean(+0.10, +0.20)
    assert sector.returns_for("NSE:3") is None


def test_beta_estimator_recovers_an_exact_single_factor_slope() -> None:
    instrument: list[Decimal | None] = [None] + [D(2) * m for m in MARKET[1:]]  # type: ignore[operator]

    loadings = BetaEstimator(window=6).loadings_at(6, instrument, MARKET, None)

    assert loadings == (D(2), D(0))


def test_beta_estimator_recovers_exact_two_factor_loadings() -> None:
    instrument: list[Decimal | None] = (
        [None]
        + [
            D(2) * m + D(3) * s  # type: ignore[operator]
            for m, s in zip(MARKET[1:], SECTOR[1:], strict=True)
        ]
    )

    loadings = BetaEstimator(window=6).loadings_at(6, instrument, MARKET, SECTOR)

    assert loadings == (D(2), D(3))


def test_beta_estimator_needs_a_full_window_of_prior_bars() -> None:
    market: list[Decimal | None] = [None, D("0.01"), D("0.02")]
    instrument: list[Decimal | None] = [None, D("0.02"), D("0.04")]

    assert BetaEstimator(window=6).loadings_at(2, instrument, market, None) is None


def test_beta_estimator_falls_back_to_market_only_with_no_sector_variance() -> None:
    flat_sector: list[Decimal | None] = [None] + [D(0)] * 6
    instrument: list[Decimal | None] = [None] + [D(2) * m for m in MARKET[1:]]  # type: ignore[operator]
    estimator = BetaEstimator(window=6, min_samples=6)

    loadings = estimator.loadings_at(6, instrument, MARKET, flat_sector)

    assert loadings == (D(2), D(0))


def test_residual_signal_is_zero_for_a_one_bar_horizon_fit_exactly_by_its_betas() -> None:
    horizon = Horizon(timedelta(minutes=5), bars=1)
    instrument: list[Decimal | None] = (
        [None]
        + [
            D(2) * m + D(3) * s  # type: ignore[operator]
            for m, s in zip(MARKET[1:], SECTOR[1:], strict=True)
        ]
    )
    calculator = ResidualSignalCalculator(BetaEstimator(window=6))

    signal = calculator.signal_at(6, horizon, instrument, MARKET, SECTOR)

    assert signal == D(0)


def test_residual_signal_is_none_without_enough_trailing_history() -> None:
    horizon = Horizon(timedelta(minutes=30), bars=6)
    market: list[Decimal | None] = [None, D("0.01"), D("0.02")]
    instrument: list[Decimal | None] = [None, D("0.02"), D("0.04")]
    calculator = ResidualSignalCalculator(BetaEstimator(window=6))

    assert calculator.signal_at(2, horizon, instrument, market, None) is None
