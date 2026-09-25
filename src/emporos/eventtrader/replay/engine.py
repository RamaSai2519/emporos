"""The event-driven replay (EM-240, PROFIT_PLAN §12): events in order, each through a decider (the
LLM pipeline, or a control), the risk engine, the fills and the costs.

Decision time is the event's `usable_from` plus 2 minutes. The context is built as of that time. The
risk engine reviews the proposal against the book as of the moment the order is placed. A trade's
whole path is simulated when it is entered; the book then knows when it is open and realised."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import IST
from emporos.eventtrader.events import ContextBuilder, MarketEvent
from emporos.eventtrader.pipeline import PipelineDecision, TradePlan, Verdict
from emporos.eventtrader.replay.book import Book
from emporos.eventtrader.replay.costs import TradeCosts
from emporos.eventtrader.replay.fills import (
    EntryFill,
    EntryFiller,
    ExitFill,
    ExitSimulator,
    entry_time,
    price_level,
)
from emporos.eventtrader.replay.market import MarketData
from emporos.eventtrader.replay.records import Scenario, TradeLeg, TradeRecord
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.risk.models import EntryProposal, Product
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.eventtrader.stages.stages import EventInput
from emporos.research.scans.base import ScanExecution

__all__ = [
    "Decider",
    "FixedPosture",
    "OptionPlacer",
    "PostureSource",
    "ReplayEngine",
    "RunResult",
    "RunStats",
    "TokenMeter",
    "decision_input",
]

LATENCY = timedelta(minutes=2)


class Decider(Protocol):
    async def decide(self, item: EventInput) -> PipelineDecision: ...


class PostureSource(Protocol):
    def scale_for(self, day: date) -> Decimal:
        """The share of the risk budgets the day may use: 0 (hold), 0.6, 1.0."""
        ...


class TokenMeter(Protocol):
    def total_inr(self) -> Decimal:
        """Cumulative token cost so far, in rupees."""
        ...


class OptionPlacer(Protocol):
    """Places a bought call or put (swing horizon only). Returns the trade, or the reason it could
    not be placed. Injected because it needs the F&O chains."""

    def place(
        self,
        event: MarketEvent,
        plan: TradePlan,
        decision_at: datetime,
        book: Book,
        proposal_scale: Decimal,
    ) -> TradeRecord | str: ...  # fmt: skip


class FixedPosture:
    def __init__(self, scale: Decimal = Decimal(1)) -> None:
        self._scale = scale

    def scale_for(self, day: date) -> Decimal:
        return self._scale


class _NoTokens:
    def total_inr(self) -> Decimal:
        return Decimal(0)


@dataclass
class RunStats:
    events: int = 0
    verdicts: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)  # events that never reached a decision
    refused: Counter[str] = field(default_factory=Counter)  # by risk rule or placement reason
    no_entry: Counter[str] = field(default_factory=Counter)  # approved, but the order did not fill
    killed_at: datetime | None = None
    token_cost_by_day: dict[date, Decimal] = field(default_factory=lambda: defaultdict(Decimal))

    @property
    def trades_attempted(self) -> int:
        return self.verdicts[Verdict.TRADE.value]


@dataclass(frozen=True)
class RunResult:
    trades: tuple[TradeRecord, ...]
    stats: RunStats
    token_cost_inr: Decimal  # every call of the run, trades or not

    def net(self, scenario: Scenario) -> Decimal:
        """Total net P&L after costs and after the models' own token cost."""
        return sum((t.net(scenario) for t in self.trades), Decimal(0)) - self.token_cost_inr


def decision_input(event: MarketEvent, context: ContextBuilder) -> EventInput:
    """What the decider sees for an event: decided 2 minutes after it is usable, on the context as
    of that moment. Shared by the replay and the prefetch so they cannot differ."""
    decision_at = event.usable_from + LATENCY
    return EventInput(event, context.context(event, decision_at), decision_at)


class ReplayEngine:
    def __init__(
        self,
        decider: Decider,
        context: ContextBuilder,
        market: MarketData,
        risk: RiskEngine,
        costs: TradeCosts,
        execution: ScanExecution,
        posture: PostureSource | None = None,
        tokens: TokenMeter | None = None,
        options: OptionPlacer | None = None,
        starting_capital: Decimal = Decimal(100000),
    ) -> None:
        self._decider, self._context, self._market = decider, context, market
        self._risk, self._costs, self._execution = risk, costs, execution
        self._posture, self._tokens = posture or FixedPosture(), tokens or _NoTokens()
        self._options = options
        self._filler = EntryFiller(market, execution)
        self._exits = ExitSimulator(market, execution)
        self._capital = starting_capital

    async def run(self, events: Sequence[MarketEvent]) -> RunResult:
        book, stats = Book(self._capital, self._market), RunStats()
        start_tokens = self._tokens.total_inr()
        for event in events:
            stats.events += 1
            if stats.killed_at is not None:
                stats.skipped["track_stopped"] += 1
                continue
            await self._one(event, book, stats)
        return RunResult(book.trades, stats, self._tokens.total_inr() - start_tokens)

    async def _one(self, event: MarketEvent, book: Book, stats: RunStats) -> None:
        if not event.instrument_id:
            stats.skipped["market_wide"] += 1  # feeds the posture, never a trade
            return
        decision_at = event.usable_from + LATENCY
        snapshot = book.snapshot(decision_at, Decimal(1))
        if self._risk.kill_tripped(snapshot):
            book.close_all(decision_at)
            stats.killed_at = decision_at
            stats.skipped["track_stopped"] += 1
            return
        item = decision_input(event, self._context)
        before = self._tokens.total_inr()
        decision = await self._decider.decide(item)
        spent = self._tokens.total_inr() - before
        stats.verdicts[decision.verdict.value] += 1
        stats.token_cost_by_day[decision_at.astimezone(IST).date()] += spent
        if decision.verdict is not Verdict.TRADE or decision.plan is None:
            return
        self._enter(event, decision.plan, decision_at, spent, book, stats)

    def _enter(
        self,
        event: MarketEvent,
        plan: TradePlan,
        decision_at: datetime,
        token_cost: Decimal,
        book: Book,
        stats: RunStats,
    ) -> None:
        if plan.instrument in (Instrument.CALL, Instrument.PUT):
            self._enter_option(event, plan, decision_at, token_cost, book, stats)
            return
        product = Product.INTRADAY if plan.instrument is Instrument.CASH_INTRADAY else Product.SWING
        placed_at = entry_time(decision_at, self._market)
        if placed_at is None:
            stats.refused["no_later_session"] += 1
            return
        reference = self._market.last_close(event.instrument_id, placed_at)
        if reference is None:
            stats.refused["no_price"] += 1
            return
        stop = price_level(reference, plan.side, plan.stop_pct, against=True)
        target = price_level(reference, plan.side, plan.target_pct, against=False)
        scale = self._posture.scale_for(placed_at.astimezone(IST).date())
        proposal = EntryProposal(
            event.instrument_id, product, plan.side, reference, stop, placed_at
        )
        verdict = self._risk.review(proposal, book.snapshot(placed_at, scale))
        if not verdict.approved or verdict.sized is None:
            for refusal in verdict.refusals:
                stats.refused[refusal.rule] += 1
            return
        sized = verdict.sized
        fill = self._filler.fill(event.instrument_id, plan.side, placed_at, sized.quantity)
        if not isinstance(fill, EntryFill):
            stats.no_entry[fill.value] += 1
            return
        exit_ = self._exit(event.instrument_id, product, plan, fill, stop, target)
        book.add(
            self._record(
                event, plan, product, sized.quantity, sized.risk, fill, exit_, stop, token_cost
            )
        )

    def _enter_option(
        self, event: MarketEvent, plan: TradePlan, decision_at: datetime, token_cost: Decimal,
        book: Book, stats: RunStats,
    ) -> None:  # fmt: skip
        if self._options is None:
            stats.refused["options_unavailable"] += 1
            return
        scale = self._posture.scale_for(decision_at.astimezone(IST).date())
        placed = self._options.place(event, plan, decision_at, book, scale)
        if isinstance(placed, str):
            stats.refused[placed] += 1
            return
        book.add(TradeRecord(**{**placed.__dict__, "token_cost_inr": token_cost}))

    def _exit(
        self, name: str, product: Product, plan: TradePlan, fill: EntryFill, stop: Decimal,
        target: Decimal,
    ) -> ExitFill:  # fmt: skip
        if product is Product.INTRADAY:
            return self._exits.intraday_exit(name, plan.side, fill, stop, target)
        return self._exits.swing_exit(name, plan.side, fill, stop, target, plan.hold_days)

    def _record(
        self, event: MarketEvent, plan: TradePlan, product: Product, quantity: int, risk: Decimal,
        fill: EntryFill, exit_: ExitFill, stop: Decimal, token_cost: Decimal,
    ) -> TradeRecord:  # fmt: skip
        move = (exit_.price - fill.reference) * quantity
        gross = move if plan.side is Side.LONG else -move
        leg = TradeLeg(
            event.instrument_id, plan.instrument, product, plan.side, quantity, fill.ts,
            fill.reference, exit_.ts, exit_.price,
        )  # fmt: skip
        return TradeRecord(
            event.event_id, event.instrument_id, event.symbol, plan.instrument, product, plan.side,
            quantity, fill.ts, fill.reference, exit_.ts, exit_.price, exit_.reason, stop, risk,
            gross, self._costs.cost(leg, Scenario.BENCHMARK),
            self._costs.cost(leg, Scenario.ADVERSE), token_cost,
        )  # fmt: skip
