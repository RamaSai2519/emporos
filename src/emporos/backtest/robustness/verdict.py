"""Validated, inconclusive or rejected: the classification of a strategy, with the evidence.

Each `Gate` looks at the evidence and answers PASS, FAIL or UNKNOWN. The rule is deliberately
asymmetric, because a strategy that is not proven is not the same as one that is disproven:

* any FAIL              -> REJECTED     (the evidence shows it loses, or breaks a limit)
* else any UNKNOWN      -> INCONCLUSIVE (the evidence is too thin, or luck is not ruled out)
* every gate PASSES     -> VALIDATED

A gate answers FAIL only when its evidence is strong enough to be believed, and UNKNOWN when it is
not. Thin data can therefore keep a strategy from being validated but can never, by itself, make
one look rejected or validated. Thresholds come from the benchmark file; gates are separate small
classes, so a new check is a new class, not an edit to an existing one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from emporos.backtest.robustness.benchmark import VerdictThresholds
from emporos.backtest.robustness.concentration import ConcentrationReport
from emporos.backtest.robustness.deflated_sharpe import DeflatedSharpeReport
from emporos.backtest.robustness.monte_carlo import MonteCarloReport
from emporos.backtest.robustness.pbo import PBOReport
from emporos.backtest.robustness.perturbation import PerturbationReport
from emporos.domain.experiments import Verdict

_ZERO = Decimal(0)


class GateOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GateResult:
    name: str
    outcome: GateOutcome
    detail: str


@dataclass(frozen=True)
class DirectionStats:
    """The long side and the short side apart, so a verdict never hides that one direction carried
    the strategy: whichever side has no trades has a zero count and net."""

    long_count: int
    long_net_pnl: Decimal
    short_count: int
    short_net_pnl: Decimal


@dataclass(frozen=True)
class Evidence:
    """Everything the gates may look at, gathered from out-of-sample results only."""

    trade_count: int
    net_pnl: Decimal
    adverse_net_pnl: Decimal
    history_days: int
    window_nets: tuple[Decimal, ...]
    worst_window_drawdown: Decimal
    monte_carlo: MonteCarloReport
    deflated_sharpe: DeflatedSharpeReport
    pbo: PBOReport
    observed_edge_bps: Decimal | None  # EM-183: mean gross P&L / entry notional, out-of-sample
    minimum_edge_bps: Decimal | None  # EM-183: mean modeled cost floor at each trade's own size
    concentration: ConcentrationReport
    perturbation: PerturbationReport | None
    baseline_net_pnl: Decimal | None  # the simple always-long baseline; None when not measured
    direction: DirectionStats = field(default_factory=lambda: DirectionStats(0, _ZERO, 0, _ZERO))
    regimes_covered: frozenset[str] = frozenset()  # distinct regimes traded across all windows
    # EM-184: set from the strategy's own StrategyMetadata.supported_regimes (non-empty = the
    # strategy declared, before any result was seen, that it only trades certain regimes) —
    # exempts RegimeDiversity, which would otherwise fault a strategy for doing exactly what it
    # was built to do.
    regime_specific: bool = False


class Gate(Protocol):
    name: str

    def assess(self, evidence: Evidence) -> GateResult: ...


class _GateBase:
    name: str

    def _result(self, outcome: GateOutcome, detail: str) -> GateResult:
        return GateResult(self.name, outcome, detail)


class ProfitAfterCosts(_GateBase):
    name = "profit after costs is real"

    def __init__(self, t: VerdictThresholds) -> None:
        self._t = t

    def assess(self, e: Evidence) -> GateResult:
        distribution = e.monte_carlo.distribution
        if distribution is None:
            return self._result(
                GateOutcome.UNKNOWN, f"net {e.net_pnl:.2f}; {e.monte_carlo.inconclusive_reason}"
            )
        p = distribution.probability_net_positive
        low, high = distribution.net_pnl.low, distribution.net_pnl.high
        detail = (
            f"net {e.net_pnl:.2f}, {self._t.confidence:.0%} interval {low:.2f} to {high:.2f}, "
            f"P(net > 0) {p:.3f}"
        )
        if p >= self._t.min_probability_net_positive and e.net_pnl > _ZERO:
            return self._result(GateOutcome.PASS, detail)
        if p <= self._t.max_probability_net_positive_to_reject:
            return self._result(GateOutcome.FAIL, detail)
        return self._result(GateOutcome.UNKNOWN, detail)


class SurvivesAdverseCosts(_GateBase):
    name = "profit survives adverse costs"

    def assess(self, e: Evidence) -> GateResult:
        detail = f"net {e.net_pnl:.2f} as run, {e.adverse_net_pnl:.2f} under adverse costs"
        if e.adverse_net_pnl > _ZERO:
            return self._result(GateOutcome.PASS, detail)
        if e.net_pnl > _ZERO:
            return self._result(GateOutcome.FAIL, detail)
        return self._result(GateOutcome.UNKNOWN, detail)


class WalkForwardWindows(_GateBase):
    name = "profitable in most walk-forward windows"

    def __init__(self, t: VerdictThresholds) -> None:
        self._t = t

    def assess(self, e: Evidence) -> GateResult:
        windows = len(e.window_nets)
        positive = sum(1 for n in e.window_nets if n > _ZERO)
        detail = f"{positive} of {windows} windows"
        if windows < self._t.min_windows:
            return self._result(
                GateOutcome.UNKNOWN, f"{detail}; fewer than {self._t.min_windows} windows"
            )
        share = Decimal(positive) / windows
        return self._result(
            GateOutcome.PASS if share >= self._t.min_positive_window_share else GateOutcome.FAIL,
            detail,
        )


class EnoughTrades(_GateBase):
    name = "enough trades"

    def __init__(self, t: VerdictThresholds) -> None:
        self._t = t

    def assess(self, e: Evidence) -> GateResult:
        detail = f"{e.trade_count} trades, {self._t.min_trades_to_validate} needed to validate"
        ok = e.trade_count >= self._t.min_trades_to_validate
        return self._result(GateOutcome.PASS if ok else GateOutcome.UNKNOWN, detail)


class EnoughHistory(_GateBase):
    name = "enough history"

    def __init__(self, t: VerdictThresholds) -> None:
        self._t = t

    def assess(self, e: Evidence) -> GateResult:
        detail = f"{e.history_days} trading days, {self._t.min_history_days} needed"
        ok = e.history_days >= self._t.min_history_days
        return self._result(GateOutcome.PASS if ok else GateOutcome.UNKNOWN, detail)


class DrawdownWithinBudget(_GateBase):
    name = "drawdown within the risk budget"

    def __init__(self, t: VerdictThresholds) -> None:
        self._t = t

    def assess(self, e: Evidence) -> GateResult:
        detail = (
            f"worst window {e.worst_window_drawdown:.1%}, budget {self._t.max_window_drawdown:.1%}"
        )
        ok = e.worst_window_drawdown <= self._t.max_window_drawdown
        return self._result(GateOutcome.PASS if ok else GateOutcome.FAIL, detail)


class NotConcentrated(_GateBase):
    name = "profit is not concentrated"

    def __init__(self, t: VerdictThresholds) -> None:
        self._limits = t.concentration

    def assess(self, e: Evidence) -> GateResult:
        c, limits = e.concentration, self._limits
        if (
            c.top_instrument_share is None
            or c.top_month_share is None
            or c.top_trades_share is None
        ):
            return self._result(GateOutcome.UNKNOWN, "no profit to attribute")
        detail = (
            f"{c.top_instrument} {c.top_instrument_share:.0%} of profit, month {c.top_month} "
            f"{c.top_month_share:.0%}, best {c.top_trades} trades {c.top_trades_share:.0%}"
        )
        concentrated = (
            c.top_instrument_share > limits.max_top_instrument_share
            or c.top_month_share > limits.max_top_month_share
            or c.top_trades_share > limits.max_top_trades_share
        )
        return self._result(GateOutcome.FAIL if concentrated else GateOutcome.PASS, detail)


class ParameterStability(_GateBase):
    name = "survives parameter changes"

    def __init__(self, t: VerdictThresholds) -> None:
        self._min = t.perturbation.min_profitable_neighbour_share

    def assess(self, e: Evidence) -> GateResult:
        share = None if e.perturbation is None else e.perturbation.profitable_share
        if share is None:
            return self._result(GateOutcome.UNKNOWN, "neighbouring parameters were not tested")
        detail = f"{share:.0%} of neighbouring parameter sets profit, {self._min:.0%} needed"
        return self._result(GateOutcome.PASS if share >= self._min else GateOutcome.FAIL, detail)


class BeatsLuck(_GateBase):
    name = "beats the luck of the search (Deflated Sharpe)"

    def __init__(self, t: VerdictThresholds) -> None:
        self._min = t.min_deflated_sharpe

    def assess(self, e: Evidence) -> GateResult:
        dsr = e.deflated_sharpe
        if dsr.deflated_sharpe is None:
            return self._result(GateOutcome.UNKNOWN, dsr.reason or "not computed")
        detail = (
            f"DSR {dsr.deflated_sharpe:.3f} over {dsr.trial_count} trials, {self._min:.2f} needed"
        )
        return self._result(
            GateOutcome.PASS if dsr.deflated_sharpe >= self._min else GateOutcome.UNKNOWN, detail
        )


class BeatsOverfitting(_GateBase):
    """EM-182: unlike `BeatsLuck`, missing PBO evidence and STRONG PBO evidence are told apart —
    the acceptance criteria ask for one or the other, not always the softer of the two. No CSCV
    result yet is UNKNOWN (thin data, not disproof); a computed probability of overfitting above
    the configured limit is FAIL (the search's in-sample winner is shown, not merely unproven, to
    tend toward an out-of-sample loser)."""

    name = "does not show CSCV overfitting evidence (PBO)"

    def __init__(self, t: VerdictThresholds) -> None:
        self._max = t.max_pbo

    def assess(self, e: Evidence) -> GateResult:
        pbo = e.pbo
        if pbo.probability_of_overfitting is None:
            return self._result(GateOutcome.UNKNOWN, pbo.reason or "not computed")
        detail = (
            f"PBO {pbo.probability_of_overfitting:.3f} over {pbo.candidate_count} candidates x "
            f"{pbo.block_count} windows ({pbo.combination_count} combinations), "
            f"{self._max:.2f} allowed"
        )
        passed = pbo.probability_of_overfitting <= self._max
        return self._result(GateOutcome.PASS if passed else GateOutcome.FAIL, detail)


class SurvivesCostError(_GateBase):
    """EM-183: the observed average gross edge must clear the modeled minimum (brokerage,
    statutory charges, spread and slippage, at each trade's own quantity and price) by the
    configured safety margin — a strategy whose apparent edge is only a whisker above its own
    cost floor is too small to trust the cost MODEL's own accuracy, let alone the strategy."""

    name = "edge survives plausible cost-model error"

    def __init__(self, t: VerdictThresholds) -> None:
        self._margin = t.min_edge_safety_margin

    def assess(self, e: Evidence) -> GateResult:
        if e.observed_edge_bps is None or e.minimum_edge_bps is None:
            return self._result(GateOutcome.UNKNOWN, "no portfolio cost model was configured")
        required = e.minimum_edge_bps * self._margin
        detail = (
            f"observed {e.observed_edge_bps:.2f} bps/trade, needs {required:.2f} bps "
            f"({self._margin}x the modeled {e.minimum_edge_bps:.2f} bps minimum)"
        )
        outcome = GateOutcome.PASS if e.observed_edge_bps >= required else GateOutcome.FAIL
        return self._result(outcome, detail)


class BeatsBaseline(_GateBase):
    name = "beats the always-long baseline"

    def assess(self, e: Evidence) -> GateResult:
        if e.baseline_net_pnl is None:
            return self._result(GateOutcome.UNKNOWN, "baseline not measured")
        detail = f"strategy net {e.net_pnl:.2f}, baseline {e.baseline_net_pnl:.2f}"
        if e.net_pnl > e.baseline_net_pnl:
            return self._result(GateOutcome.PASS, detail)
        thin = not e.monte_carlo.conclusive
        return self._result(GateOutcome.UNKNOWN if thin else GateOutcome.FAIL, detail)


class RegimeDiversity(_GateBase):
    """EM-166/EM-184: a strategy that only ever traded in one market regime has not shown a
    repeatable edge, only a fit to that regime's conditions — this is what keeps a strategy from
    graduating on evidence from a single lucky stretch. Exempted when the strategy was declared
    `regime_specific` (`StrategyMetadata.supported_regimes`, set before any result was seen) — it
    is not overfitting to trade only the regime it was explicitly built for. `min_regimes <= 1` is
    still honoured as a benchmark file's own explicit, visible opt-out (there is no default to
    fall back on any more — EM-184 removed it), distinct from a strategy's own exemption."""

    name = "evidence spans enough distinct regimes"

    def __init__(self, t: VerdictThresholds) -> None:
        self._min = t.min_regimes

    def assess(self, e: Evidence) -> GateResult:
        if e.regime_specific:
            return self._result(GateOutcome.PASS, "strategy is declared regime-specific")
        count = len(e.regimes_covered)
        if self._min <= 1:
            return self._result(GateOutcome.PASS, f"{count} regime(s); diversity not required")
        detail = (
            f"{count} distinct regime(s) traded ({sorted(e.regimes_covered)}), {self._min} needed"
        )
        if count == 0:
            return self._result(GateOutcome.UNKNOWN, "no regime data available")
        return self._result(GateOutcome.PASS if count >= self._min else GateOutcome.FAIL, detail)


@dataclass(frozen=True)
class VerdictReport:
    verdict: Verdict
    gates: tuple[GateResult, ...]

    def of(self, outcome: GateOutcome) -> tuple[GateResult, ...]:
        return tuple(g for g in self.gates if g.outcome is outcome)


class VerdictPolicy:
    def __init__(self, gates: Sequence[Gate]) -> None:
        if not gates:
            raise ValueError("a verdict needs at least one gate")
        self._gates = tuple(gates)

    @classmethod
    def standard(cls, thresholds: VerdictThresholds) -> VerdictPolicy:
        return cls(
            [
                ProfitAfterCosts(thresholds),
                SurvivesAdverseCosts(),
                WalkForwardWindows(thresholds),
                EnoughTrades(thresholds),
                EnoughHistory(thresholds),
                DrawdownWithinBudget(thresholds),
                NotConcentrated(thresholds),
                ParameterStability(thresholds),
                BeatsLuck(thresholds),
                BeatsOverfitting(thresholds),
                SurvivesCostError(thresholds),
                BeatsBaseline(),
                RegimeDiversity(thresholds),
            ]
        )

    def classify(self, evidence: Evidence) -> VerdictReport:
        results = tuple(gate.assess(evidence) for gate in self._gates)
        outcomes = {r.outcome for r in results}
        if GateOutcome.FAIL in outcomes:
            verdict = Verdict.REJECTED
        elif GateOutcome.UNKNOWN in outcomes:
            verdict = Verdict.INCONCLUSIVE
        else:
            verdict = Verdict.VALIDATED
        return VerdictReport(verdict, results)
