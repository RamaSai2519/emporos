"""EM-158: two strategies, one account — per-strategy attribution, isolation, and ownership."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from emporos.backtest.feed import FeedWindow
from emporos.backtest.multi_engine import (
    MultiStrategyBacktestEngine,
    MultiStrategyBacktestSpec,
    ScopeError,
)
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.opportunity.allocator import AllocationConstraints
from emporos.opportunity.jev_experiment import JevRunSummary
from emporos.strategies.config import ResolvedStrategyConfig, SessionSettings
from tests.support.backtest import InMemoryCandles
from tests.support.backtest_engine import (
    SHORT_WORKED_DAY,
    WORKED_DAY,
    BuyThenSell,
    BuyThenSellParameters,
    FixedSchedule,
    FixedTicks,
    SellThenBuy,
    SellThenBuyParameters,
    bars,
    registry,
)
from tests.support.strategies import INSTRUMENT, OTHER_INSTRUMENT, T0, make_config

_GENEROUS = AllocationConstraints(
    production_capital=Money.of("5000"),
    max_simultaneous_positions=5,
    max_risk_per_trade=Money.of("500"),
    max_portfolio_risk=Money.of("2000"),
)


def _long_config(
    instrument: str, buy_at: int = 2, sell_at: int = 5, fail_at: int = 0
) -> ResolvedStrategyConfig:
    base = make_config(
        name="buy_then_sell",
        instruments=(instrument,),
        parameters=BuyThenSellParameters(buy_at=buy_at, sell_at=sell_at, fail_at=fail_at),
        timeframe=Timeframe.M5,
    )
    session = SessionSettings.model_validate(
        {"no_new_entries_after": "15:00", "square_off_at": "15:15"}
    )
    return base.model_copy(update={"session": session})


def _short_config(
    instrument: str, sell_at: int = 2, buy_at: int = 5, fail_at: int = 0
) -> ResolvedStrategyConfig:
    base = make_config(
        name="sell_then_buy",
        instruments=(instrument,),
        parameters=SellThenBuyParameters(sell_at=sell_at, buy_at=buy_at, fail_at=fail_at),
        timeframe=Timeframe.M5,
    )
    session = SessionSettings.model_validate(
        {"no_new_entries_after": "15:00", "square_off_at": "15:15"}
    )
    return base.model_copy(update={"session": session})


# A steady climb, alternating bar width, exactly the recipe `test_regime.py` uses to warm a
# `MarketRegimeClassifier` into TRENDING. The classifier needs atr_period + vol_window bars
# (14 + 60 = 74 at the default settings) before it says anything but None; 90 gives it headroom
# so both instruments are classified well before T0's session bars are evaluated.
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


def _engine(candles: list[Candle]) -> MultiStrategyBacktestEngine:
    reader = InMemoryCandles(candles)
    return MultiStrategyBacktestEngine(reader, registry(), FixedTicks(), lambda: FixedSchedule())


def _spec(
    configs: list[ResolvedStrategyConfig], constraints: AllocationConstraints = _GENEROUS
) -> MultiStrategyBacktestSpec:
    return MultiStrategyBacktestSpec(
        configs=configs,
        window=FeedWindow(T0 - timedelta(hours=1), T0 + timedelta(hours=10)),
        starting_cash=Money.of("100000"),
        constraints=constraints,
    )


class TestPerStrategyAttribution:
    async def test_two_strategies_on_different_instruments_are_attributed_separately(self) -> None:
        BuyThenSell.reset()
        SellThenBuy.reset()
        long_cfg = _long_config(INSTRUMENT)
        short_cfg = _short_config(OTHER_INSTRUMENT)
        candles = [
            *bars(WORKED_DAY, instrument_id=INSTRUMENT),
            *bars(SHORT_WORKED_DAY, instrument_id=OTHER_INSTRUMENT),
            *_trend_daily_bars(INSTRUMENT),
            *_trend_daily_bars(OTHER_INSTRUMENT),
        ]

        result = await _engine(candles).run(_spec([long_cfg, short_cfg]))

        assert len(result.strategies) == 2
        long_id = next(s.run_id for s in result.strategies if s.strategy_name == "buy_then_sell")
        short_id = next(s.run_id for s in result.strategies if s.strategy_name == "sell_then_buy")
        assert long_id != short_id

        assert len(result.trades) == 2  # one round trip per strategy
        by_instrument = {t.instrument_id: t for t in result.trades}
        assert by_instrument[INSTRUMENT].strategy_run_id == long_id
        assert by_instrument[OTHER_INSTRUMENT].strategy_run_id == short_id

        assert set(result.metrics.by_strategy) == {long_id, short_id}
        assert result.metrics.by_strategy[long_id].count == 1
        assert result.metrics.by_strategy[short_id].count == 1
        assert result.alerts == ()  # neither strategy faulted
        assert result.jev == JevRunSummary()  # no jev_filter configured: nothing to report


class TestIsolation:
    async def test_a_raising_strategy_does_not_stop_the_other_from_trading(self) -> None:
        BuyThenSell.reset()
        SellThenBuy.reset()
        bad = _long_config(INSTRUMENT, fail_at=1)  # raises on the very first bar
        good = _short_config(OTHER_INSTRUMENT)
        candles = [
            *bars(WORKED_DAY, instrument_id=INSTRUMENT),
            *bars(SHORT_WORKED_DAY, instrument_id=OTHER_INSTRUMENT),
            *_trend_daily_bars(INSTRUMENT),
            *_trend_daily_bars(OTHER_INSTRUMENT),
        ]

        result = await _engine(candles).run(_spec([bad, good]))

        assert len(result.alerts) == 1 and result.alerts[0][0] == "strategy_halted"
        good_id = next(s.run_id for s in result.strategies if s.strategy_name == "sell_then_buy")
        assert len(result.trades) == 1
        assert result.trades[0].instrument_id == OTHER_INSTRUMENT
        assert result.trades[0].strategy_run_id == good_id


class TestOwnershipAtForcedSquareOff:
    async def test_a_position_never_exited_is_force_closed_and_attributed_to_its_strategy(
        self,
    ) -> None:
        BuyThenSell.reset()
        SellThenBuy.reset()
        # sell_at=0: never emits its own exit, so only the broker's end-of-day square-off closes it.
        never_exits = _long_config(INSTRUMENT, buy_at=2, sell_at=0)
        candles = [*bars(WORKED_DAY, instrument_id=INSTRUMENT), *_trend_daily_bars(INSTRUMENT)]

        result = await _engine(candles).run(_spec([never_exits]))

        assert len(result.trades) == 1
        assert result.trades[0].strategy_run_id == result.strategies[0].run_id
        assert result.counters.forced_square_offs == 1


class TestScope:
    def test_strategies_that_disagree_on_timeframe_are_refused(self) -> None:
        long_cfg = _long_config(INSTRUMENT)
        mismatched = long_cfg.model_copy(update={"timeframe": Timeframe.M15})
        with pytest.raises(ScopeError, match="timeframe"):
            _spec([long_cfg, mismatched])

    def test_strategies_that_disagree_on_square_off_time_are_refused(self) -> None:
        long_cfg = _long_config(INSTRUMENT)
        other_session = SessionSettings.model_validate(
            {"no_new_entries_after": "15:00", "square_off_at": "15:20"}
        )
        mismatched = long_cfg.model_copy(update={"session": other_session})
        with pytest.raises(ScopeError, match="square_off_at"):
            _spec([long_cfg, mismatched])

    def test_at_least_one_strategy_is_required(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            _spec([])
