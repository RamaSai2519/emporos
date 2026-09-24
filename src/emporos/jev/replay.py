"""Record once, replay forever (EM-187).

A live Jev call in a backtest is paid, slow and not reproducible. So an experiment asks the model
ONCE per distinct question, appends the answer to a `JevDecisionJournal`, and every later run
(another mode, another threshold, a re-run of the published report) is served from the journal
by `ReplayJevProvider` with no network at all. RANKING and CONFIRMATION put the same per-candidate
question, so one recording serves both and every confidence threshold.

The journal key is `(request_hash, prompt_hash, model)`: a changed prompt or model is a different
question and is never answered from the old recording.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from emporos.domain.jev_records import JevDecisionRecord
from emporos.jev.models import JevDecision, JevRequest, failed_decision
from emporos.jev.prompts import JevPrompt
from emporos.jev.protocol import JevProvider

NOT_RECORDED = "not recorded"
REPLAY_PROVIDER = "replay"


class JevDecisionJournal(Protocol):
    async def append(self, record: JevDecisionRecord) -> bool:
        """True when written; False when this question was already answered (the first stands)."""
        ...

    async def get(
        self, request_hash: str, prompt_hash: str, model: str
    ) -> JevDecisionRecord | None: ...


class InMemoryJevDecisionJournal:
    """Same contract as the Mongo journal, for tests and one-shot runs."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], JevDecisionRecord] = {}

    async def append(self, record: JevDecisionRecord) -> bool:
        if record.key in self._records:
            return False
        self._records[record.key] = record
        return True

    async def get(
        self, request_hash: str, prompt_hash: str, model: str
    ) -> JevDecisionRecord | None:
        return self._records.get((request_hash, prompt_hash, model))

    def __len__(self) -> int:
        return len(self._records)

    def records(self) -> list[JevDecisionRecord]:
        return sorted(self._records.values(), key=lambda r: (r.as_of, r.request_hash))


class RecordingJevProvider:
    """A `JevProvider` decorator: passes the request through and journals the model's answer.

    Failures are returned to the caller but not journalled (see `JevDecisionRecord`)."""

    def __init__(self, inner: JevProvider, journal: JevDecisionJournal, mode: str) -> None:
        self._inner = inner
        self._journal = journal
        self._mode = mode

    async def decide(self, request: JevRequest) -> JevDecision:
        decision = await self._inner.decide(request)
        if decision.ok:
            await self._journal.append(self._record(request, decision))
        return decision

    def _record(self, request: JevRequest, decision: JevDecision) -> JevDecisionRecord:
        return JevDecisionRecord(
            request_hash=decision.request_hash or request.request_hash(),
            as_of=request.as_of,
            symbol=request.symbol,
            strategy=request.strategy_name,
            mode=self._mode,
            decision=decision.decision,
            confidence=decision.confidence,
            provider=decision.provider,
            model=decision.model or "",
            prompt_version=decision.prompt_version or "",
            prompt_hash=decision.prompt_hash or "",
            tokens_used=decision.tokens_used,
            latency_ms=decision.latency_ms,
            recorded_at=decision.requested_at,
        )


class ReplayJevProvider:
    """Answers from the journal by question identity and never touches a network. A question that
    was never recorded is a failed decision (`not recorded`), handled by the caller's fail-open /
    fail-closed policy like any other Jev failure, so an incomplete recording is visible, never
    silently papered over with a live call."""

    def __init__(self, journal: JevDecisionJournal, prompt: JevPrompt, model: str) -> None:
        self._journal = journal
        self._prompt = prompt
        self._model = model

    async def decide(self, request: JevRequest) -> JevDecision:
        request_hash = request.request_hash()
        record = await self._journal.get(request_hash, self._prompt.content_hash, self._model)
        if record is None:
            return failed_decision(
                REPLAY_PROVIDER,
                error=NOT_RECORDED,
                requested_at=request.as_of,
                prompt_version=self._prompt.version,
                prompt_hash=self._prompt.content_hash,
                request_hash=request_hash,
            )
        return JevDecision(
            decision=record.decision,
            confidence=Decimal(record.confidence) if record.confidence is not None else None,
            provider=record.provider,
            model=record.model,
            config_version=None,
            requested_at=record.recorded_at,
            latency_ms=record.latency_ms,
            tokens_used=record.tokens_used,
            prompt_version=record.prompt_version,
            prompt_hash=record.prompt_hash,
            request_hash=record.request_hash,
        )


class RecordMissesJevProvider:
    """Record mode: a question already in the journal is answered from it, and only a question
    never asked reaches the model (and is journalled). Running several variants of one experiment
    through this pays for each distinct question once."""

    def __init__(
        self,
        journal: JevDecisionJournal,
        inner: JevProvider,
        prompt: JevPrompt,
        model: str,
        mode: str,
    ) -> None:
        self._replay = ReplayJevProvider(journal, prompt, model)
        self._record = RecordingJevProvider(inner, journal, mode)

    async def decide(self, request: JevRequest) -> JevDecision:
        recorded = await self._replay.decide(request)
        if recorded.ok:
            return recorded
        return await self._record.decide(request)
