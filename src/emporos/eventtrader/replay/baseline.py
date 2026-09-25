"""The triage-only baseline (EM-240, a DIAGNOSTIC, never a gate).

Every event triage would have sent on (material, a horizon, confidence at the variant's threshold)
is traded in triage's direction: up is long, down is short, and a swing short becomes the put. No model
picks a stop or target: for each horizon the baseline uses the MEDIAN stop %, target % (and hold
days) of the variant's own judge-approved trades, one number per horizon. It reads the variant's
recorded triage answers, so it makes no call and carries no token cost; the risk engine, the fills
and the costs are the run's own."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from emporos.eventtrader.pipeline import PipelineDecision, TradePlan, Verdict
from emporos.eventtrader.stages.models import Horizon, Instrument, Side, TriageDirection
from emporos.eventtrader.stages.stages import EventInput

__all__ = ["HorizonRule", "TriageOnlyBaseline", "horizon_rules"]

_SWING = (Instrument.CASH_SWING, Instrument.CALL, Instrument.PUT)


@dataclass(frozen=True)
class HorizonRule:
    stop_pct: float
    target_pct: float
    hold_days: int


def horizon_rules(decisions: Iterable[PipelineDecision]) -> dict[Horizon, HorizonRule]:
    """Medians over the approved trades of each horizon; a horizon with no trades has no rule."""
    plans = [d.plan for d in decisions if d.verdict is Verdict.TRADE and d.plan is not None]
    rules: dict[Horizon, HorizonRule] = {}
    for horizon, wanted in (
        (Horizon.INTRADAY, [p for p in plans if p.instrument is Instrument.CASH_INTRADAY]),
        (Horizon.SWING, [p for p in plans if p.instrument in _SWING]),
    ):
        if wanted:
            hold = round(statistics.median(p.hold_days for p in wanted))
            rules[horizon] = HorizonRule(
                statistics.median(p.stop_pct for p in wanted),
                statistics.median(p.target_pct for p in wanted),
                0 if horizon is Horizon.INTRADAY else max(1, hold),
            )
    return rules


class TriageOnlyBaseline:
    """A `Decider` over the variant's recorded decisions."""

    def __init__(
        self, decisions: Mapping[str, PipelineDecision], threshold: int,
        rules: Mapping[Horizon, HorizonRule] | None = None,
    ) -> None:  # fmt: skip
        self._decisions = dict(decisions)
        self._threshold = threshold
        self._rules = dict(rules) if rules is not None else horizon_rules(decisions.values())

    @property
    def rules(self) -> dict[Horizon, HorizonRule]:
        return dict(self._rules)

    async def decide(self, item: EventInput) -> PipelineDecision:
        event_id = item.event.event_id
        recorded = self._decisions.get(event_id)
        triage = recorded.triage if recorded else None
        if triage is None or not triage.material:
            return PipelineDecision(event_id, Verdict.NOT_MATERIAL, triage=triage)
        if triage.horizon is Horizon.NONE or triage.direction is TriageDirection.NONE:
            return PipelineDecision(event_id, Verdict.NO_HORIZON, triage=triage)
        if triage.confidence < self._threshold:
            return PipelineDecision(event_id, Verdict.BELOW_THRESHOLD, triage=triage)
        rule = self._rules.get(triage.horizon)
        if rule is None:
            return PipelineDecision(event_id, Verdict.NO_HORIZON, triage=triage)
        long = triage.direction is TriageDirection.UP
        if triage.horizon is Horizon.INTRADAY:
            instrument, side = Instrument.CASH_INTRADAY, Side.LONG if long else Side.SHORT
        else:
            instrument, side = (Instrument.CASH_SWING if long else Instrument.PUT), Side.LONG
        plan = TradePlan(
            instrument, side, rule.stop_pct, rule.target_pct, rule.hold_days, triage.confidence
        )
        return PipelineDecision(event_id, Verdict.TRADE, plan, triage=triage)
