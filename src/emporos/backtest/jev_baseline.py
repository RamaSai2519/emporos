"""What "the baseline is validated" means for a portfolio of strategies (EM-187).

Verdicts are recorded per strategy configuration, but the Jev experiment's baseline is the whole
portfolio without Jev. The portfolio is VALIDATED only if every strategy in it currently stands
VALIDATED (its recorded verdict applies to the config as it is today); any REJECTED strategy makes
it REJECTED; everything else (stale, never curated, inconclusive) leaves it INCONCLUSIVE. It is
the strictest honest fold: Jev may not launder one weak strategy inside a good portfolio.
"""

from __future__ import annotations

from collections.abc import Mapping

from emporos.backtest.robustness.verdict import GateOutcome, GateResult, VerdictReport
from emporos.domain.experiments import Verdict
from emporos.domain.research_experiments import ReasonCode
from emporos.domain.verdicts import Standing


class BaselineVerdictFolder:
    def fold(self, standings: Mapping[str, Standing]) -> VerdictReport:
        if not standings:
            raise ValueError("a baseline needs at least one strategy")
        gates = tuple(self._gate(name, standing) for name, standing in sorted(standings.items()))
        outcomes = {g.outcome for g in gates}
        if GateOutcome.FAIL in outcomes:
            verdict = Verdict.REJECTED
        elif GateOutcome.UNKNOWN in outcomes:
            verdict = Verdict.INCONCLUSIVE
        else:
            verdict = Verdict.VALIDATED
        return VerdictReport(verdict, gates)

    @staticmethod
    def _gate(strategy: str, standing: Standing) -> GateResult:
        name = f"{strategy} stands validated"
        if standing is Standing.VALIDATED:
            return GateResult(name, GateOutcome.PASS, "validated", None)
        outcome = GateOutcome.FAIL if standing is Standing.REJECTED else GateOutcome.UNKNOWN
        return GateResult(name, outcome, standing.value, ReasonCode.JEV_BASELINE_NOT_VALIDATED)
