"""How much of the time, and of the account, the strategy had at work.

* time in market = the share of equity-curve points at which any position was open;
* average exposure = mean of gross position value / equity over those same points;
* turnover = notional traded (every fill, both sides) / average daily equity, over the whole run,
  and per year (divided by the run's length in years)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emporos.backtest.metrics.decimal_math import ZERO, DecimalMath
from emporos.backtest.metrics.equity import DailyEquity
from emporos.backtest.portfolio import EquityPoint
from emporos.domain.money import Money


@dataclass(frozen=True)
class ExposureStatistics:
    time_in_market: Decimal | None
    average_exposure: Decimal | None


@dataclass(frozen=True)
class TurnoverStatistics:
    traded_notional: Money
    turnover: Decimal | None
    annualised_turnover: Decimal | None


class ExposureAnalyzer:
    def analyze(self, curve: Sequence[EquityPoint]) -> ExposureStatistics:
        if not curve:
            return ExposureStatistics(None, None)
        held = sum(1 for p in curve if p.open_positions > 0)
        shares = [
            DecimalMath.divide(p.gross_exposure.amount, p.equity.amount)
            for p in curve
            if p.equity.amount > ZERO
        ]
        return ExposureStatistics(
            time_in_market=DecimalMath.divide(Decimal(held), Decimal(len(curve))),
            average_exposure=DecimalMath.mean(shares) if shares else None,
        )


class TurnoverAnalyzer:
    def analyze(
        self, traded_notional: Money, days: Sequence[DailyEquity], years: Decimal
    ) -> TurnoverStatistics:
        if not days:
            return TurnoverStatistics(traded_notional, None, None)
        average_equity = DecimalMath.mean([d.equity for d in days])
        if average_equity <= ZERO or years <= ZERO:
            return TurnoverStatistics(traded_notional, None, None)
        turnover = DecimalMath.divide(traded_notional.amount, average_equity)
        return TurnoverStatistics(traded_notional, turnover, DecimalMath.divide(turnover, years))
