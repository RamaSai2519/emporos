"""One assembled execution stack over in-memory doubles, restartable like a real worker."""

from datetime import timedelta
from decimal import Decimal

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.marketable import MarketableLimit
from emporos.domain.signals import Signal
from emporos.execution.engine import ExecutionEngine
from emporos.execution.fills import FillProcessor, FillSynchroniser
from emporos.execution.pricing import MarketableLimitPricer
from emporos.execution.resolution import AbsencePolicy
from emporos.execution.state import OrderStateMachine
from emporos.portfolio.ledger import PositionCalculator
from emporos.risk.approval import RiskApprovedSignal
from emporos.risk.engine import RiskEngine
from tests.support.execution import (
    FaultGateway,
    FixedTicks,
    MemoryOrderJournal,
    RecordingLimiter,
    ZeroCosts,
)
from tests.support.fakes import RecordingAlertSink
from tests.support.risk import NOW, MemoryRejectionLog, ScriptedRule, StaticSnapshots, healthy
from tests.support.strategies import make_signal

ACCOUNT = "test-account"


class ExecutionRig:
    def __init__(
        self,
        error: Exception | None = None,
        absence: AbsencePolicy | None = None,
        buffer_bps: str = "0",
    ) -> None:
        self.clock = FixedClock(NOW)
        self.journal = MemoryOrderJournal()
        self.gateway = FaultGateway(self.journal, error)
        self.limiter = RecordingLimiter()
        self.absence = absence
        self.buffer_bps = buffer_bps
        self.engine = self.restart()
        self.processor = self.fill_processor()
        self.sync = FillSynchroniser(self.gateway, self.processor)
        self.risk = RiskEngine(
            [ScriptedRule("allow")],
            StaticSnapshots(healthy()),
            MemoryRejectionLog(),
            IdGenerator(),
            self.clock,
            RecordingAlertSink(),
        )

    def restart(self) -> ExecutionEngine:
        """A new engine over the SAME durable journal and broker: what a process restart is."""
        return ExecutionEngine(
            self.gateway,
            self.journal,
            self.limiter,
            self.clock,
            IdGenerator(),
            OrderStateMachine(),
            MarketableLimitPricer(MarketableLimit(Decimal(self.buffer_bps)), FixedTicks()),
            ACCOUNT,
            self.absence,
        )

    def fill_processor(self) -> FillProcessor:
        return FillProcessor(
            self.journal,
            ZeroCosts(),
            PositionCalculator(),
            OrderStateMachine(),
            self.clock,
            IdGenerator(),
            ACCOUNT,
        )

    async def approval(self, key: str = "signal") -> RiskApprovedSignal:
        return await self.approval_for(make_signal(), key)

    async def approval_for(self, signal: Signal, key: str) -> RiskApprovedSignal:
        decision = await self.risk.review(signal, key)
        assert isinstance(decision, RiskApprovedSignal)
        return decision

    def wait(self, seconds: int) -> None:
        self.clock.advance(timedelta(seconds=seconds))
