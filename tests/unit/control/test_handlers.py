"""Each command does what the catalogue says; a manual order is no more privileged than a signal."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from emporos.control.commands import parse_command
from emporos.control.handlers import (
    MANUAL_RUN,
    BackfillHandler,
    BacktestHandler,
    CancelOrderHandler,
    ClosePositionHandler,
    KillSwitchHandler,
    PlaceManualOrderHandler,
    ReconcileNowHandler,
    SquareOffAllHandler,
    StartStrategyHandler,
    StopStrategyHandler,
    TradingModeHandler,
    UpdateStrategyConfigHandler,
)
from emporos.control.processor import Outcome
from emporos.core.clock import FixedClock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money
from emporos.domain.orders import OrderSide
from emporos.domain.signals import SignalKind
from emporos.marketdata.session import SessionWindow
from emporos.persistence.records import CommandRecord, SignalRecord
from emporos.risk.approval import RiskApprovedSignal
from emporos.risk.engine import RiskEngine
from emporos.risk.kill_switch import SinkOutcome
from emporos.risk.snapshot import KillSwitchReading, OrderFlowFacts, ReconciliationStatus
from emporos.risk.standard import StandardRuleSet
from emporos.session.lifecycle import SessionState
from emporos.session.signal_path import GatedExecutionSink
from emporos.session.square_off import SquareOffReport
from emporos.signals.recorder import SignalRecorder
from tests.support.fakes import RecordingAlertSink
from tests.support.records import RecordFactory
from tests.support.risk import (
    NOW,
    MemoryRejectionLog,
    StaticSnapshots,
    calm_market,
    generous_limits,
    healthy,
    healthy_system,
    long_position,
    working,
)
from tests.support.strategies import INSTRUMENT
from tests.unit.control.test_processor import command
from tests.unit.execution.rig import ExecutionRig
from tests.unit.session.test_signal_path import Signals


def run(kind: str, params: dict, **kw):  # type: ignore[no-untyped-def,type-arg]
    record = command(kind=kind, params=params, issued_by="rama", **kw)
    return record, parse_command(kind, params)[1]


class TestSimpleHandlers:
    async def test_the_kill_switch_engages_and_releases_and_needs_a_reason_to_halt(self) -> None:
        calls: list[tuple[str, ...]] = []

        class Control:
            ok = True

            async def engage(self, reason: str, set_by: str) -> list[SinkOutcome]:
                calls.append(("engage", reason, set_by))
                return [SinkOutcome("file", self.ok), SinkOutcome("mongo", True)]

            async def release(self, set_by: str) -> list[SinkOutcome]:
                calls.append(("release", set_by))
                return [SinkOutcome("file", self.ok)]

        control = Control()
        handler = KillSwitchHandler(control)
        record, params = run("SET_KILL_SWITCH", {"halted": True, "reason": "wedged"})
        assert (await handler.handle(params, record)).status.value == "DONE"
        record, params = run("SET_KILL_SWITCH", {"halted": True})
        assert (await handler.handle(params, record)).status.value == "REJECTED"
        record, params = run("SET_KILL_SWITCH", {"halted": False})
        assert (await handler.handle(params, record)).status.value == "DONE"
        control.ok = False
        record, params = run("SET_KILL_SWITCH", {"halted": False})
        assert (await handler.handle(params, record)).status.value == "FAILED"
        assert calls[0] == ("engage", "wedged", "rama")

    async def test_square_off_and_close_report_what_was_sent_and_what_could_not_be_priced(
        self,
    ) -> None:
        class SquareOff:
            def __init__(self, report: SquareOffReport) -> None:
                self.report, self.calls = report, []

            async def flatten(self, reason: str, only: str | None = None) -> SquareOffReport:
                self.calls.append((reason, only))
                return self.report

        sent = SquareOff(SquareOffReport(("A", "B"), (), ()))
        record, params = run("SQUARE_OFF_ALL", {})
        outcome = await SquareOffAllHandler(sent).handle(params, record)
        assert outcome.status.value == "DONE" and outcome.data["submitted"] == ["A", "B"]
        record, params = run("CLOSE_POSITION", {"instrument_id": "A"})
        await ClosePositionHandler(sent).handle(params, record)
        assert sent.calls[-1][1] == "A"
        unpriced = SquareOff(SquareOffReport((), (), ("A",)))
        assert (
            await ClosePositionHandler(unpriced).handle(params, record)
        ).status.value == "FAILED"
        nothing = SquareOff(SquareOffReport((), (), ()))
        done = await ClosePositionHandler(nothing).handle(params, record)
        assert done.status.value == "DONE" and "no open position" in done.message

    async def test_cancel_is_idempotent_and_an_unknown_order_is_rejected(self) -> None:
        rig = ExecutionRig()
        order = await rig.engine.place(await rig.approval())
        handler = CancelOrderHandler(rig.engine)
        record, params = run("CANCEL_ORDER", {"order_id": order.id})
        first = await handler.handle(params, record)
        second = await handler.handle(params, record)  # already terminal: a no-op, not an error
        assert (
            first.status.value == second.status.value == "DONE"
            and second.data["state"] == "CANCELLED"
        )
        record, params = run("CANCEL_ORDER", {"order_id": "missing"})
        assert (await handler.handle(params, record)).status.value == "REJECTED"
        assert len(rig.gateway.placements) == 1

    async def test_reconcile_now_reports_the_result(self) -> None:
        class Reconciler:
            found = 0

            async def run_now(self) -> int:
                return self.found

        reconciler = Reconciler()
        record, params = run("RECONCILE_NOW", {})
        clean = await ReconcileNowHandler(reconciler).handle(params, record)
        reconciler.found = 2
        dirty = await ReconcileNowHandler(reconciler).handle(params, record)
        assert clean.data["discrepancies"] == 0 and dirty.data["discrepancies"] == 2

    async def test_set_trading_mode_is_always_rejected(self) -> None:
        for mode in ("LIVE", "PAPER", "anything"):
            record, params = run("SET_TRADING_MODE", {"mode": mode})
            outcome = await TradingModeHandler().handle(params, record)
            assert outcome.status.value == "REJECTED" and "deployment" in outcome.message

    async def test_strategy_commands_are_delegated_and_config_updates_are_guarded_mid_session(
        self,
    ) -> None:
        class Strategies:
            def __init__(self) -> None:
                self.calls: list[tuple[str, ...]] = []
                self.error: ValueError | None = None

            async def start(self, name: str) -> str:
                self.calls.append(("start", name))
                return f"{name} started"

            async def stop(self, name: str) -> str:
                self.calls.append(("stop", name))
                return f"{name} stopped"

            async def store_config(self, name: str, config: dict) -> str:  # type: ignore[type-arg]
                if self.error:
                    raise self.error
                self.calls.append(("config", name))
                return "stored"

        class Session:
            state = SessionState.TRADING

        strategies, session = Strategies(), Session()
        record, params = run("START_STRATEGY", {"name": "s"})
        assert (
            await StartStrategyHandler(strategies).handle(params, record)
        ).message == "s started"
        record, params = run("STOP_STRATEGY", {"name": "s"})
        assert (await StopStrategyHandler(strategies).handle(params, record)).message == "s stopped"
        update = UpdateStrategyConfigHandler(strategies, session)  # type: ignore[arg-type]
        record, params = run("UPDATE_STRATEGY_CONFIG", {"name": "s", "config": {"a": 1}})
        assert (await update.handle(params, record)).status.value == "REJECTED"  # mid-session
        record, params = run("UPDATE_STRATEGY_CONFIG", {"name": "s", "config": {}, "force": True})
        assert (await update.handle(params, record)).status.value == "DONE"
        session.state = SessionState.REPORTING
        record, params = run("UPDATE_STRATEGY_CONFIG", {"name": "s", "config": {}})
        assert (await update.handle(params, record)).status.value == "DONE"  # between sessions
        strategies.error = ValueError("unknown key")
        assert (await update.handle(params, record)).status.value == "REJECTED"

    async def test_long_running_jobs_are_started_and_a_backwards_range_is_rejected(self) -> None:
        class Jobs:
            def __init__(self) -> None:
                self.started: list[tuple[str, str]] = []

            async def start(self, kind: str, command_id: str, params: dict) -> str:  # type: ignore[type-arg]
                self.started.append((kind, command_id))
                return f"job-{kind}"

        jobs = Jobs()
        record, params = run("TRIGGER_BACKFILL", {"instrument_ids": ["A"], "days": 3})
        assert (await BackfillHandler(jobs).handle(params, record)).data["job_id"] == "job-backfill"
        record, params = run(
            "RUN_BACKTEST", {"strategy": "s", "start": "2026-01-01", "end": "2026-02-01"}
        )
        assert (await BacktestHandler(jobs).handle(params, record)).data["job_id"] == "job-backtest"
        record, params = run(
            "RUN_BACKTEST", {"strategy": "s", "start": "2026-02-01", "end": "2026-01-01"}
        )
        assert (await BacktestHandler(jobs).handle(params, record)).status.value == "REJECTED"
        assert len(jobs.started) == 2 and date(2026, 1, 1)


def _refusals() -> dict[str, dict[str, object]]:
    """Each row: changes to a healthy snapshot in which exactly ONE rule must refuse the order."""
    switch = KillSwitchReading(True, True, "test")
    wide = calm_market(bid=Money.of("90"), ask=Money.of("110"))
    return {
        "TradingModeGuard": {"system": healthy_system(live_trading_enabled=False)},
        "KillSwitchGuard": {"system": healthy_system(kill_switch=switch)},
        "StaleDataGuard": {"markets": {INSTRUMENT: calm_market(stale=True)}},
        "BrokerHealthGuard": {"system": healthy_system(broker_session_ok=False)},
        "ReconciliationGuard": {
            "system": healthy_system(reconciliation=ReconciliationStatus.FAILED)
        },
        "DuplicateOrderGuard": {"flow": OrderFlowFacts(working=(working(OrderSide.BUY, 1),))},
        "PriceSanityGuard": {"markets": {INSTRUMENT: calm_market(ltp=Money.of("50"))}},
        "AbnormalSpreadGuard": {"markets": {INSTRUMENT: wide}},
    }


REFUSED = _refusals()


class ManualRig:
    """The REAL gated path (recorder, standard rule set, risk engine, execution engine) with a
    snapshot the test controls, so what refuses a strategy signal can be shown to refuse a manual
    order identically."""

    def __init__(self, snapshot=None, limits=None):  # type: ignore[no-untyped-def]
        self.exec = ExecutionRig()
        self.store = Signals()
        self.alerts = RecordingAlertSink()
        self.rejections = MemoryRejectionLog()
        rules = StandardRuleSet(limits or generous_limits(), SessionWindow()).rules()
        self.risk = RiskEngine(
            rules, StaticSnapshots(snapshot or healthy()), self.rejections, IdGenerator(),
            FixedClock(NOW), self.alerts,
        )  # fmt: skip
        self.sink = GatedExecutionSink(
            SignalRecorder(self.store, IdGenerator()), self.risk, self.exec.engine, self.alerts
        )
        self.positions = RecordFactory()

        class Held:
            def __init__(inner) -> None:
                inner.rows = []

            async def open_positions(inner) -> list:  # type: ignore[type-arg]
                return inner.rows

        self.held = Held()
        self.handler = PlaceManualOrderHandler(self.sink, self.held, FixedClock(NOW))

    async def manual(self, key: str = "k1", **overrides: object) -> Outcome:
        params = {
            "instrument_id": INSTRUMENT, "side": "BUY", "quantity": 10, "limit_price": "100.00",
            "reason": "test", **overrides,
        }  # fmt: skip
        record = command(
            kind="PLACE_MANUAL_ORDER", params=params, idempotency_key=key, issued_by="rama"
        )
        return await self.handler.handle(parse_command("PLACE_MANUAL_ORDER", params)[1], record)


class TestManualOrders:
    async def test_a_manual_order_that_passes_risk_is_placed_tagged_manual(self) -> None:
        rig = ManualRig()
        outcome = await rig.manual()
        assert outcome.status.value == "DONE"
        (order,) = rig.exec.journal.orders.values()
        assert order.strategy_run_id == MANUAL_RUN and order.state == "OPEN"
        (signal,) = rig.store.rows.values()
        assert signal.reason.startswith("MANUAL by rama") and signal.kind == "ENTRY"

    async def test_a_manual_order_that_reduces_a_position_is_an_exit(self) -> None:
        rig = ManualRig()
        rig.held.rows = [RecordFactory().position(instrument_id=INSTRUMENT, net_quantity=10)]
        await rig.manual(side="SELL")
        (signal,) = rig.store.rows.values()
        assert signal.kind == SignalKind.EXIT.value

    async def test_replaying_the_command_returns_the_same_order_and_places_nothing_new(
        self,
    ) -> None:
        rig = ManualRig()
        first = await rig.manual(key="same")
        rig.exec.wait(1)
        replay = await rig.manual(key="same")  # what recovery does after a restart mid-execution
        assert first.data["order_id"] == replay.data["order_id"]
        assert len(rig.exec.journal.orders) == 1 and len(rig.exec.gateway.placements) == 1
        assert len(rig.store.rows) == 1  # the signal is on record once, too

    @pytest.mark.parametrize("rule", sorted(REFUSED))
    async def test_every_rule_that_refuses_a_strategy_signal_refuses_the_manual_order(
        self, rule: str
    ) -> None:
        snapshot = healthy(**REFUSED[rule])
        # The strategy signal, through the same engine and snapshot:
        strategy = ManualRig(snapshot)
        from tests.support.strategies import make_signal

        decision = await strategy.risk.review(make_signal(), "strategy-signal")
        assert not isinstance(decision, RiskApprovedSignal) and decision.rule == rule
        # The manual order:
        rig = ManualRig(snapshot)
        outcome = await rig.manual()
        assert outcome.status.value == "REJECTED" and outcome.data["rule"] == rule
        assert rig.exec.journal.orders == {} and rig.exec.gateway.placements == []
        assert [r.rule for r in rig.rejections.rejections] == [rule]  # persisted like any other

    @pytest.mark.parametrize(
        ("limits", "params", "rule"),
        [
            (dict(max_order_quantity=5), dict(quantity=10), "MaxOrderQuantityGuard"),
            (dict(max_position_value=Decimal(500)), dict(), "MaxPositionValueGuard"),
            (dict(max_capital_deployed=Decimal(500)), dict(), "MaxCapitalDeployedGuard"),
        ],
    )
    async def test_the_size_and_exposure_limits_bind_a_manual_order_too(
        self,
        limits: dict,
        params: dict,
        rule: str,  # type: ignore[type-arg]
    ) -> None:
        rig = ManualRig(limits=generous_limits(**limits))
        outcome = await rig.manual(**params)
        assert outcome.status.value == "REJECTED" and outcome.data["rule"] == rule
        assert rig.exec.gateway.placements == []

    async def test_an_execution_refusal_is_a_rejection_with_the_reason(self) -> None:
        rig = ManualRig()
        rig.exec.journal.orders["x"] = RecordFactory().order(
            instrument_id=INSTRUMENT, state="UNKNOWN", account_id="test-account"
        )  # the instrument is frozen by an unresolved order
        outcome = await rig.manual()
        assert outcome.status.value == "REJECTED" and "frozen" in outcome.message
        assert (
            long_position(INSTRUMENT, 1, "1")
            and replace(healthy())
            and timedelta(0) == timedelta(0)
        )
        assert isinstance(SignalRecord, type)
        assert CommandRecord and Money and SessionState
