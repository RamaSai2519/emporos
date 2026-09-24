"""May this strategy start? One small condition per rule, and a policy that lists every refusal.

Two policies use the same conditions differently:

* **Paper** may start anything, but a strategy that is not currently VALIDATED needs the operator to
  name its standing (`rejected`, `stale`, ...) in the command. A rejected strategy in paper is a
  legitimate experiment; an unread click is not.
* **Live** needs every condition to hold, and nothing can be acknowledged past them: the
  `LIVE_TRADING_ENABLED` switch, the strategy enabled in its config, a VALIDATED verdict for
  exactly this configuration, the kill switch clear, and (EM-189) the graduation ledger at
  LIVE_CONSERVATIVE for exactly this configuration, a human's typed acknowledgement of it, and the
  worker started with the risk tier that stage requires.

Conditions are independent objects, so a new rule is a new class in a policy's list, not an edit to
an existing one. All refusals are reported together, so the operator fixes them in one pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from emporos.domain.graduation import GraduationStage
from emporos.domain.verdicts import RecordedVerdict, Standing, standing_of
from emporos.risk.config import RiskTier
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.snapshot import ConfigSnapshotter


class LaunchRefused(Exception):
    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass(frozen=True)
class LaunchRequest:
    strategy: str
    behaviour_hash: str
    enabled: bool  # the strategy's own `enabled` flag in its config
    acknowledged: str | None = None  # the standing the operator typed, if any


class VerdictBook(Protocol):
    async def latest(self, strategy: str) -> RecordedVerdict | None: ...


class SwitchView(Protocol):
    async def halted(self) -> bool: ...


class GraduationStageView(Protocol):
    """Where a configuration stands on the road to real money. The ledger is the only source."""

    async def stage(self, strategy: str, behaviour_hash: str) -> GraduationStage: ...


class AcknowledgementView(Protocol):
    async def acknowledged(self, strategy: str, behaviour_hash: str) -> bool: ...


class LaunchCondition(Protocol):
    async def refusal(self, request: LaunchRequest) -> str | None:
        """Why this request must not proceed, or None if this rule is satisfied."""
        ...


class LaunchPolicy(Protocol):
    async def require(self, request: LaunchRequest) -> None:
        """Return if the launch may proceed; raise `LaunchRefused` with every reason otherwise."""
        ...


class AllConditions:
    """A policy that holds only when every one of its conditions does."""

    def __init__(self, conditions: Sequence[LaunchCondition]) -> None:
        self._conditions = tuple(conditions)

    async def require(self, request: LaunchRequest) -> None:
        reasons = [r for c in self._conditions if (r := await c.refusal(request)) is not None]
        if reasons:
            raise LaunchRefused(reasons)


class VerdictStanding:
    """Looks the verdict up once per request, so conditions agree on what it says."""

    def __init__(self, book: VerdictBook) -> None:
        self._book = book

    async def of(self, request: LaunchRequest) -> tuple[Standing, RecordedVerdict | None]:
        verdict = await self._book.latest(request.strategy)
        return standing_of(verdict, request.behaviour_hash), verdict


class StandingAcknowledged:
    """Unless the strategy is validated, the operator must have typed its standing."""

    def __init__(self, standings: VerdictStanding) -> None:
        self._standings = standings

    async def refusal(self, request: LaunchRequest) -> str | None:
        standing, _ = await self._standings.of(request)
        if standing is Standing.VALIDATED or request.acknowledged == standing.value:
            return None
        return (
            f"{request.strategy} is not validated (standing: {standing.value}); "
            f"to run it anyway, acknowledge its standing by sending {standing.value!r}"
        )


class ValidatedForThisConfig:
    """The strategy must hold a VALIDATED verdict that was recorded for exactly this config."""

    def __init__(self, standings: VerdictStanding) -> None:
        self._standings = standings

    async def refusal(self, request: LaunchRequest) -> str | None:
        standing, verdict = await self._standings.of(request)
        if standing is Standing.VALIDATED:
            return None
        if standing is Standing.STALE and verdict is not None:
            return (
                f"the {verdict.verdict.value} verdict on record for {request.strategy} was "
                "recorded for a different configuration; curate the current one"
            )
        if standing is Standing.NONE:
            return f"{request.strategy} has no recorded verdict"
        return f"{request.strategy} is {standing.value}, not validated"


class LiveTradingSwitch:
    """The operator's environment-level switch (LIVE_TRADING_ENABLED)."""

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    async def refusal(self, request: LaunchRequest) -> str | None:
        if self._enabled:
            return None
        return "LIVE_TRADING_ENABLED is not set to 'true'"


class StrategyEnabled:
    async def refusal(self, request: LaunchRequest) -> str | None:
        if request.enabled:
            return None
        return f"{request.strategy} has `enabled: false` in its config"


class KillSwitchClear:
    def __init__(self, switch: SwitchView) -> None:
        self._switch = switch

    async def refusal(self, request: LaunchRequest) -> str | None:
        if await self._switch.halted():
            return "the kill switch is engaged"
        return None


# The risk tier each stage trades under. A stage with no entry places no tier requirement.
STAGE_RISK_TIERS = {
    GraduationStage.LIVE_CONSERVATIVE: RiskTier.LIVE_CONSERVATIVE,
    GraduationStage.PRODUCTION: RiskTier.STANDARD,
}


class GraduatedTo:
    """The ledger holds this configuration at `minimum` or beyond. Retired, researching and stale
    (edited since it was promoted) all fall short."""

    def __init__(self, minimum: GraduationStage, stages: GraduationStageView) -> None:
        self._minimum = minimum
        self._stages = stages

    async def refusal(self, request: LaunchRequest) -> str | None:
        stage = await self._stages.stage(request.strategy, request.behaviour_hash)
        if stage.rank >= self._minimum.rank:
            return None
        return (
            f"{request.strategy} is at {stage.value} for this configuration; it must be graduated "
            f"to {self._minimum.value} (`emporos graduation promote`)"
        )


class FirstLiveAcknowledged:
    """A human typed the acknowledgement for exactly this configuration. Always required: whether
    the strategy has run live before is not something a launch should be trusted to say."""

    def __init__(self, acknowledgements: AcknowledgementView) -> None:
        self._acknowledgements = acknowledgements

    async def refusal(self, request: LaunchRequest) -> str | None:
        if await self._acknowledgements.acknowledged(request.strategy, request.behaviour_hash):
            return None
        return (
            f"no human acknowledgement is recorded for {request.strategy} in this configuration "
            f"(`emporos graduation acknowledge {request.strategy}`)"
        )


class RiskTierMatchesStage:
    """The worker was started with the risk limits the strategy's stage requires."""

    def __init__(self, loaded: RiskTier, stages: GraduationStageView) -> None:
        self._loaded = loaded
        self._stages = stages

    async def refusal(self, request: LaunchRequest) -> str | None:
        stage = await self._stages.stage(request.strategy, request.behaviour_hash)
        required = STAGE_RISK_TIERS.get(stage)
        if required is None or required is self._loaded:
            return None
        return (
            f"{request.strategy} is at {stage.value}, which trades under the {required.value} risk "
            f"tier, but this worker loaded {self._loaded.value}"
        )


class AnyOf:
    """Satisfied when any one condition is; otherwise every reason is reported."""

    def __init__(self, conditions: Sequence[LaunchCondition]) -> None:
        if not conditions:
            raise ValueError("AnyOf with no condition could never be satisfied")
        self._conditions = tuple(conditions)

    async def refusal(self, request: LaunchRequest) -> str | None:
        reasons: list[str] = []
        for condition in self._conditions:
            reason = await condition.refusal(request)
            if reason is None:
                return None
            reasons.append(reason)
        return "; or ".join(reasons)


@dataclass(frozen=True)
class LiveGraduation:
    """What the live gate needs from graduation, bundled so a live policy cannot be built without
    it: there is no optional argument to forget."""

    stages: GraduationStageView
    acknowledgements: AcknowledgementView
    risk_tier: RiskTier


def paper_policy(book: VerdictBook, stages: GraduationStageView) -> LaunchPolicy:
    """Paper may run what is graduated to PAPER, or what the operator names the standing of."""
    return AllConditions(
        [
            AnyOf(
                [
                    GraduatedTo(GraduationStage.PAPER, stages),
                    StandingAcknowledged(VerdictStanding(book)),
                ]
            )
        ]
    )


def live_policy(
    book: VerdictBook,
    switch_enabled: bool,
    kill_switch: SwitchView,
    graduation: LiveGraduation,
) -> LaunchPolicy:
    return AllConditions(
        [
            LiveTradingSwitch(switch_enabled),
            StrategyEnabled(),
            ValidatedForThisConfig(VerdictStanding(book)),
            KillSwitchClear(kill_switch),
            GraduatedTo(GraduationStage.LIVE_CONSERVATIVE, graduation.stages),
            FirstLiveAcknowledged(graduation.acknowledgements),
            RiskTierMatchesStage(graduation.risk_tier, graduation.stages),
        ]
    )


class LaunchFacts(Protocol):
    def request_for(self, name: str, acknowledged: str | None) -> LaunchRequest | None:
        """What a launch of this strategy is, or None if there is no such strategy."""
        ...


class ConfigLaunchFacts:
    """Launch requests built from the strategy configs this worker can load."""

    def __init__(self, configs: Sequence[ResolvedStrategyConfig]) -> None:
        self._configs = {c.name: c for c in configs}
        self._snapshotter = ConfigSnapshotter()

    def request_for(self, name: str, acknowledged: str | None) -> LaunchRequest | None:
        config = self._configs.get(name)
        if config is None:
            return None
        behaviour_hash = self._snapshotter.take(config).behaviour_hash
        return LaunchRequest(name, behaviour_hash, config.enabled, acknowledged)


class PolicyStartGate:
    """The `StartGate` of the control layer, answered by a launch policy.

    It judges strategies that exist. A name it does not know is passed through unjudged: the host
    that would launch it fails it by name, exactly as it always has.
    """

    def __init__(self, policy: LaunchPolicy, facts: LaunchFacts) -> None:
        self._policy = policy
        self._facts = facts

    async def check(self, name: str, acknowledged: str | None) -> str | None:
        request = self._facts.request_for(name, acknowledged)
        if request is None:
            return None
        try:
            await self._policy.require(request)
        except LaunchRefused as refused:
            return str(refused)
        return None
