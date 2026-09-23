"""Does paper behave like the backtest? PASS, FAIL or UNKNOWN per check, then one verdict.

The same asymmetric rule as `backtest.robustness.verdict`, on purpose:

* any FAIL             -> REJECTED     (paper is shown to degrade beyond a bound)
* else any UNKNOWN     -> INCONCLUSIVE (too thin a sample, or nothing to compare against)
* every check PASSES   -> VALIDATED

A degradation check answers FAIL only when the sample is big enough to believe it; on a thin
sample it answers UNKNOWN, so a few sessions can neither validate nor condemn a strategy. Each
check is its own small class, so a new bound is a new class, not an edit.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from emporos.backtest.robustness.verdict import GateOutcome, GateResult
from emporos.domain.experiments import Verdict
from emporos.parity.config import ParityThresholds
from emporos.parity.metrics import ParityMetric, ParityMetrics

_ZERO = Decimal(0)


class ParityGate(Protocol):
    name: str

    def assess(self, metrics: ParityMetrics) -> GateResult: ...


class _Gate:
    name: str

    def __init__(self, t: ParityThresholds) -> None:
        self._t = t

    def _result(self, outcome: GateOutcome, detail: str) -> GateResult:
        return GateResult(self.name, outcome, detail)

    def _thin(self, m: ParityMetrics) -> str | None:
        if m.sessions < self._t.min_sessions:
            return f"only {m.sessions} session(s), {self._t.min_sessions} needed to judge"
        if m.matched_trades < self._t.min_matched_trades:
            return f"only {m.matched_trades} matched trade(s), {self._t.min_matched_trades} needed"
        return None

    def _judge(self, m: ParityMetrics, breached: bool | None, detail: str) -> GateResult:
        """`breached` None means the numbers needed do not exist on one side or the other."""
        if breached is None:
            return self._result(GateOutcome.UNKNOWN, f"{detail}: nothing to compare")
        thin = self._thin(m)
        if breached:
            if thin is not None:
                return self._result(GateOutcome.UNKNOWN, f"{detail} (past its bound, but {thin})")
            return self._result(GateOutcome.FAIL, detail)
        if thin is not None:
            return self._result(GateOutcome.UNKNOWN, f"{detail} (within bound, but {thin})")
        return self._result(GateOutcome.PASS, detail)


def _fmt(value: Decimal | None, places: str = "0.0001") -> str:
    return "n/a" if value is None else str(value.quantize(Decimal(places)))


class EnoughSessions(_Gate):
    name = "enough paper sessions"

    def assess(self, m: ParityMetrics) -> GateResult:
        detail = f"{m.sessions} session(s), {self._t.min_sessions} needed"
        ok = m.sessions >= self._t.min_sessions
        return self._result(GateOutcome.PASS if ok else GateOutcome.UNKNOWN, detail)


class EnoughMatchedTrades(_Gate):
    name = "enough matched trades"

    def assess(self, m: ParityMetrics) -> GateResult:
        detail = f"{m.matched_trades} matched trade(s), {self._t.min_matched_trades} needed"
        ok = m.matched_trades >= self._t.min_matched_trades
        return self._result(GateOutcome.PASS if ok else GateOutcome.UNKNOWN, detail)


class SignalAgreement(_Gate):
    name = "paper signals as the backtest does"

    def assess(self, m: ParityMetrics) -> GateResult:
        agreement = m.signal_agreement
        detail = f"agreement {_fmt(agreement)}, {self._t.min_signal_agreement} needed"
        breached = None if agreement is None else agreement < self._t.min_signal_agreement
        return self._judge(m, breached, detail)


class FillRateDegradation(_Gate):
    name = "paper fills as often as the backtest"

    def assess(self, m: ParityMetrics) -> GateResult:
        delta = m.delta(ParityMetric.FILL_RATE)
        drop = None if delta.absolute is None else -delta.absolute
        detail = (
            f"fill rate {_fmt(delta.paper)} vs {_fmt(delta.backtest)}, "
            f"drop {_fmt(drop)} (at most {self._t.max_fill_rate_drop})"
        )
        return self._judge(m, None if drop is None else drop > self._t.max_fill_rate_drop, detail)


class SlippageDegradation(_Gate):
    name = "paper slips no more than the backtest"

    def assess(self, m: ParityMetrics) -> GateResult:
        delta = m.delta(ParityMetric.SLIPPAGE_MEAN_BPS)
        rise = delta.absolute
        detail = (
            f"mean slippage {_fmt(delta.paper, '0.01')} vs {_fmt(delta.backtest, '0.01')} bps, "
            f"rise {_fmt(rise, '0.01')} (at most {self._t.max_slippage_increase_bps})"
        )
        breached = None if rise is None else rise > self._t.max_slippage_increase_bps
        return self._judge(m, breached, detail)


class ExpectancyDegradation(_Gate):
    name = "paper earns what the backtest earns per trade"

    def assess(self, m: ParityMetrics) -> GateResult:
        delta = m.delta(ParityMetric.EXPECTANCY)
        relative = delta.relative
        detail = (
            f"expectancy {_fmt(delta.paper, '0.000001')} vs {_fmt(delta.backtest, '0.000001')}, "
            f"change {_fmt(relative)} (drop at most {self._t.max_expectancy_drop_fraction})"
        )
        breached = None if relative is None else -relative > self._t.max_expectancy_drop_fraction
        return self._judge(m, breached, detail)


class DrawdownDegradation(_Gate):
    name = "paper draws down no more than the backtest"

    def assess(self, m: ParityMetrics) -> GateResult:
        delta = m.delta(ParityMetric.MAX_DRAWDOWN)
        relative = delta.relative
        detail = (
            f"worst drawdown {_fmt(delta.paper)} vs {_fmt(delta.backtest)}, "
            f"rise {_fmt(relative)} (at most {self._t.max_drawdown_increase_fraction})"
        )
        if delta.backtest == _ZERO and delta.paper == _ZERO:
            return self._judge(m, False, detail)
        breached = None if relative is None else relative > self._t.max_drawdown_increase_fraction
        return self._judge(m, breached, detail)


class WinRateDegradation(_Gate):
    name = "paper wins as often as the backtest"

    def assess(self, m: ParityMetrics) -> GateResult:
        delta = m.delta(ParityMetric.WIN_RATE)
        drop = None if delta.absolute is None else -delta.absolute
        detail = (
            f"win rate {_fmt(delta.paper)} vs {_fmt(delta.backtest)}, "
            f"drop {_fmt(drop)} (at most {self._t.max_win_rate_drop})"
        )
        return self._judge(m, None if drop is None else drop > self._t.max_win_rate_drop, detail)


class TurnoverDegradation(_Gate):
    name = "paper trades no more than the backtest"

    def assess(self, m: ParityMetrics) -> GateResult:
        ratio = m.turnover_ratio
        detail = (
            f"paper traded {_fmt(ratio)}x the backtest's value "
            f"(at most {self._t.max_turnover_ratio}x)"
        )
        return self._judge(m, None if ratio is None else ratio > self._t.max_turnover_ratio, detail)


class ParityVerdictReport:
    def __init__(self, verdict: Verdict, gates: tuple[GateResult, ...]) -> None:
        self.verdict = verdict
        self.gates = gates

    def of(self, outcome: GateOutcome) -> tuple[GateResult, ...]:
        return tuple(g for g in self.gates if g.outcome is outcome)


class ParityPolicy:
    def __init__(self, gates: Sequence[ParityGate]) -> None:
        if not gates:
            raise ValueError("a parity verdict needs at least one gate")
        names = [g.name for g in gates]
        if len(set(names)) != len(names):
            raise ValueError("two parity gates share a name")
        self._gates = tuple(gates)

    @classmethod
    def standard(cls, t: ParityThresholds) -> ParityPolicy:
        return cls(
            [
                EnoughSessions(t), EnoughMatchedTrades(t), SignalAgreement(t),
                FillRateDegradation(t), SlippageDegradation(t), ExpectancyDegradation(t),
                DrawdownDegradation(t), WinRateDegradation(t), TurnoverDegradation(t),
            ]
        )  # fmt: skip

    def classify(self, metrics: ParityMetrics) -> ParityVerdictReport:
        results = tuple(gate.assess(metrics) for gate in self._gates)
        outcomes = {r.outcome for r in results}
        if GateOutcome.FAIL in outcomes:
            verdict = Verdict.REJECTED
        elif GateOutcome.UNKNOWN in outcomes:
            verdict = Verdict.INCONCLUSIVE
        else:
            verdict = Verdict.VALIDATED
        return ParityVerdictReport(verdict, results)
