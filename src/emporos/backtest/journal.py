"""Backtest event journal (EM-185): what the engine actually decided, order by order and fill by
fill, exposed through an injected sink so `emporos.parity` can compare it against a paper run
without changing what the backtest computes.

    strategy signal ─▶ on_signal          (before the gate: every signal the strategy emitted)
    order accepted/rejected ─▶ on_order   (after the gate and pricing, whatever the exchange says)
    a bar fills a resting order ─▶ on_fill (with the charges the same cost model would apply)
    a round trip closes ─▶ on_trade        (the same `ClosedTrade` the backtest's result exposes)

`NoSink` is the default: a run without a sink is byte-identical to a run with one, because nothing
here can influence what the engine does — it only watches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from emporos.backtest.orders import Fill, SimEvent, SimOrderRequest
from emporos.backtest.portfolio import ClosedTrade
from emporos.domain.fees import ChargeBreakdown
from emporos.domain.signals import Signal


class BacktestEventSink(Protocol):
    def on_signal(self, signal: Signal, *, system: bool) -> None: ...

    def on_order(self, signal: Signal, request: SimOrderRequest, event: SimEvent) -> None: ...

    def on_fill(self, fill: Fill, charges: ChargeBreakdown) -> None: ...

    def on_trade(self, trade: ClosedTrade) -> None: ...


class NoSink:
    """The default: observes nothing, costs nothing."""

    def on_signal(self, signal: Signal, *, system: bool = False) -> None:
        return None

    def on_order(self, signal: Signal, request: SimOrderRequest, event: SimEvent) -> None:
        return None

    def on_fill(self, fill: Fill, charges: ChargeBreakdown) -> None:
        return None

    def on_trade(self, trade: ClosedTrade) -> None:
        return None


@dataclass(frozen=True)
class ExpectedSignal:
    """One signal the strategy (or the session's own square-off) produced, 1-based per run —
    the same "sequence" convention `SignalRecord.sequence` uses on the paper side."""

    sequence: int
    signal: Signal
    system: bool  # True for the session's own forced exit, never the strategy's own decision


@dataclass(frozen=True)
class ExpectedOrder:
    sequence: int
    signal: Signal
    request: SimOrderRequest
    event: SimEvent


@dataclass(frozen=True)
class ExpectedFill:
    sequence: int
    fill: Fill
    charges: ChargeBreakdown


@dataclass(frozen=True)
class ExpectedTrade:
    sequence: int
    trade: ClosedTrade


@dataclass
class RecordingSink:
    """Collects every event a run produced, in the order it happened: exactly what `NoSink`
    discards. A real run's journal is a few thousand entries; nothing here bounds it."""

    signals: list[ExpectedSignal] = field(default_factory=list)
    orders: list[ExpectedOrder] = field(default_factory=list)
    fills: list[ExpectedFill] = field(default_factory=list)
    trades: list[ExpectedTrade] = field(default_factory=list)

    def on_signal(self, signal: Signal, *, system: bool = False) -> None:
        self.signals.append(ExpectedSignal(len(self.signals) + 1, signal, system))

    def on_order(self, signal: Signal, request: SimOrderRequest, event: SimEvent) -> None:
        self.orders.append(ExpectedOrder(len(self.orders) + 1, signal, request, event))

    def on_fill(self, fill: Fill, charges: ChargeBreakdown) -> None:
        self.fills.append(ExpectedFill(len(self.fills) + 1, fill, charges))

    def on_trade(self, trade: ClosedTrade) -> None:
        self.trades.append(ExpectedTrade(len(self.trades) + 1, trade))
