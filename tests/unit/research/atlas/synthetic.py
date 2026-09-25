"""Hand-built markets for the atlas tests: bars from chosen returns, no data files."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from emporos.research.atlas.panel import SLOTS, InstrumentPanel

SESSIONS = 300
FIRST = date(2018, 1, 1)


def sessions(n: int = SESSIONS) -> tuple[date, ...]:
    days: list[date] = []
    d = FIRST
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return tuple(days)


def panel_from(bar_returns: np.ndarray, gaps: np.ndarray, start: float = 100.0) -> InstrumentPanel:
    """Bars whose returns are given: bar 0 is measured from the session's open, which is the
    previous close times (1 + gap)."""
    n = len(gaps)
    close = np.zeros((n, SLOTS))
    open0 = np.zeros(n)
    previous = start
    for d in range(n):
        open0[d] = previous * (1 + gaps[d])
        price = open0[d]
        for k in range(SLOTS):
            price *= 1 + bar_returns[d, k]
            close[d, k] = price
        previous = close[d, -1]
    return InstrumentPanel(close, open0, close[:, -1].copy())


def market_and_stock(
    seed: int = 1, n: int = SESSIONS, beta: float = 1.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(market bar returns, market gaps, stock idiosyncratic bar returns, stock idio gaps)."""
    rng = np.random.default_rng(seed)
    market = rng.normal(0, 0.0003, (n, SLOTS))
    gaps = rng.normal(0, 0.002, n)
    idio = rng.normal(0, 0.0004, (n, SLOTS))
    idio_gaps = rng.normal(0, 0.002, n)
    return market, gaps, idio, idio_gaps


def stock_panel(
    market: np.ndarray, gaps: np.ndarray, idio: np.ndarray, idio_gaps: np.ndarray, beta: float = 1.0
) -> InstrumentPanel:
    return panel_from(beta * market + idio, beta * gaps + idio_gaps)
