"""Market, sector and group out of a move (EM-243, plan §2).

A series' daily return is `beta . regressors + residual`. The betas for a day come from the 120
sessions BEFORE it (ordinary least squares on the sessions where every input printed, at least 60
of them), so the residual of a day is one nobody could have fitted with the day in hand. The
residual's scale is the standard deviation of the previous 60 residuals (at least 40). The same
betas turn the day's 5-minute returns into a residual PATH (the overnight gap's residual first, then
each bar's), and the 15-minute residual scale is that of the previous 60 sessions' windows."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from emporos.research.atlas.arrays import Floats
from emporos.research.atlas.panel import SLOTS
from emporos.research.atlas.returns import Returns

__all__ = ["Decomposition", "Decomposer", "MoveRules"]

WINDOW_BARS = 3  # a 15-minute window


@dataclass(frozen=True)
class MoveRules:
    beta_window: int = 120
    beta_min: int = 60
    sigma_window: int = 60
    sigma_min: int = 40
    ridge: float = 1e-10


@dataclass(frozen=True)
class Decomposition:
    betas: Floats  # (S, p)
    resid: Floats  # (S,) the day's residual return
    sigma: Floats  # (S,) scale of the previous residuals
    gap_resid: Floats  # (S,)
    path: Floats  # (S, SLOTS) cumulative residual: the gap's, then each bar's
    window: Floats  # (S, SLOTS - 2) 15-minute residual windows, indexed by their first bar
    sigma15: Floats  # (S,)


class Decomposer:
    def __init__(self, rules: MoveRules | None = None) -> None:
        self._rules = rules or MoveRules()

    def decompose(self, own: Returns, regressors: list[Returns]) -> Decomposition:
        size = len(own.daily)
        x = np.stack([r.daily for r in regressors], axis=1) if regressors else np.empty((size, 0))
        betas = self._betas(own.daily, x)
        resid = own.daily - (betas * x).sum(axis=1)  # NaN in a beta or an input: no residual
        sigma = self._trailing_std(resid)
        gap_resid = own.gap - self._explained(own.gap, betas, [r.gap for r in regressors])
        eps = own.bars - self._explained(own.bars, betas, [r.bars for r in regressors])
        return Decomposition(
            betas, resid, sigma, gap_resid, self._path(gap_resid, eps), *self._windows(eps)
        )

    @staticmethod
    def _explained(like: Floats, betas: Floats, series: list[Floats]) -> Floats:
        total = np.zeros_like(like)
        for j, values in enumerate(series):
            total += (betas[:, j] if values.ndim == 1 else betas[:, j, None]) * values
        return total

    def _betas(self, y: Floats, x: Floats) -> Floats:
        size, p = x.shape
        betas = np.full((size, p), np.nan)
        if p == 0:
            return betas
        rules = self._rules
        ok = ~np.isnan(y) & ~np.isnan(x).any(axis=1)
        for d in range(size):
            lo = max(0, d - rules.beta_window)
            rows = np.flatnonzero(ok[lo:d]) + lo
            if len(rows) < rules.beta_min:
                continue
            xs, ys = x[rows], y[rows]
            gram = xs.T @ xs + rules.ridge * np.eye(p) * len(rows)
            betas[d] = np.linalg.solve(gram, xs.T @ ys)
        return betas

    def _trailing_std(self, values: Floats) -> Floats:
        rules = self._rules
        out = np.full(len(values), np.nan)
        for d in range(len(values)):
            past = values[max(0, d - rules.sigma_window) : d]
            past = past[~np.isnan(past)]
            if len(past) >= rules.sigma_min:
                out[d] = float(np.std(past, ddof=1))
        return out

    @staticmethod
    def _path(gap_resid: Floats, eps: Floats) -> Floats:
        valid = (~np.isnan(eps)).sum(axis=1) >= SLOTS - 15
        path = gap_resid[:, None] + np.cumsum(np.nan_to_num(eps), axis=1)
        path[~valid] = np.nan
        path[np.isnan(gap_resid)] = np.nan
        return path

    def _windows(self, eps: Floats) -> tuple[Floats, Floats]:
        span = SLOTS - WINDOW_BARS + 1
        window = sum(eps[:, k : k + span] for k in range(WINDOW_BARS))
        assert not isinstance(window, int)
        valid = ~np.isnan(window)
        filled = np.where(valid, window, 0.0)
        total, squares, count = filled.sum(axis=1), (filled**2).sum(axis=1), valid.sum(axis=1)
        rules = self._rules
        sigma15 = np.full(len(eps), np.nan)
        for d in range(len(eps)):
            lo = max(0, d - rules.sigma_window)
            n = count[lo:d].sum()
            if d - lo >= rules.sigma_min and n > 0:
                mean = total[lo:d].sum() / n
                sigma15[d] = float(np.sqrt(max(squares[lo:d].sum() / n - mean**2, 0.0)))
        return window, sigma15
