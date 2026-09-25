"""Builders for swing tests: daily bars, series, a zero-cost schedule, scripted strategies."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from emporos.domain.candles import Candle, Timeframe
from emporos.domain.fees import FeeSchedule, TradeProduct
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money
from emporos.research.adjustments import AdjustmentLedger, PriceAdjuster
from emporos.research.swing.costs import CostScenario, SwingCostModel
from emporos.research.swing.data import (
    AsOfView,
    Quarantine,
    SwingDataset,
    SwingSeries,
    SwingSeriesFactory,
)
from emporos.research.swing.rules import DecisionContext, Holding, Intent

MONDAY = date(2026, 1, 5)
FREE = CostScenario("free", Decimal(1), Decimal(0))

ZERO_SCHEDULE = FeeSchedule(
    name="zero-delivery",
    effective_from=date(2026, 1, 1),
    brokerage_flat=Money.zero(),
    brokerage_percent=Decimal(0),
    brokerage_minimum=Money.zero(),
    stt_sell_percent=Decimal(0),
    exchange_transaction_percent={Exchange.NSE: Decimal(0)},
    sebi_per_crore=Money.zero(),
    stamp_duty_buy_percent=Decimal(0),
    gst_percent=Decimal(0),
    product=TradeProduct.DELIVERY,
)


def free_costs() -> SwingCostModel:
    return SwingCostModel(ZERO_SCHEDULE, FREE)


def sessions(count: int, start: date = MONDAY) -> list[date]:
    """`count` consecutive weekdays from `start` (no holidays)."""
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def bar(instrument_id: str, day: date, open_: str, close: str, volume: int = 1000) -> Candle:
    high, low = max(open_, close, key=Decimal), min(open_, close, key=Decimal)
    ts = datetime.fromisoformat(f"{day:%Y-%m-%d}T00:00:00+05:30").astimezone(UTC)
    return Candle(
        instrument_id, Timeframe.D1, ts, Money.of(open_), Money.of(high), Money.of(low),
        Money.of(close), volume,
    )  # fmt: skip


def series(
    instrument_id: str,
    days: Sequence[date],
    opens_closes: Sequence[tuple[str, str]],
    ledger: AdjustmentLedger | None = None,
    quarantine: Quarantine | None = None,
    volumes: Sequence[int] | None = None,
) -> SwingSeries:
    vols = volumes or [1000] * len(days)
    raw = [
        bar(instrument_id, d, o, c, v)
        for d, (o, c), v in zip(days, opens_closes, vols, strict=True)
    ]
    adjusted = PriceAdjuster(ledger or AdjustmentLedger()).adjust(instrument_id, raw)
    return SwingSeriesFactory(quarantine).build(adjusted)


def dataset(*all_series: SwingSeries) -> SwingDataset:
    return SwingDataset(all_series)


class Scripted:
    """A strategy that wants whatever `script(context)` says, and remembers every context."""

    def __init__(self, script: Callable[[DecisionContext], Sequence[Intent]]) -> None:
        self._script = script
        self.seen: list[DecisionContext] = []

    def desired(self, context: DecisionContext) -> Sequence[Intent]:
        self.seen.append(context)
        return self._script(context)


def hold(instrument_id: str, from_day: date, until_day: date) -> Scripted:
    """Wants `instrument_id` on decisions from `from_day` up to but not including `until_day`."""
    return Scripted(lambda c: [Intent(instrument_id)] if from_day <= c.day < until_day else [])


class QuarantineSet:
    def __init__(self, pairs: Mapping[str, Sequence[date]]) -> None:
        self._pairs = {(i, d) for i, days in pairs.items() for d in days}

    def is_quarantined(self, instrument_id: str, day: date) -> bool:
        return (instrument_id, day) in self._pairs


def holding(name: str, sessions_held: int = 0, price: str = "100", value: str = "20000",
            equity: str = "100000") -> Holding:  # fmt: skip
    return Holding(name, 0, MONDAY, sessions_held, Decimal(price), Decimal(value), Decimal(equity))


def context(
    data: SwingDataset,
    day: date,
    holdings: Sequence[Holding] = (),
    tradable: Sequence[str] | None = None,
    equity: str = "100000",
) -> DecisionContext:
    names = frozenset(tradable if tradable is not None else data.instrument_ids)
    return DecisionContext(
        day, data.calendar_index(day), AsOfView(data, day), {h.instrument_id: h for h in holdings},
        Decimal(equity), names,
    )  # fmt: skip


class Always:
    """A regime filter (or rebalance calendar) that says the same thing every time."""

    def __init__(self, on: bool) -> None:
        self._on = on

    def is_on(self, context: DecisionContext) -> bool:
        return self._on

    def is_rebalance(self, view: AsOfView) -> bool:
        return self._on
