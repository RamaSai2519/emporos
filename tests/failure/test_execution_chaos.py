"""EM-81 — the execution failure suite. The most important tests in the project.

The REAL execution stack (engine, fill processor, state machine, gateway) runs over every broker
in the contract harness — the in-memory reference, `AngelOneBroker` over a SmartAPI emulator, and
`PaperBroker` — with faults injected on the broker's side: a placement whose reply is lost, a
process that dies between writing the intent and calling the broker, fills redelivered, a whole
seeded session of all of them at once. The invariant in every case: one order per approved signal
at the broker, never two, never a silently lost fill.
"""

from __future__ import annotations

import contextlib
import random
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest

from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.marketable import MarketableLimit
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.execution.engine import ExecutionEngine
from emporos.execution.fills import FillProcessor, FillSynchroniser
from emporos.execution.gateway import BrokerOrderGateway
from emporos.execution.pricing import MarketableLimitPricer
from emporos.execution.resolution import AbsencePolicy
from emporos.execution.state import OrderStateMachine
from emporos.portfolio.ledger import PositionCalculator
from emporos.risk.approval import RiskApprovedSignal
from emporos.risk.engine import RiskEngine
from tests.contract.harnesses import HARNESS_FACTORIES, BrokerHarness
from tests.support.execution import FixedTicks, MemoryOrderJournal, RecordingLimiter, ZeroCosts
from tests.support.fakes import RecordingAlertSink
from tests.support.risk import NOW, MemoryRejectionLog, ScriptedRule, StaticSnapshots, healthy
from tests.support.strategies import make_signal

ACCOUNT = "chaos-account"
INSTRUMENT = "NSE:3045"
PRICE = "996.20"


@pytest.fixture(params=sorted(HARNESS_FACTORIES))
def world(request: pytest.FixtureRequest) -> Iterator[World]:
    yield World(HARNESS_FACTORIES[request.param]())


class World:
    """A durable journal and a broker that outlive any number of engine 'processes'."""

    def __init__(self, harness: BrokerHarness) -> None:
        self.h = harness
        self.clock = FixedClock(NOW)
        self.journal = MemoryOrderJournal()
        self.gateway = BrokerOrderGateway(harness.broker)
        self.risk = RiskEngine(
            [ScriptedRule("allow")], StaticSnapshots(healthy()), MemoryRejectionLog(),
            IdGenerator(), self.clock, RecordingAlertSink(),
        )  # fmt: skip
        self.fills = FillProcessor(
            self.journal, ZeroCosts(), PositionCalculator(), OrderStateMachine(), self.clock,
            IdGenerator(), ACCOUNT,
        )  # fmt: skip
        self.sync = FillSynchroniser(harness.broker, self.fills)

    def engine(self, absence: AbsencePolicy | None = None) -> ExecutionEngine:
        """A fresh process: nothing in memory, everything durable in journal and broker."""
        return ExecutionEngine(
            self.gateway, self.journal, RecordingLimiter(), self.clock, IdGenerator(),
            OrderStateMachine(), MarketableLimitPricer(MarketableLimit(Decimal(0)), FixedTicks()),
            ACCOUNT, absence or AbsencePolicy(confirmations=2, window=timedelta(seconds=10)),
            approval_lifetime=timedelta(minutes=5),
        )  # fmt: skip

    async def approval(self, key: str, side: OrderSide = OrderSide.BUY) -> RiskApprovedSignal:
        signal = make_signal(instrument_id=INSTRUMENT, price=PRICE, side=side, quantity=10)
        decision = await self.risk.review(signal, key)
        assert isinstance(decision, RiskApprovedSignal)
        return decision

    async def broker_tags(self) -> Counter[str | None]:
        return Counter(o.client_tag for o in await self.h.broker.get_order_book())


class TestAmbiguousPlacement:
    async def test_a_lost_reply_is_adopted_and_never_resent(self, world: World) -> None:
        approval = await world.approval("s1")
        world.h.lose_next_place_reply()
        order = await world.engine().place(approval)
        assert order.state == "UNKNOWN"
        assert sum((await world.broker_tags()).values()) == 1  # the broker DID take it
        (adopted,) = await world.engine().recover()
        assert adopted.state == "OPEN" and adopted.broker_order_id is not None
        again = await world.engine().place(approval)  # a retry of the same approval
        assert again == adopted
        assert sum((await world.broker_tags()).values()) == 1  # exactly one order at the broker

    async def test_the_instrument_stays_frozen_until_the_doubt_is_resolved(
        self, world: World
    ) -> None:
        world.h.lose_next_place_reply()
        await world.engine().place(await world.approval("s1"))
        with pytest.raises(ValueError, match="frozen"):
            await world.engine().place(await world.approval("s2"))
        await world.engine().recover()
        await world.engine().place(await world.approval("s2"))
        assert sum((await world.broker_tags()).values()) == 2


class TestCrashes:
    async def test_dying_after_the_broker_took_the_order_adopts_it_on_restart(
        self, world: World
    ) -> None:
        """The reply never came (the process died), but the broker holds the order."""
        approval = await world.approval("s1")
        world.h.lose_next_place_reply()
        placed = await world.engine().place(approval)  # intent durable, outcome unknown
        (recovered,) = await world.engine().recover()  # "the restarted process"
        assert recovered.id == placed.id and recovered.state == "OPEN"
        assert (await world.engine().place(approval)).id == placed.id
        assert sum((await world.broker_tags()).values()) == 1

    async def test_an_intent_the_broker_never_saw_is_rejected_after_the_window(
        self, world: World
    ) -> None:
        # Only meaningful for a broker book that starts empty: replay the crash on the intent alone.
        approval = await world.approval("s1")
        engine = world.engine()
        original_submit = world.gateway.submit

        async def die(request):  # type: ignore[no-untyped-def]
            raise ConnectionResetError("process killed")

        world.gateway.submit = die  # type: ignore[method-assign]
        placed = await engine.place(approval)
        world.gateway.submit = original_submit  # type: ignore[method-assign]
        assert placed.state == "UNKNOWN"
        assert sum((await world.broker_tags()).values()) == 0
        restarted = world.engine()
        for _ in range(2):
            await restarted.recover()
        world.clock.advance(timedelta(seconds=11))
        (rejected,) = await restarted.recover()
        assert rejected.state == "REJECTED"
        assert sum((await world.broker_tags()).values()) == 0
        # The strategy may now signal again — a NEW signal, a NEW order.
        await restarted.place(await world.approval("s1-again"))
        assert sum((await world.broker_tags()).values()) == 1


class TestFills:
    async def test_redelivered_fills_are_applied_exactly_once(self, world: World) -> None:
        order = await world.engine().place(await world.approval("s1"))
        world.h.fill(order.broker_order_id or "", 4, Money.of(PRICE))
        world.h.fill(order.broker_order_id or "", 6, Money.of(PRICE))
        for _ in range(4):  # a reconnect replays the whole trade book, repeatedly
            await world.sync.sync()
        assert len(world.journal.executions) == 2
        stored = world.journal.orders[order.id]
        assert (stored.state, stored.filled_quantity) == ("FILLED", 10)
        (position,) = world.journal.positions.values()
        assert position.net_quantity == 10

    async def test_a_fill_that_beats_the_cancel_is_kept_and_the_order_is_not_reopened(
        self, world: World
    ) -> None:
        engine = world.engine()
        order = await engine.place(await world.approval("s1"))
        world.h.fill(order.broker_order_id or "", 3, Money.of(PRICE))
        await engine.cancel(order.id)
        await world.sync.sync()
        settled = await engine.refresh(order.id)
        assert settled.filled_quantity == 3
        assert settled.state in ("CANCELLED", "PENDING_CANCEL", "OPEN", "PARTIALLY_FILLED")
        assert len(world.journal.executions) == 1


class TestSeededSession:
    """A whole session of signals with faults injected at random, replayed from a seed."""

    @pytest.mark.parametrize("seed", [1, 7, 42, 2026])
    async def test_a_chaotic_session_never_duplicates_an_order_or_loses_a_fill(
        self, world: World, seed: int
    ) -> None:
        rng = random.Random(seed)
        engine = world.engine()
        approvals: list[RiskApprovedSignal] = []
        for n in range(30):
            approval = await world.approval(f"sig-{n}")
            approvals.append(approval)
            world.clock.advance(timedelta(seconds=1))
            if rng.random() < 0.35:
                world.h.lose_next_place_reply()
            with contextlib.suppress(ValueError):  # frozen by an earlier doubt: try again later
                await engine.place(approval)
            if rng.random() < 0.25:
                engine = world.engine()  # the process dies and restarts
                await engine.recover()
            if rng.random() < 0.5:
                await engine.resolve_unresolved()
            for held in await world.h.broker.get_order_book():
                remaining = held.quantity - held.filled_quantity
                if remaining and rng.random() < 0.4:
                    world.h.fill(held.broker_order_id, min(remaining, rng.randint(1, 10)),
                                 Money.of(PRICE))  # fmt: skip
            if rng.random() < 0.5:
                await world.sync.sync()
            for approval_again in rng.sample(approvals, k=min(3, len(approvals))):
                with contextlib.suppress(ValueError):  # spurious retries of old approvals
                    await engine.place(approval_again)
        await engine.recover()
        await world.sync.sync()
        await world.sync.sync()

        broker_tags = await world.broker_tags()
        assert all(count == 1 for count in broker_tags.values()), broker_tags  # NO duplicates
        internal = world.journal.orders.values()
        assert len({o.idempotency_key for o in internal}) == len(list(internal))
        keys = Counter(o.signal_id for o in internal)
        assert all(count == 1 for count in keys.values())  # one order per approved signal
        sent_tags = {o.client_tag for o in await world.h.broker.get_order_book()}
        for order in internal:
            if order.state in ("OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED"):
                assert order.ordertag in sent_tags  # what we say the broker holds, it holds
        # Fills: the trade book and our executions agree exactly — none lost, none doubled.
        book = await world.h.broker.get_trade_book()
        assert {t.trade_id for t in book} == set(world.journal.executions)
        assert sum(t.quantity for t in book) == sum(o.filled_quantity for o in internal)
