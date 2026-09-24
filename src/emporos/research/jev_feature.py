"""Jev's recorded confidence as a research meta-feature (EM-187).

Jev is tested as a *feature* of a candidate, not as a signal: the question is whether its opinion
predicts what happens next, judged by the same `FeatureStudy` machinery as every other feature
(forward returns, rank IC, decile spread, cost-adjusted expectancy, by regime).

The feature reads ONLY the recorded decisions in a `JevConfidenceIndex` built from the journal;
there is no provider inside a study, so a study can never spend money or call the model. It is
causal by construction: the value at a bar is the recorded decision whose `as_of` falls inside
that bar (after its open, at or before its close), i.e. something known by the time the bar is
complete, and a bar with no such decision is undefined (`None`), never filled from an older one.

The value is signed by the decision, `+confidence` for a confirm and `-confidence` for a reject; an
abstain has no opinion and is undefined. It scores the candidate's quality and is direction-
agnostic: the journal record does not carry the candidate's side, so for a strategy that trades
both ways evaluate the long and short candidates separately.

The journal stores the symbol the model saw, which is a pseudonym when the run was anonymised, so
the index is keyed by that and the feature maps a bar's real instrument id through the same
`pseudonym` function.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from emporos.domain.jev_records import JevDecisionRecord
from emporos.research.features import CausalHistory, Feature, FeatureDefinition, FeatureRegistry

JEV_CONFIDENCE = "jev_confidence"
CONFIRM = "confirm"
REJECT = "reject"


@dataclass(frozen=True)
class _Opinion:
    as_of: datetime
    value: Decimal


class JevConfidenceIndex:
    """Recorded opinions by the symbol the model saw, each list ordered by time."""

    def __init__(self, opinions: dict[str, list[_Opinion]]) -> None:
        self._opinions = {s: sorted(o, key=lambda x: x.as_of) for s, o in opinions.items()}
        self._times = {s: [o.as_of for o in os] for s, os in self._opinions.items()}

    @classmethod
    def from_records(
        cls, records: Iterable[JevDecisionRecord], model: str, prompt_hash: str
    ) -> JevConfidenceIndex:
        """Only records of THIS model and prompt: another prompt's opinion is another feature."""
        opinions: dict[str, list[_Opinion]] = {}
        for record in records:
            if record.model != model or record.prompt_hash != prompt_hash:
                continue
            value = cls._signed(record)
            if value is not None:
                opinions.setdefault(record.symbol, []).append(_Opinion(record.as_of, value))
        return cls(opinions)

    @staticmethod
    def _signed(record: JevDecisionRecord) -> Decimal | None:
        if record.confidence is None or record.decision not in (CONFIRM, REJECT):
            return None
        return record.confidence if record.decision == CONFIRM else -record.confidence

    def within(self, symbol: str, after: datetime, until: datetime) -> Decimal | None:
        """The latest opinion with `after < as_of <= until`, or None."""
        times = self._times.get(symbol)
        if not times:
            return None
        position = bisect_right(times, until) - 1
        if position < 0 or times[position] <= after:
            return None
        return self._opinions[symbol][position].value


class JevConfidenceFeature:
    def __init__(self, index: JevConfidenceIndex, pseudonym: Callable[[str], str]) -> None:
        self._index = index
        self._pseudonym = pseudonym

    def compute(self, history: CausalHistory) -> Decimal | None:
        bar = history.last
        return self._index.within(self._pseudonym(bar.instrument_id), bar.ts, bar.closes_at)


def jev_confidence_definition(
    model: str, prompt_hash: str, version: str = "1"
) -> FeatureDefinition:
    return FeatureDefinition(
        name=JEV_CONFIDENCE,
        version=version,
        description="Jev's recorded confidence for a candidate in this bar: + confirm, - reject",
        parameters={"model": model, "prompt_hash": prompt_hash},
    )


def register_jev_confidence(
    registry: FeatureRegistry,
    index: JevConfidenceIndex,
    pseudonym: Callable[[str], str],
    model: str,
    prompt_hash: str,
) -> Feature:
    feature = JevConfidenceFeature(index, pseudonym)
    registry.register(jev_confidence_definition(model, prompt_hash), feature)
    return feature
