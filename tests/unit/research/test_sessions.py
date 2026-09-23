"""EM-180: session splitting and within-day return extraction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tests.unit.research.conftest import bar

from emporos.backtest.metrics.decimal_math import DecimalMath
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.research.horizons import Horizon
from emporos.research.sessions import (
    early_return,
    late_session_return,
    session_local_returns,
    split_sessions,
    subsequent_return,
)

D = Decimal


def _at(ts: datetime, close: Decimal | int) -> Candle:
    amount = Money.of(Decimal(close))
    return Candle(
        instrument_id="NSE:1", timeframe=Timeframe.M5, ts=ts,
        open=amount, high=amount, low=amount, close=amount, volume=1000,
    )  # fmt: skip


def test_split_sessions_groups_consecutive_bars_by_ist_day() -> None:
    day1_open = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)  # 09:15 IST
    day2_open = datetime(2026, 3, 3, 3, 45, tzinfo=UTC)
    bars = [
        _at(day1_open, 100), _at(day1_open + timedelta(minutes=5), 101),
        _at(day2_open, 102),
    ]  # fmt: skip

    sessions = split_sessions(bars)

    assert [day for day, _ in sessions] == [day1_open.date(), day2_open.date()]
    assert len(sessions[0][1]) == 2
    assert len(sessions[1][1]) == 1


def test_session_local_returns_starts_with_the_first_bars_own_move() -> None:
    day = [bar(0, 100, open_=98), bar(1, 102), bar(2, 101)]

    returns = session_local_returns(day)

    assert returns[0] == DecimalMath.divide(D(2), D(98))
    assert returns[1] == DecimalMath.divide(D(2), D(100))
    assert returns[2] == DecimalMath.divide(D(-1), D(102))


def test_early_return_compounds_the_first_horizon_bars() -> None:
    returns = [D("0.01"), D("0.01"), D("0.01")]
    horizon = Horizon(timedelta(minutes=10), bars=2)

    assert early_return(returns, horizon) == D("0.0201")  # 1.01*1.01 - 1


def test_early_return_is_none_without_enough_bars() -> None:
    returns = [D("0.01")]
    horizon = Horizon(timedelta(minutes=10), bars=2)

    assert early_return(returns, horizon) is None


def test_subsequent_return_starts_exactly_where_early_return_ended() -> None:
    returns = [D("0.01"), D("0.01"), D("0.02"), D("0.02")]
    start = Horizon(timedelta(minutes=10), bars=2)
    span = Horizon(timedelta(minutes=10), bars=2)

    assert subsequent_return(returns, start, span) == D("0.0404")  # 1.02*1.02 - 1


def test_subsequent_return_is_none_past_the_end() -> None:
    returns = [D("0.01"), D("0.01"), D("0.02")]
    start = Horizon(timedelta(minutes=10), bars=2)
    span = Horizon(timedelta(minutes=10), bars=2)

    assert subsequent_return(returns, start, span) is None


def test_late_session_return_covers_the_remainder_of_the_day() -> None:
    returns = [D("0.01"), D("0.01"), D("0.02"), D("-0.01")]
    start = Horizon(timedelta(minutes=10), bars=2)

    assert late_session_return(returns, start) == D("0.0098")  # 1.02*0.99 - 1


def test_late_session_return_is_none_with_nothing_left_in_the_day() -> None:
    returns = [D("0.01"), D("0.01")]
    start = Horizon(timedelta(minutes=10), bars=2)

    assert late_session_return(returns, start) is None
