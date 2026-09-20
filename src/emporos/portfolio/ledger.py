"""Pure average-cost position accounting from immutable persisted executions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.persistence.records import ExecutionRecord, PositionRecord


class PositionCalculator:
    """Apply one fill to a position, including reductions, flat closes and reversals."""

    def apply(self, before: PositionRecord, fill: ExecutionRecord) -> PositionRecord:
        if fill.instrument_id != before.instrument_id or fill.account_id != before.account_id:
            raise ValueError("fill does not belong to the position")
        if fill.quantity <= 0 or fill.price <= Money.zero():
            raise ValueError("fill quantity and price must be positive")
        fees = fill.fees or Money.zero()
        if fees < Money.zero():
            raise ValueError("fees cannot be negative")
        signed = fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
        net = before.net_quantity
        after = net + signed
        gross = before.gross_realised_pnl
        if gross is None:
            gross = before.realised_pnl + (before.fees or Money.zero())
        if net == 0 or (net > 0) == (signed > 0):
            average = Money(
                (before.average_price.amount * abs(net) + fill.price.amount * fill.quantity)
                / abs(after)
            )
        else:
            gross = gross + (fill.price - before.average_price).times(
                min(abs(net), fill.quantity) * (1 if net > 0 else -1)
            )
            average = (
                Money.zero()
                if after == 0
                else before.average_price
                if (after > 0) == (net > 0)
                else fill.price
            )
        total_fees = (before.fees or Money.zero()) + fees
        return before.model_copy(
            update={
                "net_quantity": after,
                "average_price": average,
                "gross_realised_pnl": gross,
                "fees": total_fees,
                "realised_pnl": gross - total_fees,
                "updated_at": fill.ts,
            }
        )


@dataclass(frozen=True)
class PortfolioValuation:
    realised: Money
    unrealised: Money | None
    fees: Money
    missing_marks: tuple[str, ...]


class PortfolioValuator:
    """Never substitute cost price for a missing market mark in reported P&L."""

    def value(
        self, positions: Sequence[PositionRecord], marks: Mapping[str, Money]
    ) -> PortfolioValuation:
        realised, unrealised, fees = Money.zero(), Money.zero(), Money.zero()
        missing: list[str] = []
        for position in positions:
            realised = realised + position.realised_pnl
            fees = fees + (position.fees or Money.zero())
            if position.net_quantity:
                mark = marks.get(position.instrument_id)
                if mark is None:
                    missing.append(position.instrument_id)
                else:
                    unrealised = unrealised + (mark - position.average_price).times(
                        position.net_quantity
                    )
        return PortfolioValuation(
            realised, None if missing else unrealised, fees, tuple(sorted(missing))
        )
