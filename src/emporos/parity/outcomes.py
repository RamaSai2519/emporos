"""What became of each signal, on each side, derived from the records and the backtest journal.

Paper: an approved signal has one root order (its cancel-then-replace children carry
`parent_order_id`); fills come from `executions`, a risk refusal from `risk_events`.
Backtest: an order is tied to its signal by the journal, fills to orders by client tag.
The two builders answer with the same `SideOutcome`, so the classifier never cares which side it
is looking at.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from emporos.backtest.journal import ExpectedFill, RecordingSink
from emporos.domain.money import Money
from emporos.domain.signals import SignalKind
from emporos.parity.inputs import PaperRunData
from emporos.parity.latency import LatencyProfiler
from emporos.parity.models import (
    ParityStatus,
    SideOutcome,
    SignalOrigin,
    SignalPoint,
)
from emporos.parity.slippage import SlippageMeasure
from emporos.persistence.records import (
    ExecutionRecord,
    OrderEventRecord,
    OrderRecord,
    SignalRecord,
)
from emporos.signals.quotes import DecisionQuote

_ZERO = Money.zero()


def _average(fills: Sequence[tuple[int, Money]]) -> Money | None:
    quantity = sum(q for q, _ in fills)
    if not quantity:
        return None
    return Money(sum((p.amount * q for q, p in fills), Decimal(0)) / quantity)


class PaperSignals:
    """The strategy's signals as recorded, in the shape the matcher wants."""

    def points(self, data: PaperRunData) -> tuple[SignalPoint, ...]:
        points: list[SignalPoint] = []
        for number, record in enumerate(
            sorted(data.signals, key=lambda s: (s.ts, s.sequence or 0))
        ):
            if (
                record.kind is None
                or record.side is None
                or record.price is None
                or record.quantity is None
            ):
                continue  # not a strategy signal (or a row too old to say what it was)
            points.append(
                SignalPoint(
                    SignalOrigin.PAPER,
                    record.id,
                    record.sequence or number + 1,
                    record.instrument_id,
                    SignalKind(record.kind),
                    record.side,
                    record.ts,
                    record.price,
                    record.quantity,
                )  # fmt: skip
            )
        return tuple(points)


class PaperOutcomes:
    def __init__(
        self, latency: LatencyProfiler | None = None, slippage: SlippageMeasure | None = None
    ) -> None:
        self._latency = latency or LatencyProfiler()
        self._slippage = slippage or SlippageMeasure()

    def by_signal(self, data: PaperRunData) -> dict[str, SideOutcome]:
        orders_of: dict[str, list[OrderRecord]] = defaultdict(list)
        for order in data.orders:
            if order.signal_id is not None:
                orders_of[order.signal_id].append(order)
        fills_of: dict[str, list[ExecutionRecord]] = defaultdict(list)
        for execution in data.executions:
            fills_of[execution.order_id].append(execution)
        events_of: dict[str, list[OrderEventRecord]] = defaultdict(list)
        for event in data.order_events:
            events_of[event.order_id].append(event)
        rejected = {e.signal_id for e in data.risk_events if e.signal_id is not None}
        return {
            s.id: self._outcome(s, orders_of[s.id], fills_of, events_of, s.id in rejected)
            for s in data.signals
        }

    def _outcome(
        self,
        signal: SignalRecord,
        orders: list[OrderRecord],
        fills_of: dict[str, list[ExecutionRecord]],
        events_of: dict[str, list[OrderEventRecord]],
        rejected: bool,
    ) -> SideOutcome:
        roots = [o for o in orders if o.parent_order_id is None]
        executions = [x for o in orders for x in fills_of.get(o.id, ())]
        average = _average([(x.quantity, x.price) for x in executions])
        charges = Money(sum((x.fees.amount for x in executions if x.fees), Decimal(0)))
        quote = self._quote(signal)
        slippage = None
        if average is not None and signal.side is not None and signal.price is not None:
            slippage = self._slippage.measure(signal.side, average, quote, signal.price)
        return SideOutcome(
            ordered_quantity=sum(o.quantity for o in roots),
            filled_quantity=sum(x.quantity for x in executions),
            average_fill_price=average,
            charges=charges,
            rejected_by_risk=rejected,
            latency=self._latency.profile(signal.ts, orders, events_of, executions),
            slippage=slippage,
        )

    @staticmethod
    def _quote(signal: SignalRecord) -> DecisionQuote | None:
        if signal.quote_ts is None or signal.quote_source is None:
            return None
        if signal.quote_ltp is None and signal.quote_bid is None and signal.quote_ask is None:
            return None
        return DecisionQuote(
            signal.quote_ts, signal.quote_source,
            signal.quote_ltp, signal.quote_bid, signal.quote_ask,
        )  # fmt: skip


class BacktestSignals:
    def points(self, journal: RecordingSink) -> tuple[SignalPoint, ...]:
        return tuple(
            SignalPoint(
                SignalOrigin.BACKTEST,
                f"bt-{e.sequence}",
                e.sequence,
                e.signal.instrument_id,
                e.signal.kind,
                e.signal.side,
                e.signal.ts,
                e.signal.limit_price,
                e.signal.quantity,
                e.system,
            )  # fmt: skip
            for e in journal.signals
        )


class BacktestOutcomes:
    def __init__(self, slippage: SlippageMeasure | None = None) -> None:
        self._slippage = slippage or SlippageMeasure()

    def by_ref(self, journal: RecordingSink) -> dict[str, SideOutcome]:
        fills_by_tag: dict[str, list[ExpectedFill]] = defaultdict(list)
        for f in journal.fills:
            fills_by_tag[f.fill.tag].append(f)
        outcomes: dict[str, SideOutcome] = {}
        for expected in journal.signals:
            orders = [o for o in journal.orders if o.signal is expected.signal]
            fills = [f for o in orders for f in fills_by_tag.get(o.request.tag, ())]
            average = _average([(f.fill.quantity, f.fill.price) for f in fills])
            charges = Money(sum((f.charges.total.amount for f in fills), Decimal(0)))
            slippage = None
            if average is not None:
                slippage = self._slippage.measure(
                    expected.signal.side, average, None, expected.signal.limit_price
                )
            outcomes[f"bt-{expected.sequence}"] = SideOutcome(
                ordered_quantity=sum(o.request.quantity for o in orders),
                filled_quantity=sum(f.fill.quantity for f in fills),
                average_fill_price=average,
                charges=charges,
                slippage=slippage,
            )
        return outcomes


class StatusClassifier:
    """Names how one matched (or unmatched) pair ended, with the reason in words."""

    def classify(
        self, paper: SideOutcome | None, backtest: SideOutcome | None
    ) -> tuple[ParityStatus, str]:
        if paper is None:
            return ParityStatus.BACKTEST_ONLY_SIGNAL, "the backtest signalled; paper did not"
        if paper.rejected_by_risk:
            return ParityStatus.RISK_REJECTED, "paper's risk engine blocked the signal"
        if backtest is None:
            return ParityStatus.PAPER_ONLY_SIGNAL, "paper signalled; the backtest did not"
        if paper.partly_filled or backtest.partly_filled:
            side = "paper" if paper.partly_filled else "the backtest"
            return ParityStatus.PARTIAL, f"{side} filled only part of its order"
        paper_filled = paper.filled_quantity > 0
        backtest_filled = backtest.filled_quantity > 0
        if backtest_filled and not paper_filled:
            if paper.ordered_quantity > 0:
                return ParityStatus.MISSED, "paper's order went unfilled; the backtest filled"
            return ParityStatus.FILLED_BACKTEST_NOT_PAPER, "paper never placed an order"
        if paper_filled and not backtest_filled:
            return ParityStatus.FILLED_PAPER_NOT_BACKTEST, "paper filled; the backtest did not"
        if paper_filled:
            return ParityStatus.MATCHED, "both sides filled"
        return ParityStatus.MATCHED, "neither side filled"
