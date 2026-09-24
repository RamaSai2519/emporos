"""What `emporos graduation ...` says and does, apart from typer and Mongo so it can be tested.

Every method returns the lines to print and whether it succeeded; it never prints. The human
acknowledgement is the one thing here that takes a person: `acknowledge` shows exactly what is being
accepted and asks for the phrase through an injected `prompt`, so a test can prove a wrong phrase
records nothing, and no script path can supply the phrase for the operator.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.core.clock import Clock
from emporos.core.errors import ConfigurationError
from emporos.domain.graduation import (
    GraduationEvent,
    GraduationStage,
    LiveAcknowledgement,
    acknowledgement_phrase,
)
from emporos.graduation.ports import AcknowledgementBook, ExperimentEvidence
from emporos.graduation.service import (
    GraduationService,
    InvalidTransition,
    PromotionRefused,
    StandingReport,
)
from emporos.persistence.graduation_store import AcknowledgementExistsError, GraduationConflictError
from emporos.risk.config import RiskTier
from emporos.risk.limits import RiskLimits
from emporos.session.launch_gate import VerdictBook


@dataclass(frozen=True)
class Outcome:
    lines: tuple[str, ...]
    ok: bool


class StrategySubjects:
    """Strategy name -> the behaviour hash of its config as it is on disk now."""

    def __init__(self, hashes: dict[str, str]) -> None:
        self._hashes = dict(hashes)

    def names(self) -> list[str]:
        return sorted(self._hashes)

    def hash_of(self, name: str) -> str:
        try:
            return self._hashes[name]
        except KeyError:
            raise ConfigurationError(
                f"unknown strategy {name!r}; known: {', '.join(self.names())}"
            ) from None


class GraduationConsole:
    def __init__(
        self,
        service: GraduationService,
        subjects: StrategySubjects,
        acknowledgements: AcknowledgementBook,
        experiments: ExperimentEvidence,
        verdicts: VerdictBook,
        clock: Clock,
        live_tier: RiskTier,
        live_limits: RiskLimits,
    ) -> None:
        self._service = service
        self._subjects = subjects
        self._acknowledgements = acknowledgements
        self._experiments = experiments
        self._verdicts = verdicts
        self._clock = clock
        self._live_tier = live_tier
        self._live_limits = live_limits

    async def status(self, names: Sequence[str], jev_enabled: bool = False) -> Outcome:
        chosen = list(names) or self._subjects.names()
        lines: list[str] = []
        for name in chosen:
            behaviour_hash = self._subjects.hash_of(name)
            report = await self._service.standing(name, behaviour_hash, None, jev_enabled)
            lines += self._standing_lines(report)
        return Outcome(tuple(lines), True)

    async def promote(
        self, name: str, target: GraduationStage, actor: str, experiment: str | None,
        jev_enabled: bool = False,
    ) -> Outcome:  # fmt: skip
        behaviour_hash = self._subjects.hash_of(name)
        try:
            event = await self._service.promote(
                name, behaviour_hash, target, actor, experiment, jev_enabled
            )
        except PromotionRefused as refused:
            lines = [f"{name}: NOT promoted to {target.value}"]
            lines += [f"  - {reason}" for reason in refused.reasons]
            return Outcome(tuple(lines), False)
        except GraduationConflictError as conflict:
            return Outcome((f"{name}: another promotion won the race ({conflict}); retry",), False)
        return Outcome(self._event_lines(event), True)

    async def demote(self, name: str, to: GraduationStage, actor: str, reason: str) -> Outcome:
        try:
            event = await self._service.demote(
                name, self._subjects.hash_of(name), to, actor, reason
            )
        except InvalidTransition as invalid:
            return Outcome((f"{name}: {invalid}",), False)
        return Outcome(self._event_lines(event), True)

    async def history(self, name: str) -> Outcome:
        self._subjects.hash_of(name)  # an unknown name is an error, not an empty history
        events = await self._service.history(name)
        if not events:
            return Outcome((f"{name}: no graduation events",), True)
        return Outcome(tuple(self._event_line(e) for e in events), True)

    async def acknowledge(
        self, name: str, operator: str, prompt: Callable[[str], str],
        experiment: str | None = None,
    ) -> Outcome:  # fmt: skip
        """Show what going live means, and record the acknowledgement only if the operator types
        the exact phrase. One attempt: a wrong phrase records nothing and says so."""
        behaviour_hash = self._subjects.hash_of(name)
        stage = await self._service.current(name, behaviour_hash)
        if stage.rank < GraduationStage.PAPER.rank:
            return Outcome(
                (f"{name} is at {stage.value}: promote it to paper before acknowledging live",),
                False,
            )
        phrase = acknowledgement_phrase(name, behaviour_hash)
        shown = [*await self._summary(name, behaviour_hash, experiment), ""]
        typed = prompt(f"Type {phrase!r} exactly to accept the first live deployment")
        if typed != phrase:
            return Outcome((*shown, "The phrase did not match: nothing was recorded."), False)
        try:
            await self._acknowledgements.record(
                LiveAcknowledgement(
                    name,
                    behaviour_hash,
                    operator,
                    typed,
                    self._live_tier.value,
                    self._clock.now(),
                )  # fmt: skip
            )
        except AcknowledgementExistsError:
            return Outcome(
                (*shown, f"{name} is already acknowledged in this configuration."), False
            )
        return Outcome((*shown, f"Acknowledged by {operator}."), True)

    # --- what is shown -------------------------------------------------------------------------
    async def _summary(self, name: str, behaviour_hash: str, cited: str | None) -> list[str]:
        limits = self._live_limits
        lines = [
            f"LIVE acknowledgement for {name} @ {behaviour_hash[:8]}",
            f"  risk tier:            {self._live_tier.value}",
            f"  account capital:      {_plain(limits.account_capital)}",
            f"  max capital deployed: {_plain(limits.max_capital_deployed)}",
            f"  max position value:   {_plain(limits.max_position_value)}",
            f"  max open positions:   {limits.max_open_positions}",
            f"  max daily loss:       {_plain(limits.max_daily_loss)}",
            f"  max strategy loss:    {_plain(limits.max_strategy_loss)}",
            f"  max risk per trade:   {_plain(limits.max_risk_per_trade)}",
        ]
        view = await self._experiments.get(cited) if cited else None
        view = view or await self._experiments.latest_for(behaviour_hash, "strategy")
        if view is None:
            lines.append("  experiment:           none published for this configuration")
        else:
            lines.append(
                f"  experiment:           {view.experiment_id} ({view.outcome.value}, "
                f"holdout {'reserved' if view.holdout_reserved else 'NOT reserved'})"
            )
        verdict = await self._verdicts.latest(name)
        if verdict is None:
            lines.append("  verdict:              none recorded")
        else:
            standing = verdict.standing_for(behaviour_hash).value
            lines.append(f"  verdict:              {standing} ({verdict.experiment})")
        return lines

    @staticmethod
    def _standing_lines(report: StandingReport) -> list[str]:
        head = f"{report.strategy} @ {report.behaviour_hash[:8]}: {report.stage.value}"
        if report.next_stage is None:
            return [head + " (nothing further is promotable)"]
        lines = [head + f" -> next: {report.next_stage.value}"]
        for item in report.requirements:
            mark = "ok  " if item.assessment.is_met else "MISS"
            reason = "" if item.assessment.refusal is None else f": {item.assessment.refusal}"
            lines.append(f"  [{mark}] {item.name}{reason}")
        return lines

    @staticmethod
    def _event_line(event: GraduationEvent) -> str:
        cited = ", ".join(f"{e.kind.value}:{e.ref}" for e in event.evidence) or "-"
        return (
            f"#{event.seq} {event.at.isoformat()} {event.kind.value} "
            f"{event.from_stage.value}->{event.to_stage.value} by {event.actor}: "
            f"{event.reason} [{cited}]"
        )

    def _event_lines(self, event: GraduationEvent) -> tuple[str, ...]:
        return (f"{event.strategy}: {self._event_line(event)}",)


def _plain(value: Decimal | None) -> str:
    return "not set" if value is None else format(value, "f")
