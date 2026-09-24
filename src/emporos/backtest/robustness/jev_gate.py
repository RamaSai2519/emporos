"""The Jev graduation guard: Jev may add to a strategy that already works; it may not rescue one
that does not (EM-187).

The rule, as code:

* If the BASELINE (the same portfolio with Jev off) is not VALIDATED, the Jev arm is at most
  INCONCLUSIVE (`JEV_BASELINE_NOT_VALIDATED`), and REJECTED only if it is itself rejected. A weak
  or negative baseline that Jev "improves" is exactly what this exists to refuse: an LLM that
  merely trims the losers of a losing strategy has not produced an edge.
* If the baseline IS validated, the Jev arm is VALIDATED only if every one of these passes: its own
  full verdict is VALIDATED; the expectancy gain, net of what Jev cost, is positive; the paired
  day-level interval for the gain is entirely above zero; and the max drawdown is no worse (within
  a declared tolerance).

Same asymmetry as the standard verdict: a gate says FAIL only when the evidence is strong enough
to be believed (the whole interval is on the wrong side), UNKNOWN when it is merely not enough
(the interval straddles zero, the arms could not be paired). Any FAIL rejects, any UNKNOWN leaves
it inconclusive, and only all-PASS validates.

Each gate is a small class over a `JevGateContext`, so a new requirement is a new class.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar, Protocol

from emporos.backtest.jev_incremental import JevIncrementalEvidence
from emporos.backtest.robustness.verdict import GateOutcome, GateResult, VerdictReport
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import ReasonCode

_ZERO = Decimal(0)


@dataclass(frozen=True)
class JevGateContext:
    baseline: VerdictReport
    treatment: VerdictReport | None  # None: the Jev arm has not been judged by the full policy
    evidence: JevIncrementalEvidence


class JevGate(Protocol):
    name: str
    code: ClassVar[ReasonCode]

    def assess(self, context: JevGateContext) -> GateResult: ...


class _GateBase:
    name: str
    code: ClassVar[ReasonCode]

    def _result(self, outcome: GateOutcome, detail: str) -> GateResult:
        return GateResult(self.name, outcome, detail, self.code)


class BaselineValidated(_GateBase):
    """Recorded as UNKNOWN when the baseline is not validated: it caps the outcome at INCONCLUSIVE
    without itself rejecting anything."""

    name = "the baseline without Jev is validated"
    code = ReasonCode.JEV_BASELINE_NOT_VALIDATED

    def assess(self, context: JevGateContext) -> GateResult:
        verdict = context.baseline.verdict
        if verdict is Verdict.VALIDATED:
            return self._result(GateOutcome.PASS, "the baseline is validated")
        return self._result(
            GateOutcome.UNKNOWN,
            f"the baseline is {verdict.value}: Jev is not eligible to lift a strategy that has "
            "not validated on its own",
        )


class TreatmentValidated(_GateBase):
    name = "the Jev arm passes the full verdict on its own"
    code = ReasonCode.JEV_TREATMENT_NOT_VALIDATED

    def assess(self, context: JevGateContext) -> GateResult:
        treatment = context.treatment
        if treatment is None:
            return self._result(GateOutcome.UNKNOWN, "the Jev arm was not judged by the policy")
        if treatment.verdict is Verdict.VALIDATED:
            return self._result(GateOutcome.PASS, "the Jev arm is validated")
        outcome = GateOutcome.FAIL if treatment.verdict is Verdict.REJECTED else GateOutcome.UNKNOWN
        return self._result(outcome, f"the Jev arm is {treatment.verdict.value}")


class TreatmentNotRejected(_GateBase):
    """For a weak baseline: the Jev arm's own verdict can only make things worse, never better."""

    name = "the Jev arm is not itself rejected"
    code = ReasonCode.JEV_TREATMENT_NOT_VALIDATED

    def assess(self, context: JevGateContext) -> GateResult:
        treatment = context.treatment
        if treatment is not None and treatment.verdict is Verdict.REJECTED:
            return self._result(GateOutcome.FAIL, "the Jev arm is rejected on its own")
        return self._result(GateOutcome.PASS, "the Jev arm is not rejected on its own")


class NetExpectancyLift(_GateBase):
    name = "the expectancy gain survives Jev's own cost"
    code = ReasonCode.JEV_NO_NET_EXPECTANCY_LIFT

    def assess(self, context: JevGateContext) -> GateResult:
        gain = context.evidence.net_of_jev_average_trade_delta
        if gain is None:
            return self._result(GateOutcome.UNKNOWN, "an arm has no trades to average")
        detail = (
            f"average trade {gain:+.2f} INR versus the baseline, net of "
            f"{context.evidence.cost.inr:.2f} INR of Jev cost"
        )
        return self._result(GateOutcome.PASS if gain > _ZERO else GateOutcome.FAIL, detail)


class SignificantLift(_GateBase):
    name = "the gain is distinguishable from noise"
    code = ReasonCode.JEV_LIFT_NOT_SIGNIFICANT

    def assess(self, context: JevGateContext) -> GateResult:
        paired = context.evidence.paired
        if paired is None:
            return self._result(GateOutcome.UNKNOWN, context.evidence.paired_reason or "unpaired")
        interval = paired.mean_daily_return
        detail = (
            f"paired daily gain {interval.low:+.6f} to {interval.high:+.6f} "
            f"({paired.confidence:.0%}, {paired.days} days, net of Jev cost)"
        )
        if interval.low > _ZERO:
            return self._result(GateOutcome.PASS, detail)
        if interval.high < _ZERO:
            return self._result(GateOutcome.FAIL, detail)
        return self._result(GateOutcome.UNKNOWN, detail)


class DrawdownNotWorse(_GateBase):
    name = "the maximum drawdown is no worse"
    code = ReasonCode.JEV_DRAWDOWN_WORSE

    def __init__(self, tolerance: Decimal = _ZERO) -> None:
        if tolerance < _ZERO:
            raise ValueError("the drawdown tolerance cannot be negative")
        self._tolerance = tolerance

    def assess(self, context: JevGateContext) -> GateResult:
        change = context.evidence.deltas.max_drawdown
        detail = f"max drawdown moved {change:+.4f} (tolerance {self._tolerance})"
        outcome = GateOutcome.PASS if change <= self._tolerance else GateOutcome.FAIL
        return self._result(outcome, detail)


class JevIncrementalPolicy:
    """`classify(baseline, treatment, evidence)` is the whole decision. The two gate lists are
    injected so a stricter or looser policy is a composition choice, not an edit here."""

    def __init__(
        self,
        weak_baseline_gates: Sequence[JevGate],
        validated_baseline_gates: Sequence[JevGate],
    ) -> None:
        if not weak_baseline_gates or not validated_baseline_gates:
            raise ValueError("a Jev policy needs gates for both branches")
        self._weak = tuple(weak_baseline_gates)
        self._validated = tuple(validated_baseline_gates)

    @classmethod
    def standard(cls, drawdown_tolerance: Decimal = _ZERO) -> JevIncrementalPolicy:
        return cls(
            weak_baseline_gates=[BaselineValidated(), TreatmentNotRejected()],
            validated_baseline_gates=[
                BaselineValidated(),
                TreatmentValidated(),
                NetExpectancyLift(),
                SignificantLift(),
                DrawdownNotWorse(drawdown_tolerance),
            ],
        )

    def classify(
        self,
        baseline: VerdictReport,
        treatment: VerdictReport | None,
        evidence: JevIncrementalEvidence,
    ) -> VerdictReport:
        context = JevGateContext(baseline, treatment, evidence)
        gates = self._validated if baseline.verdict is Verdict.VALIDATED else self._weak
        results = tuple(gate.assess(context) for gate in gates)
        outcomes = {r.outcome for r in results}
        if GateOutcome.FAIL in outcomes:
            verdict = Verdict.REJECTED
        elif GateOutcome.UNKNOWN in outcomes:
            verdict = Verdict.INCONCLUSIVE
        else:
            verdict = Verdict.VALIDATED
        return VerdictReport(verdict, results)
