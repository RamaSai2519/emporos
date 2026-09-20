"""What walk-forward tunes, how a candidate is scored, and how one is chosen.

The selector is handed `TrainingScore`s — a candidate and its score on the TRAINING window — and
nothing else: no result object, no test-window figure, no way to ask for one. That is how "parameter
selection sees only the training window" is a fact about types and not a promise.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import ValidationError

from emporos.backtest.engine import BacktestResult
from emporos.strategies.config import ResolvedStrategyConfig


class ParameterError(ValueError):
    """A candidate names a parameter the strategy does not have, or gives it an invalid value."""


class NoViableCandidateError(RuntimeError):
    """Every candidate scored nothing on the training window (no trades, no variance...)."""


@dataclass(frozen=True)
class ParameterCandidate:
    """A named set of overrides for the strategy's `parameters`. Values are ints or strings (a
    decimal is a quoted string), exactly as in the YAML."""

    name: str
    overrides: Mapping[str, int | str]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a candidate needs a name")


class ConfigVariants:
    """A strategy config with a candidate's overrides applied and re-validated by the strategy's
    own parameter model, so a misspelt or out-of-range parameter fails here, loudly."""

    def apply(
        self, config: ResolvedStrategyConfig, candidate: ParameterCandidate
    ) -> ResolvedStrategyConfig:
        current: dict[str, Any] = config.parameters.model_dump()
        unknown = set(candidate.overrides) - set(current)
        if unknown:
            raise ParameterError(f"{candidate.name}: unknown parameter(s) {sorted(unknown)}")
        try:
            parameters = type(config.parameters).model_validate(current | dict(candidate.overrides))
        except ValidationError as error:
            raise ParameterError(f"{candidate.name}: {error.errors()[0]['msg']}") from error
        return config.model_copy(update={"parameters": parameters})


class Objective(Protocol):
    name: str

    def score(self, result: BacktestResult) -> Decimal | None:
        """A number to maximise, or None when the run cannot be scored."""
        ...


class MetricObjective:
    def __init__(self, name: str, extract: Callable[[BacktestResult], Decimal | None]) -> None:
        self.name = name
        self._extract = extract

    def score(self, result: BacktestResult) -> Decimal | None:
        return self._extract(result)


SHARPE = MetricObjective("sharpe", lambda r: r.metrics.returns.sharpe)
TOTAL_RETURN = MetricObjective("total_return", lambda r: r.metrics.returns.total_return)
NET_PNL = MetricObjective("net_pnl", lambda r: r.metrics.trades.net_pnl.amount)


@dataclass(frozen=True)
class TrainingScore:
    candidate: ParameterCandidate
    score: Decimal | None


class ParameterSelector(Protocol):
    def select(self, scores: Sequence[TrainingScore]) -> ParameterCandidate: ...


class BestScoreSelector:
    """The highest training score; ties go to the earlier candidate; unscored candidates lose."""

    def select(self, scores: Sequence[TrainingScore]) -> ParameterCandidate:
        scored = [s for s in scores if s.score is not None]
        if not scored:
            raise NoViableCandidateError("no candidate could be scored on the training window")
        best = scored[0]
        for challenger in scored[1:]:
            assert challenger.score is not None and best.score is not None
            if challenger.score > best.score:
                best = challenger
        return best.candidate
