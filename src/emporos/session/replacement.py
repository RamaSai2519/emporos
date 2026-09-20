"""Adapts the risk engine to execution's replacement port (the composition layer's job).

Execution may not build or accept a raw `Signal`, and risk knows nothing of execution. The session
layer sits above both: it turns an execution `Replacement` into a fresh signal and has the risk
engine judge it like any other.
"""

from emporos.core.clock import Clock
from emporos.domain.signals import Signal, SignalKind
from emporos.execution.repricing import Replacement
from emporos.risk.approval import RiskDecision
from emporos.risk.engine import RiskEngine


class RiskReplacementReviewer:
    def __init__(self, risk: RiskEngine, clock: Clock) -> None:
        self._risk = risk
        self._clock = clock

    async def review(self, replacement: Replacement) -> RiskDecision:
        signal = Signal(
            strategy_run_id=replacement.strategy_run_id,
            instrument_id=replacement.instrument_id,
            kind=SignalKind(replacement.kind),
            side=replacement.side,
            order_type=replacement.order_type,
            quantity=replacement.quantity,
            limit_price=replacement.limit_price,
            ts=self._clock.now(),
            reason=replacement.reason,
        )
        return await self._risk.review(signal, replacement.key)
