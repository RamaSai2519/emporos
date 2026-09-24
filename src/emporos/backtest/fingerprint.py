"""`ExperimentFingerprint` — a content hash of everything that must be identical between two arms
of an experiment (EM-187).

"Identical" is otherwise a convention the caller keeps. The fingerprint makes it a fact that can
be checked: it is a SHA-256 over the spec's behaviour (each config's behaviour hash, the window,
capital, allocation constraints, fill and metric settings) plus what the spec cannot see about
the run — the data provenance hashes, the fee schedule and the reserved holdout. It is computed
once from the spec that both arms are given, and every arm's result is verified against it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import timedelta

from emporos.backtest.multi_engine import MultiStrategyBacktestSpec
from emporos.backtest.provenance import ResearchProvenance
from emporos.core.hashing import Canonical, content_hash
from emporos.domain.research_experiments import DatePair
from emporos.strategies.snapshot import Canonicalizer, ConfigSnapshotter


class FingerprintMismatch(ValueError):
    """Two arms of an experiment did not run under identical assumptions."""


@dataclass(frozen=True)
class FingerprintContext:
    """What the spec cannot see, but which must still be identical between arms."""

    provenance: ResearchProvenance | None = None
    fee_schedule_id: str | None = None
    holdout: DatePair | None = None


@dataclass(frozen=True)
class ExperimentFingerprint:
    digest: str

    def verify(self, spec: MultiStrategyBacktestSpec, context: FingerprintContext) -> None:
        """Raises unless `spec` (as an arm actually ran it) hashes to this fingerprint."""
        actual = ExperimentFingerprinter().fingerprint(spec, context)
        if actual != self:
            raise FingerprintMismatch(
                f"an experiment arm ran under different assumptions: {actual.digest} "
                f"is not {self.digest}"
            )


class ExperimentFingerprinter:
    def __init__(
        self,
        snapshotter: ConfigSnapshotter | None = None,
        canonicalizer: Canonicalizer | None = None,
    ) -> None:
        self._snapshotter = snapshotter or ConfigSnapshotter()
        self._canonicalizer = canonicalizer or Canonicalizer()

    def fingerprint(
        self, spec: MultiStrategyBacktestSpec, context: FingerprintContext | None = None
    ) -> ExperimentFingerprint:
        context = context or FingerprintContext()
        document = self._canonicalizer.canonical(
            {
                "configs": [self._snapshotter.take(c).behaviour_hash for c in spec.configs],
                "window": [spec.window.start.isoformat(), spec.window.end.isoformat()],
                "starting_cash": spec.starting_cash.amount,
                "constraints": dataclasses.asdict(spec.constraints),
                "fills": spec.fills,
                "metrics": dataclasses.asdict(spec.metrics),
                "warmup_bars": spec.warmup_bars,
                "warmup_lookback_seconds": _seconds(spec.warmup_lookback),
                "assumptions": list(spec.assumptions),
                "provenance": self._provenance(context.provenance),
                "fee_schedule_id": context.fee_schedule_id,
                "holdout": self._holdout(context.holdout),
            }
        )
        assert isinstance(document, dict)
        return ExperimentFingerprint(content_hash(document))

    @staticmethod
    def _provenance(provenance: ResearchProvenance | None) -> Canonical:
        if provenance is None:
            return None
        return {
            "schema_version": provenance.schema_version,
            "timeframe": provenance.dataset_timeframe.value,
            "first": provenance.dataset_first.isoformat(),
            "last": provenance.dataset_last.isoformat(),
            "universe_hash": provenance.universe_hash,
            "calendar_version": provenance.calendar_version,
            "quarantine_hash": provenance.quarantine_hash,
            "assumed_instrument_ids": list(provenance.assumed_instrument_ids),
        }

    @staticmethod
    def _holdout(holdout: DatePair | None) -> Canonical:
        if holdout is None:
            return None
        return [holdout.first.isoformat(), holdout.last.isoformat()]


def _seconds(delta: timedelta) -> int:
    return int(delta.total_seconds())
