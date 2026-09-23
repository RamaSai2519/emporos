"""EM-178: `ForwardReturnCalculator`."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from tests.unit.research.conftest import bars

from emporos.domain.candles import Timeframe
from emporos.research.horizons import ForwardReturnCalculator


def test_standard_horizons_resolve_to_whole_numbers_of_5m_bars() -> None:
    calculator = ForwardReturnCalculator(Timeframe.M5)

    labels_and_bars = {h.label: h.bars for h in calculator.horizons}

    assert labels_and_bars == {"5m": 1, "15m": 3, "30m": 6, "60m": 12}


def test_a_horizon_that_does_not_divide_evenly_is_refused() -> None:
    with pytest.raises(ValueError, match="whole number"):
        ForwardReturnCalculator(Timeframe.M15, horizons=(timedelta(minutes=20),))


def test_returns_at_computes_the_simple_forward_return() -> None:
    series = bars([100, 110, 121])
    calculator = ForwardReturnCalculator(Timeframe.M5, horizons=(timedelta(minutes=5),))
    (horizon,) = calculator.horizons

    returns = calculator.returns_at(series, 0)

    assert returns[horizon] == Decimal("0.1")


def test_returns_at_is_none_past_the_end_of_the_series() -> None:
    series = bars([100, 110])
    calculator = ForwardReturnCalculator(Timeframe.M5, horizons=(timedelta(minutes=15),))
    (horizon,) = calculator.horizons

    returns = calculator.returns_at(series, 1)

    assert returns[horizon] is None


def test_returns_at_is_none_when_the_origin_price_is_zero() -> None:
    series = bars([0, 10])
    calculator = ForwardReturnCalculator(Timeframe.M5, horizons=(timedelta(minutes=5),))
    (horizon,) = calculator.horizons

    assert calculator.returns_at(series, 0)[horizon] is None
