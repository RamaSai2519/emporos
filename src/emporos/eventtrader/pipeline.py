"""The decision pipeline: Triage, then a Panel of three, then a Judge, then an optional Arbiter
(PROFIT_PLAN §12.3, EM-240).

Every stage is behind the `LlmStage` Protocol; this class only orders them and applies the rules
that are not the models' to bend: a trade needs the judge's yes AND, when the panel is on, at least
two of the three panellists on the judge's side; any invalid reply or failed call is no trade. The
output is a proposal. Nothing here sizes a position or reaches an order: the risk engine decides how
much, and can always refuse."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from emporos.eventtrader.stages.base import LlmStage, StageOutcome
from emporos.eventtrader.stages.models import (
    ArbiterResult,
    Horizon,
    Instrument,
    JudgeResult,
    PanelView,
    Side,
    TriageResult,
)
from emporos.eventtrader.stages.stages import ArbiterInput, EventInput, JudgeInput

__all__ = ["DecisionPipeline", "PipelineConfig", "PipelineDecision", "TradePlan", "Verdict"]

PANEL_SIZE = 3
PANEL_AGREEMENT = 2


class Verdict(StrEnum):
    TRADE = "trade"
    NOT_MATERIAL = "not_material"
    BELOW_THRESHOLD = "below_threshold"
    NO_HORIZON = "no_horizon"
    PANEL_DISAGREES = "panel_disagrees"
    JUDGE_NO = "judge_no"
    ARBITER_VETO = "arbiter_veto"
    STAGE_ERROR = "stage_error"


@dataclass(frozen=True)
class PipelineConfig:
    triage_threshold: int  # the triage confidence (0-100) an item needs to reach the panel
    panel_enabled: bool = True
    arbiter_enabled: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.triage_threshold <= 100:
            raise ValueError("the triage threshold is a confidence from 0 to 100")


@dataclass(frozen=True)
class TradePlan:
    """What the models want; the risk engine decides whether and how much."""

    instrument: Instrument
    side: Side
    stop_pct: float
    target_pct: float
    hold_days: int
    confidence: int


@dataclass(frozen=True)
class PipelineDecision:
    event_id: str
    verdict: Verdict
    plan: TradePlan | None = None
    triage: TriageResult | None = None
    views: tuple[PanelView, ...] = ()
    judge: JudgeResult | None = None
    arbiter: ArbiterResult | None = None
    errors: tuple[str, ...] = ()


class DecisionPipeline:
    def __init__(
        self,
        config: PipelineConfig,
        triage: LlmStage[EventInput, TriageResult],
        panel: tuple[
            LlmStage[EventInput, PanelView],
            LlmStage[EventInput, PanelView],
            LlmStage[EventInput, PanelView],
        ],
        judge: LlmStage[JudgeInput, JudgeResult],
        arbiter: LlmStage[ArbiterInput, ArbiterResult] | None = None,
    ) -> None:
        if config.arbiter_enabled and arbiter is None:
            raise ValueError("the arbiter is switched on but none was given")
        self._config, self._triage, self._panel = config, triage, panel
        self._judge, self._arbiter = judge, arbiter

    async def decide(self, item: EventInput) -> PipelineDecision:
        event_id, at = item.event.event_id, item.decision_at
        triaged = await self._triage.run(item, at)
        if triaged.value is None:
            return PipelineDecision(event_id, Verdict.STAGE_ERROR, errors=_errors(triaged))
        triage = triaged.value
        stopped = self._screen(event_id, triage)
        if stopped is not None:
            return stopped
        views: tuple[PanelView, ...] = ()
        if self._config.panel_enabled:
            outcomes = await asyncio.gather(*(s.run(item, at) for s in self._panel))
            missing = [o for o in outcomes if o.value is None]
            if missing:
                return PipelineDecision(
                    event_id, Verdict.STAGE_ERROR, triage=triage, errors=_errors(*missing)
                )
            views = tuple(o.value for o in outcomes if o.value is not None)
        judged_input = JudgeInput(item, views)
        judged = await self._judge.run(judged_input, at)
        if judged.value is None:
            return PipelineDecision(
                event_id, Verdict.STAGE_ERROR, triage=triage, views=views, errors=_errors(judged)
            )
        judge = judged.value
        if not judge.trade or judge.instrument is None or judge.side is None:
            return PipelineDecision(
                event_id, Verdict.JUDGE_NO, triage=triage, views=views, judge=judge
            )
        if views and self._agreeing(views, judge.side) < PANEL_AGREEMENT:
            return PipelineDecision(
                event_id, Verdict.PANEL_DISAGREES, triage=triage, views=views, judge=judge
            )
        arbiter: ArbiterResult | None = None
        if self._config.arbiter_enabled and self._arbiter is not None:
            arbitrated = await self._arbiter.run(ArbiterInput(judged_input, judge), at)
            if arbitrated.value is None:
                return PipelineDecision(
                    event_id, Verdict.STAGE_ERROR, triage=triage, views=views, judge=judge,
                    errors=_errors(arbitrated),
                )  # fmt: skip
            arbiter = arbitrated.value
            if not arbiter.approve:
                return PipelineDecision(
                    event_id, Verdict.ARBITER_VETO, triage=triage, views=views, judge=judge,
                    arbiter=arbiter,
                )  # fmt: skip
        plan = TradePlan(
            judge.instrument, judge.side, judge.stop_pct, judge.target_pct, judge.hold_days,
            judge.confidence,
        )  # fmt: skip
        return PipelineDecision(event_id, Verdict.TRADE, plan, triage, views, judge, arbiter)

    def _screen(self, event_id: str, triage: TriageResult) -> PipelineDecision | None:
        if not triage.material:
            return PipelineDecision(event_id, Verdict.NOT_MATERIAL, triage=triage)
        if triage.horizon is Horizon.NONE:
            return PipelineDecision(event_id, Verdict.NO_HORIZON, triage=triage)
        if triage.confidence < self._config.triage_threshold:
            return PipelineDecision(event_id, Verdict.BELOW_THRESHOLD, triage=triage)
        return None

    @staticmethod
    def _agreeing(views: tuple[PanelView, ...], side: Side) -> int:
        return sum(1 for v in views if v.side is side)


def _errors(*outcomes: StageOutcome[Any]) -> tuple[str, ...]:
    return tuple(o.error or "unknown" for o in outcomes)
