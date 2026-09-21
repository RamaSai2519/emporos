"""Turns a curation result into the verdict that gets recorded.

Two sources, one shape: the `CurationRecord` a run has just produced, and the JSON document an
earlier run wrote (so verdicts reached before verdicts were recorded can be recorded from their own
reports, never retyped). A result without a robustness assessment cannot be VALIDATED; it is
REJECTED if it failed its checks and INCONCLUSIVE otherwise, and its checks stand in as its gates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from emporos.backtest.curation import CurationRecord
from emporos.domain.experiments import Verdict
from emporos.domain.verdicts import GateFinding, RecordedVerdict


@dataclass(frozen=True)
class VerdictContext:
    """What a verdict needs to say about how it was reached, beyond the numbers themselves."""

    behaviour_hash: str  # of the config that was judged
    capital: str
    first_day: str
    last_day: str
    experiment: str
    recorded_at: datetime
    notes: tuple[str, ...] = ()


class CurationVerdicts:
    def of(self, record: CurationRecord, context: VerdictContext) -> RecordedVerdict:
        if record.robustness is not None:
            verdict = Verdict(record.robustness.verdict.verdict.value)
            gates = tuple(
                GateFinding(g.name, g.outcome.value, g.detail)
                for g in record.robustness.verdict.gates
            )
        else:
            verdict = Verdict.INCONCLUSIVE if record.verdict.passed else Verdict.REJECTED
            gates = tuple(
                GateFinding(
                    c.name, "pass" if c.passed else "fail", f"{c.actual} (needs {c.required})"
                )
                for c in record.verdict.checks
            )
        return _recorded(record.strategy, verdict, gates, context, "curation")


class ReportVerdicts:
    """Reads one strategy's entry from a curation JSON report."""

    def of(self, document: Mapping[str, Any], context: VerdictContext) -> RecordedVerdict:
        robustness = document.get("robustness")
        if robustness is None:
            verdict = Verdict.INCONCLUSIVE if document["passed"] else Verdict.REJECTED
            gates = tuple(
                GateFinding(
                    c["name"], "pass" if c["passed"] else "fail",
                    f"{c['actual']} (needs {c['required']})",
                )
                for c in document["checks"]
            )  # fmt: skip
        else:
            verdict = Verdict(robustness["verdict"])
            gates = tuple(
                GateFinding(g["name"], g["outcome"], g["detail"]) for g in robustness["gates"]
            )
        return _recorded(str(document["strategy"]), verdict, gates, context, "imported")

    def entries(self, documents: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
        return {str(d["strategy"]): d for d in documents}


def _recorded(
    strategy: str,
    verdict: Verdict,
    gates: tuple[GateFinding, ...],
    context: VerdictContext,
    source: str,
) -> RecordedVerdict:
    return RecordedVerdict(
        strategy=strategy,
        behaviour_hash=context.behaviour_hash,
        verdict=verdict,
        gates=gates,
        capital=context.capital,
        first_day=context.first_day,
        last_day=context.last_day,
        experiment=context.experiment,
        source=source,
        recorded_at=context.recorded_at,
        notes=context.notes,
    )
