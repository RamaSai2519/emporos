"""The kill switch (EM-74): sources, monitor, control, and the promise that a halt stops new
orders within one poll interval even with the database down."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from emporos.core.clock import FixedClock
from emporos.core.config import Settings
from emporos.core.errors import ConfigurationError
from emporos.core.ids import IdGenerator
from emporos.domain.trading_mode import TradingMode
from emporos.risk.approval import RiskApprovedSignal, RiskRejection
from emporos.risk.assembly import (
    MonitoredSystemFacts,
    SnapshotAssembler,
    UnreportedBrokerHealth,
    UnreportedReconciliation,
)
from emporos.risk.engine import RiskEngine
from emporos.risk.kill_switch import (
    FileSentinelKillSwitch,
    KillSwitchControl,
    KillSwitchMonitor,
    SourceReading,
)
from emporos.risk.rules.system import KillSwitchGuard
from emporos.risk.snapshot import AccountFacts, InstrumentMarket, OrderFlowFacts
from tests.support.fakes import AdvancingSleeper, RecordingAlertSink
from tests.support.risk import NOW, MemoryRejectionLog
from tests.support.strategies import make_signal

POLL = 5.0


class ScriptedSwitch:
    """A source/sink whose state, and failures, the test controls."""

    def __init__(self, name: str, halted: bool = False, error: Exception | None = None) -> None:
        self.name = name
        self.halted = halted
        self.reason = ""
        self.error = error
        self.hang = False
        self.reads = 0

    async def read(self) -> SourceReading:
        self.reads += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return SourceReading(self.halted, self.reason)

    async def engage(self, reason: str, set_by: str, at: object) -> None:
        if self.error is not None:
            raise self.error
        self.halted, self.reason = True, reason

    async def release(self, set_by: str, at: object) -> None:
        if self.error is not None:
            raise self.error
        self.halted, self.reason = False, ""


def monitor(*sources: ScriptedSwitch | FileSentinelKillSwitch, **kw: float) -> tuple[
    KillSwitchMonitor, FixedClock, AdvancingSleeper
]:  # fmt: skip
    clock = FixedClock(NOW)
    sleeper = AdvancingSleeper(clock)
    return KillSwitchMonitor(sources, clock, sleeper, **kw), clock, sleeper  # type: ignore[arg-type]


class SteppedSleeper:
    """A `Sleeper` that waits until the test releases it, then advances the clock by the wait: the
    test decides exactly when each poll happens."""

    def __init__(self, clock: FixedClock) -> None:
        self._clock = clock
        self._asleep = asyncio.Event()
        self._release = asyncio.Event()
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._asleep.set()
        await self._release.wait()
        self._release.clear()
        self._clock.advance(timedelta(seconds=seconds))

    async def until_sleeping(self) -> None:
        await self._asleep.wait()
        self._asleep.clear()

    async def tick(self) -> None:
        """Let the loop wake, poll, and go back to sleep (or finish)."""
        self._release.set()
        for _ in range(20):
            await asyncio.sleep(0)
        self._asleep.clear()


class TestFileSentinel:
    async def test_no_file_means_not_halted(self, tmp_path: Path) -> None:
        assert await FileSentinelKillSwitch(tmp_path / "HALT").read() == SourceReading(False)

    async def test_engaging_creates_the_file_with_the_reason_and_who_set_it(
        self, tmp_path: Path
    ) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "nested" / "dir" / "HALT")
        await sentinel.engage("runaway strategy", "rama", NOW)

        reading = await sentinel.read()
        assert reading.halted and reading.reason.startswith("runaway strategy")
        assert "set by rama" in sentinel.path.read_text()

    async def test_releasing_removes_the_file_and_is_harmless_when_already_clear(
        self, tmp_path: Path
    ) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        await sentinel.engage("x", "rama", NOW)
        await sentinel.release("rama", NOW)
        await sentinel.release("rama", NOW)
        assert (await sentinel.read()).halted is False

    async def test_engaging_leaves_no_temporary_files_behind(self, tmp_path: Path) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        await sentinel.engage("a", "rama", NOW)
        await sentinel.engage("b", "rama", NOW)
        assert [p.name for p in tmp_path.iterdir()] == ["HALT"]

    async def test_a_failed_write_leaves_neither_a_sentinel_nor_a_temporary_file(
        self, tmp_path: Path
    ) -> None:
        class Boom:
            def __str__(self) -> str:
                raise RuntimeError("cannot render the reason")

        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        with pytest.raises(RuntimeError):
            await sentinel.engage(Boom(), "rama", NOW)  # type: ignore[arg-type]
        assert list(tmp_path.iterdir()) == []

    async def test_an_unreadable_sentinel_raises_rather_than_reading_as_clear(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "HALT").mkdir()  # a directory where the file should be
        with pytest.raises(OSError):
            await FileSentinelKillSwitch(tmp_path / "HALT").read()


class TestMonitorReading:
    async def test_before_the_first_read_the_switch_is_unknown_and_so_halted(self) -> None:
        mon, _, _ = monitor(ScriptedSwitch("mongo"))
        reading = mon.reading()
        assert reading.halted and not reading.known

    async def test_every_source_clear_reads_as_known_and_not_halted(self) -> None:
        mon, _, _ = monitor(ScriptedSwitch("mongo"), ScriptedSwitch("file"))
        reading = await mon.refresh()
        assert (reading.halted, reading.known) == (False, True)
        assert mon.reading() == reading

    async def test_any_source_halted_halts_and_names_it(self) -> None:
        file = ScriptedSwitch("file", halted=True)
        file.reason = "operator"
        mon, _, _ = monitor(ScriptedSwitch("mongo"), file)
        reading = await mon.refresh()
        assert (reading.halted, reading.known, reading.source) == (True, True, "file")
        assert reading.reason == "operator"

    async def test_an_unreadable_source_makes_the_state_unknown_and_so_halted(self) -> None:
        mon, _, _ = monitor(
            ScriptedSwitch("mongo", error=ConnectionError("down")), ScriptedSwitch("file")
        )
        reading = await mon.refresh()
        assert (reading.halted, reading.known, reading.source) == (True, False, "mongo")

    async def test_the_file_sentinel_still_halts_when_mongo_is_down(self, tmp_path: Path) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        await sentinel.engage("db outage drill", "rama", NOW)
        mon, _, _ = monitor(ScriptedSwitch("mongo", error=ConnectionError("down")), sentinel)
        reading = await mon.refresh()
        assert (reading.halted, reading.known, reading.source) == (True, True, "file")

    async def test_a_hanging_source_times_out_and_reads_as_unknown(self) -> None:
        stuck = ScriptedSwitch("mongo")
        stuck.hang = True
        mon, _, _ = monitor(stuck, ScriptedSwitch("file"), read_timeout_seconds=0.05)
        reading = await mon.refresh()
        assert (reading.halted, reading.known) == (True, False)

    async def test_a_hanging_source_does_not_delay_the_others(self) -> None:
        stuck, file = ScriptedSwitch("mongo"), ScriptedSwitch("file", halted=True)
        stuck.hang = True
        mon, _, _ = monitor(stuck, file, read_timeout_seconds=0.05)
        assert (await mon.refresh()).halted and file.reads == 1

    async def test_a_reading_older_than_three_polls_is_unknown_again(self) -> None:
        mon, clock, _ = monitor(ScriptedSwitch("mongo"), poll_seconds=POLL)
        await mon.refresh()
        clock.advance(timedelta(seconds=3 * POLL))
        assert mon.reading().known  # exactly three polls old: still trusted
        clock.advance(timedelta(microseconds=1))
        stale = mon.reading()
        assert stale.halted and not stale.known and "last read" in stale.reason

    def test_a_monitor_needs_a_source_and_positive_intervals(self) -> None:
        clock = FixedClock(NOW)
        with pytest.raises(ConfigurationError, match="no source"):
            KillSwitchMonitor([], clock, AdvancingSleeper(clock))
        with pytest.raises(ConfigurationError, match="positive"):
            KillSwitchMonitor([ScriptedSwitch("m")], clock, AdvancingSleeper(clock), poll_seconds=0)


class TestMonitorLoop:
    async def test_it_polls_at_the_configured_interval(self) -> None:
        mon, _, sleeper = monitor(ScriptedSwitch("mongo"), poll_seconds=POLL)
        task = asyncio.create_task(mon.run())
        for _ in range(20):
            await asyncio.sleep(0)
        mon.stop()
        await task
        assert sleeper.sleeps and set(sleeper.sleeps) == {POLL}

    async def test_the_loop_survives_a_failing_source_and_recovers(self) -> None:
        flaky = ScriptedSwitch("mongo", error=ConnectionError("down"))
        mon, _, _ = monitor(flaky, poll_seconds=POLL)
        task = asyncio.create_task(mon.run())
        for _ in range(10):
            await asyncio.sleep(0)
        assert not mon.reading().known
        flaky.error = None
        for _ in range(20):
            await asyncio.sleep(0)
        mon.stop()
        await task
        assert mon.reading().known and not mon.reading().halted


class TestHaltStopsOrdersWithinOnePollInterval:
    """The acceptance test: a real sentinel, the monitor loop in virtual time, and the real
    engine with the real guard. Nothing is placed: the engine only says yes or no."""

    def engine(self, mon: KillSwitchMonitor, clock: FixedClock) -> RiskEngine:
        class Accounts:
            async def account_facts(self, signal: object) -> AccountFacts:
                return AccountFacts()

        class Markets:
            async def market_facts(self, instrument_id: str) -> InstrumentMarket:
                return InstrumentMarket()

        class Flow:
            async def order_flow(self) -> OrderFlowFacts:
                return OrderFlowFacts()

        system = MonitoredSystemFacts(
            TradingMode.PAPER, False, mon, UnreportedBrokerHealth(), UnreportedReconciliation()
        )
        return RiskEngine(
            [KillSwitchGuard()],
            SnapshotAssembler(clock, system, Accounts(), Markets(), Flow()),
            MemoryRejectionLog(),
            IdGenerator(),
            clock,
            RecordingAlertSink(),
        )

    async def test_orders_flow_until_the_sentinel_appears_and_stop_within_one_poll(
        self, tmp_path: Path
    ) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        clock = FixedClock(NOW)
        sleeper = SteppedSleeper(clock)
        mon = KillSwitchMonitor([sentinel], clock, sleeper, poll_seconds=POLL)
        engine = self.engine(mon, clock)

        task = asyncio.create_task(mon.run())
        await sleeper.until_sleeping()  # the first poll is done: the switch reads clear
        assert isinstance(await engine.review(make_signal(), "a"), RiskApprovedSignal)

        # The worst case: the halt lands just AFTER a poll.
        await KillSwitchControl([sentinel], [sentinel], clock).engage("drill", "rama")
        halted_at = clock.now()
        assert isinstance(
            await engine.review(make_signal(), "b"), RiskApprovedSignal
        )  # not seen yet

        await sleeper.tick()  # the next poll, one interval later
        seen_at = clock.now()
        decision = await engine.review(make_signal(), "c")
        mon.stop()
        await sleeper.tick()
        await task

        assert isinstance(decision, RiskRejection) and decision.rule == "KillSwitchGuard"
        assert seen_at - halted_at == timedelta(seconds=POLL)  # exactly one poll interval later
        assert sleeper.sleeps[0] == POLL

    async def test_clearing_the_sentinel_lets_orders_flow_again(self, tmp_path: Path) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        mon, clock, _ = monitor(sentinel)
        engine = self.engine(mon, clock)
        control = KillSwitchControl([sentinel], [sentinel], clock)
        await control.engage("drill", "rama")
        await mon.refresh()
        assert isinstance(await engine.review(make_signal(), "a"), RiskRejection)
        await control.release("rama")
        await mon.refresh()
        assert isinstance(await engine.review(make_signal(), "b"), RiskApprovedSignal)


class TestControl:
    def control(self, *places: ScriptedSwitch) -> KillSwitchControl:
        return KillSwitchControl(places, places, FixedClock(NOW), timeout_seconds=0.05)

    async def test_engaging_writes_every_place_and_reports_each(self) -> None:
        mongo, file = ScriptedSwitch("mongo"), ScriptedSwitch("file")
        outcomes = await self.control(mongo, file).engage("drill", "rama")
        assert [(o.name, o.ok) for o in outcomes] == [("mongo", True), ("file", True)]
        assert mongo.halted and file.halted and file.reason == "drill"

    async def test_one_place_failing_is_reported_and_the_others_still_land(self) -> None:
        mongo, file = ScriptedSwitch("mongo", error=ConnectionError("down")), ScriptedSwitch("file")
        outcomes = await self.control(mongo, file).engage("drill", "rama")
        by_name = {o.name: o for o in outcomes}
        assert not by_name["mongo"].ok and "ConnectionError" in by_name["mongo"].detail
        assert by_name["file"].ok and file.halted

    async def test_a_hanging_place_is_given_up_on(self) -> None:
        class Hangs(ScriptedSwitch):
            async def engage(self, reason: str, set_by: str, at: object) -> None:
                await asyncio.Event().wait()

        stuck, file = Hangs("mongo"), ScriptedSwitch("file")
        outcomes = await self.control(stuck, file).engage("drill", "rama")
        assert [o.ok for o in outcomes] == [False, True]

    async def test_a_halt_without_a_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="say why"):
            await self.control(ScriptedSwitch("file")).engage("  ", "rama")

    async def test_releasing_clears_every_place(self) -> None:
        mongo, file = ScriptedSwitch("mongo", halted=True), ScriptedSwitch("file", halted=True)
        outcomes = await self.control(mongo, file).release("rama")
        assert all(o.ok for o in outcomes) and not mongo.halted and not file.halted

    async def test_status_reports_each_place_and_what_could_not_be_read(self) -> None:
        mongo = ScriptedSwitch("mongo", error=ConnectionError("down"))
        file = ScriptedSwitch("file", halted=True)
        outcomes = {o.name: o for o in await self.control(mongo, file).status()}
        assert outcomes["file"].halted is True and outcomes["file"].ok
        assert outcomes["mongo"].ok is False and outcomes["mongo"].halted is None

    def test_a_control_with_nowhere_to_write_is_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="no place to write"):
            KillSwitchControl([], [], FixedClock(NOW))


class TestSettings:
    @pytest.mark.parametrize(
        ("flag", "enabled"),
        [
            ("true", True),
            ("false", False),
            ("True", False),
            ("1", False),
            ("yes", False),
            ("", False),
            (None, False),
        ],
    )
    def test_live_trading_is_enabled_only_by_exactly_true(
        self, flag: str | None, enabled: bool
    ) -> None:
        settings = Settings(_env_file=None, LIVE_TRADING_ENABLED=flag)  # type: ignore[call-arg]
        assert settings.live_trading_enabled is enabled

    def test_the_sentinel_path_comes_from_settings_or_defaults_under_home(
        self, tmp_path: Path
    ) -> None:
        explicit = Settings(_env_file=None, KILL_SWITCH_FILE=str(tmp_path / "H"))  # type: ignore[call-arg]
        assert explicit.kill_switch_path == tmp_path / "H"
        default = Settings(_env_file=None, KILL_SWITCH_FILE=None)  # type: ignore[call-arg]
        assert default.kill_switch_path == Path.home() / ".emporos" / "HALT"


class TestExitsPermittedByATripwireHalt:
    """EM-189: only a halt the anomaly tripwire set lets exits through."""

    async def test_a_tripwire_sentinel_permits_exits_and_an_operator_sentinel_does_not(
        self, tmp_path: Path
    ) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        await sentinel.engage("feed dropped", "tripwire", NOW)
        assert (await sentinel.read()).exits_permitted is True

        await sentinel.engage("feed dropped", "rama", NOW)
        assert (await sentinel.read()).exits_permitted is False

    async def test_a_reason_that_merely_mentions_the_tripwire_does_not_permit_exits(
        self, tmp_path: Path
    ) -> None:
        sentinel = FileSentinelKillSwitch(tmp_path / "HALT")
        await sentinel.engage("set by tripwire at noon", "rama", NOW)
        assert (await sentinel.read()).exits_permitted is False

    async def test_the_mongo_flag_permits_exits_only_for_the_tripwire(self) -> None:
        from emporos.persistence.records import KillSwitchRecord
        from emporos.risk.kill_switch import MongoKillSwitch

        class Repo:
            def __init__(self, set_by: str) -> None:
                self.record = KillSwitchRecord(
                    _id="current", halted=True, reason="r", set_by=set_by, changed_at=NOW
                )

            async def current(self) -> KillSwitchRecord:
                return self.record

        assert (await MongoKillSwitch(Repo("tripwire")).read()).exits_permitted is True  # type: ignore[arg-type]
        assert (await MongoKillSwitch(Repo("rama")).read()).exits_permitted is False  # type: ignore[arg-type]

    async def test_exits_are_permitted_only_when_every_halted_source_permits_them(self) -> None:
        class Source:
            def __init__(self, name: str, permits: bool) -> None:
                self.name, self._permits = name, permits

            async def read(self) -> SourceReading:
                return SourceReading(True, "x", self._permits)

        both, _, _ = monitor(Source("mongo", True), Source("file", True))  # type: ignore[arg-type]
        mixed, _, _ = monitor(Source("mongo", True), Source("file", False))  # type: ignore[arg-type]
        assert (await both.refresh()).exits_permitted is True
        assert (await mixed.refresh()).exits_permitted is False
