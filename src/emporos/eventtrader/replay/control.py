"""The coin-flip control (EM-240, PROFIT_PLAN §12.5): the same approved trades with the side chosen
by a fair coin, run through the same risk engine, fills and costs, many times.

If the models' direction were worthless, a coin would do as well: the real run's total net P&L
would sit in the middle of the coin runs'. p = (1 + runs at least as good as the real one) /
(1 + runs)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from random import Random

from emporos.eventtrader.events import MarketEvent
from emporos.eventtrader.pipeline import PipelineDecision, TradePlan, Verdict
from emporos.eventtrader.replay.engine import Decider, ReplayEngine
from emporos.eventtrader.replay.records import Scenario
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.eventtrader.stages.stages import EventInput

__all__ = ["CoinFlipControl", "CoinFlipDecider", "ControlResult", "plan_with_view"]

SEED = 20260925
RUNS = 1000


def plan_with_view(plan: TradePlan, view: Side) -> TradePlan:
    """The plan re-pointed at `view` (LONG: the name rises; SHORT: it falls), same stop, target and
    horizon. Intraday cash trades either way. On the swing horizon a rise is bought as it was (cash
    for a cash plan, else a call) and a fall is bought as a PUT: a cash swing short is not allowed,
    so a cash swing that flips to short becomes the put."""
    if plan.instrument is Instrument.CASH_INTRADAY:
        return replace(plan, side=view)
    if view is Side.SHORT:
        return replace(plan, instrument=Instrument.PUT, side=Side.LONG)
    instrument = (
        Instrument.CASH_SWING if plan.instrument is Instrument.CASH_SWING else Instrument.CALL
    )
    return replace(plan, instrument=instrument, side=Side.LONG)


class CoinFlipDecider:
    """The approved decisions, with every trade's side decided by the coin."""

    def __init__(self, decisions: Mapping[str, PipelineDecision], rng: Random) -> None:
        self._decisions, self._rng = decisions, rng

    async def decide(self, item: EventInput) -> PipelineDecision:
        decision = self._decisions[item.event.event_id]
        if decision.verdict is not Verdict.TRADE or decision.plan is None:
            return decision
        view = Side.LONG if self._rng.random() < 0.5 else Side.SHORT
        return replace(decision, plan=plan_with_view(decision.plan, view))


@dataclass(frozen=True)
class ControlResult:
    runs: int
    real_net: Decimal
    coin_nets: tuple[Decimal, ...]
    scenario: Scenario

    @property
    def at_least_as_good(self) -> int:
        return sum(1 for n in self.coin_nets if n >= self.real_net)

    @property
    def p_value(self) -> float:
        return (1 + self.at_least_as_good) / (1 + self.runs)

    @property
    def coin_mean(self) -> Decimal:
        return (
            sum(self.coin_nets, Decimal(0)) / len(self.coin_nets) if self.coin_nets else Decimal(0)
        )


class CoinFlipControl:
    def __init__(
        self,
        engine_for: Callable[[Decider], ReplayEngine],
        decisions: Mapping[str, PipelineDecision],
        runs: int = RUNS,
        seed: int = SEED,
    ) -> None:
        if runs < 1:
            raise ValueError("a control needs at least one run")
        self._engine_for, self._decisions = engine_for, decisions
        self._runs, self._seed = runs, seed

    async def run(
        self, events: Sequence[MarketEvent], real_net: Decimal, scenario: Scenario
    ) -> ControlResult:
        """Only the events whose decision was a trade matter to a coin run (the rest never touch
        the book), so only they are replayed, in their own order."""
        trades = [
            e for e in events
            if self._decisions[e.event_id].verdict is Verdict.TRADE
        ]  # fmt: skip
        rng = Random(self._seed)
        nets: list[Decimal] = []
        for _ in range(self._runs):
            engine = self._engine_for(CoinFlipDecider(self._decisions, rng))
            result = await engine.run(trades)
            nets.append(sum((t.net(scenario) for t in result.trades), Decimal(0)))
        return ControlResult(self._runs, real_net, tuple(nets), scenario)
