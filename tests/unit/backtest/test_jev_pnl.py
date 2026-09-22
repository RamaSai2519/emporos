"""EM-162: Jev-on vs Jev-off compared through actual fills, drawdown and attribution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from emporos.backtest.feed import FeedWindow
from emporos.backtest.jev_pnl import JevOnOffBacktestExperiment
from emporos.backtest.multi_engine import MultiStrategyBacktestEngine, MultiStrategyBacktestSpec
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.jev.config import JevConfig
from emporos.jev.models import CONFIRM, CONFIRMATION, REJECT, JevDecision, JevRequest
from emporos.opportunity.allocator import AllocationConstraints
from emporos.opportunity.jev_filter import JevMetaDecisionFilter
from emporos.strategies.config import ResolvedStrategyConfig, SessionSettings
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import (
    WORKED_DAY,
    BuyThenSell,
    BuyThenSellParameters,
    FixedSchedule,
    FixedTicks,
    bars,
    registry,
)
from tests.support.strategies import INSTRUMENT, T0, make_config

DECIDED_AT = datetime(2026, 1, 5, 3, 46, tzinfo=UTC)
_GENEROUS = AllocationConstraints(
    production_capital=Money.of("5000"),
    max_simultaneous_positions=5,
    max_risk_per_trade=Money.of("500"),
    max_portfolio_risk=Money.of("2000"),
)
# Same recipe as test_multi_engine.py: clears MarketRegimeClassifier's ~74-bar warm-up.
_TREND_DAYS = 90


def _trend_daily_bars(instrument_id: str) -> list[Candle]:
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


def _config() -> ResolvedStrategyConfig:
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


def _candles() -> list[Candle]:
    return [*bars(WORKED_DAY, instrument_id=INSTRUMENT), *_trend_daily_bars(INSTRUMENT)]


def _engine(jev_filter: JevMetaDecisionFilter | None) -> MultiStrategyBacktestEngine:
    reader = InMemoryCandles(_candles())
    return MultiStrategyBacktestEngine(
        reader, registry(), FixedTicks(), lambda: FixedSchedule(), jev_filter=jev_filter
    )


def _spec() -> MultiStrategyBacktestSpec:
    return MultiStrategyBacktestSpec(
        configs=[_config()],
        window=FeedWindow(T0 - timedelta(hours=1), T0 + timedelta(hours=10)),
        starting_cash=Money.of("100000"),
        constraints=_GENEROUS,
    )


class _AlwaysConfirms:
    async def decide(self, request: JevRequest) -> JevDecision:
        return JevDecision(
            decision=CONFIRM, confidence=Decimal("0.9"), provider="test", model="test-model",
            config_version=None, requested_at=DECIDED_AT, latency_ms=42, tokens_used=100,
        )  # fmt: skip


class _AlwaysRejects:
    async def decide(self, request: JevRequest) -> JevDecision:
        return JevDecision(
            decision=REJECT, confidence=Decimal("0.9"), provider="test", model="test-model",
            config_version=None, requested_at=DECIDED_AT, latency_ms=17, tokens_used=55,
        )  # fmt: skip


class TestJevOnOffBacktestExperiment:
    async def test_a_confirming_jev_changes_nothing_but_its_own_reported_cost(self) -> None:
        BuyThenSell.reset()
        treatment_filter = JevMetaDecisionFilter(
            _AlwaysConfirms(), JevConfig(enabled=True, mode=CONFIRMATION)
        )
        experiment = JevOnOffBacktestExperiment(_engine(None), _engine(treatment_filter))

        comparison = await experiment.run(_spec())

        assert comparison.trade_count_delta == 0
        assert comparison.net_pnl_delta == Money.zero()
        assert comparison.baseline.jev.reviews == 0
        assert comparison.treatment.jev.reviews == 1
        assert comparison.treatment.jev.latency_ms == 42
        assert comparison.treatment.jev.tokens == 100
        assert comparison.treatment.jev.rejections == 0

    async def test_a_rejecting_jev_stops_the_trade_and_the_pnl_reflects_it(self) -> None:
        BuyThenSell.reset()
        treatment_filter = JevMetaDecisionFilter(
            _AlwaysRejects(), JevConfig(enabled=True, mode=CONFIRMATION)
        )
        experiment = JevOnOffBacktestExperiment(_engine(None), _engine(treatment_filter))

        comparison = await experiment.run(_spec())

        assert comparison.baseline.metrics.trades.count == 1
        assert comparison.treatment.metrics.trades.count == 0
        assert comparison.trade_count_delta == -1
        assert comparison.treatment.jev.rejections == 1
        # the baseline paid real costs on its one trade the rejected treatment never took
        assert comparison.net_pnl_delta != Money.zero()
