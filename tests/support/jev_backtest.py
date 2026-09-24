"""A one-day multi-strategy backtest scenario, shared by the Jev experiment tests: one strategy
that opens a single position, so a Jev rejection means exactly zero trades."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from emporos.backtest.feed import FeedWindow
from emporos.backtest.multi_engine import MultiStrategyBacktestEngine, MultiStrategyBacktestSpec
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.opportunity.allocator import AllocationConstraints
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from emporos.strategies.config import ResolvedStrategyConfig, SessionSettings
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import (
    WORKED_DAY,
    BuyThenSellParameters,
    FixedSchedule,
    FixedTicks,
    bars,
    registry,
)
from tests.support.strategies import INSTRUMENT, T0, make_config

_GENEROUS = AllocationConstraints(
    production_capital=Money.of("5000"),
    max_simultaneous_positions=5,
    max_risk_per_trade=Money.of("500"),
    max_portfolio_risk=Money.of("2000"),
)
# Same recipe as test_multi_engine.py: clears MarketRegimeClassifier's ~74-bar warm-up.
_TREND_DAYS = 90


def trend_daily_bars(instrument_id: str) -> list[Candle]:
    base = T0.astimezone(UTC).date() - timedelta(days=_TREND_DAYS)
    closes = [Decimal("1000") + Decimal("0.4") * i for i in range(1, _TREND_DAYS + 1)]
    widths = [Decimal("1.0") if i % 2 == 0 else Decimal("1.2") for i in range(_TREND_DAYS)]
    out = []
    for day, (close, width) in enumerate(zip(closes, widths, strict=True), start=1):
        half = width / 2
        ts = datetime(base.year, base.month, base.day, tzinfo=UTC) + timedelta(days=day)
        out.append(
            Candle(
                instrument_id=instrument_id, timeframe=Timeframe.D1, ts=ts,
                open=Money(close), high=Money(close + half), low=Money(close - half),
                close=Money(close), volume=1000,
            )
        )  # fmt: skip
    return out


def scenario_config() -> ResolvedStrategyConfig:
    # sell_at=0: the strategy never emits its own exit, so the only way a round trip closes is
    # the broker's forced end-of-day square-off — and the only way one OPENS, when Jev is
    # reviewing, is a confirmed entry. A rejected entry then means truly zero trades, not a
    # naked short from an exit signal fired with nothing open to exit (BuyThenSell counts bars,
    # not positions).
    base = make_config(
        name="buy_then_sell",
        instruments=(INSTRUMENT,),
        parameters=BuyThenSellParameters(buy_at=2, sell_at=0),
        timeframe=Timeframe.M5,
    )
    session = SessionSettings.model_validate(
        {"no_new_entries_after": "15:00", "square_off_at": "15:15"}
    )
    return base.model_copy(update={"session": session})


def scenario_candles() -> list[Candle]:
    return [*bars(WORKED_DAY, instrument_id=INSTRUMENT), *trend_daily_bars(INSTRUMENT)]


def scenario_engine(jev_filter: JevMetaDecisionFilter | None) -> MultiStrategyBacktestEngine:
    reader = InMemoryCandles(scenario_candles())
    return MultiStrategyBacktestEngine(
        reader, registry(), FixedTicks(), lambda: FixedSchedule(), jev_filter=jev_filter
    )


def scenario_spec() -> MultiStrategyBacktestSpec:
    return MultiStrategyBacktestSpec(
        configs=[scenario_config()],
        window=FeedWindow(T0 - timedelta(hours=1), T0 + timedelta(hours=10)),
        starting_cash=Money.of("100000"),
        constraints=_GENEROUS,
    )
