"""EM-185: the parity job cannot hurt a session, and the CLI reaches the service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import pytest
from typer.testing import CliRunner

from emporos.cli import parity_commands
from emporos.cli.main import app
from emporos.cli.parity_composition import ParityCloseOutHook, ParityRuntime
from emporos.core.clock import FixedClock
from emporos.domain.experiments import Verdict
from emporos.domain.parity import ParityKind, ParityReport
from emporos.domain.verdicts import GateFinding
from emporos.parity.codec import SessionParityCodec
from emporos.parity.service import DailyOutcome, SkippedRun
from emporos.session.close_out import CloseOutResult, EndOfDay
from tests.support.fakes import RecordingAlertSink
from tests.unit.parity.test_service import Rig, mirrored_paper
from tests.unit.session.test_worker_parts import Recovery

runner = CliRunner()
NOW = datetime(2026, 1, 5, 11, tzinfo=UTC)  # 16:30 IST on Monday 2026-01-05


class Snap:
    async def take(self, kind: object) -> Any:
        from tests.support.records import RecordFactory

        return RecordFactory().portfolio_snapshot()

    async def resolve_unresolved(self) -> tuple[object, ...]:
        return ()


class Hook:
    name = "probe"

    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.seen = error, []

    async def after_close_out(self, result: CloseOutResult) -> None:
        self.seen.append(result)
        if self.error:
            raise self.error


class TestEndOfDayHooks:
    async def test_a_hook_runs_after_the_snapshot_and_sees_the_result(self) -> None:
        r, hook = Recovery(), Hook()
        out = await EndOfDay(r, Snap(), r, Snap(), RecordingAlertSink(), [hook]).close_out()  # type: ignore[arg-type]
        assert hook.seen == [out] and out.snapshot is not None

    async def test_a_failing_hook_is_alerted_and_changes_nothing(self) -> None:
        r, alerts = Recovery(), RecordingAlertSink()
        first, second = Hook(RuntimeError("boom")), Hook()
        second.name = "second"
        out = await EndOfDay(r, Snap(), r, Snap(), alerts, [first, second]).close_out()  # type: ignore[arg-type]
        assert [n for n, _ in alerts.alerts] == ["close_out_hook_failed:probe"]
        assert out.snapshot is not None and len(second.seen) == 1  # the next hook still ran


def _report(
    kind: ParityKind = ParityKind.DAILY, verdict: Verdict = Verdict.INCONCLUSIVE
) -> ParityReport:
    outcome = "fail" if verdict is Verdict.REJECTED else "unknown"
    return ParityReport(
        "s", "h", kind, date(2026, 1, 5), date(2026, 1, 5), verdict,
        (GateFinding("enough paper sessions", outcome, "x"),),
        1, 0, {}, NOW,
    )  # fmt: skip


class FakeService:
    def __init__(self, delay: float = 0) -> None:
        self.days: list[date] = []
        self.delay = delay
        self.real = Rig().paper(mirrored_paper(0)).service

    async def daily(self, day: date, roll_up: bool = True) -> DailyOutcome:
        await asyncio.sleep(self.delay)
        self.days.append(day)
        return DailyOutcome(day, (_report(),), 1, (SkippedRun("r9", "no config hash"),))

    async def weekly(self, day: date) -> tuple[ParityReport, ...]:
        return (_report(ParityKind.WEEKLY),)

    def assemble(self, sessions: Any, kind: ParityKind) -> ParityReport:
        return self.real.assemble(sessions, kind)


class FakeStore:
    def __init__(self, reports: list[ParityReport]) -> None:
        self.reports = reports

    async def for_strategy(self, strategy: str) -> list[ParityReport]:
        return [r for r in self.reports if r.strategy == strategy]

    async def strategies(self) -> list[str]:
        return sorted({r.strategy for r in self.reports})


def opener(service: FakeService, store: FakeStore | None = None) -> Any:
    @asynccontextmanager
    async def open_(*_: object, **__: object) -> AsyncIterator[ParityRuntime]:
        yield ParityRuntime(service, store or FakeStore([]))  # type: ignore[arg-type]

    return open_


class TestTheCloseOutHook:
    async def test_it_reports_todays_ist_session(self) -> None:
        service = FakeService()
        hook = ParityCloseOutHook(opener(service), FixedClock(NOW))
        await hook.after_close_out(CloseOutResult(0, None, None, None))
        assert service.days == [date(2026, 1, 5)]

    async def test_a_hung_job_is_cut_off_rather_than_holding_the_session_open(self) -> None:
        hook = ParityCloseOutHook(
            opener(FakeService(delay=5)), FixedClock(NOW), timeout_seconds=0.05
        )
        with pytest.raises(TimeoutError):
            await hook.after_close_out(CloseOutResult(0, None, None, None))


class TestTheCommands:
    def test_the_paper_group_offers_the_parity_commands(self) -> None:
        result = runner.invoke(app, ["paper", "parity", "--help"])
        assert result.exit_code == 0
        assert all(name in result.output for name in ("daily", "weekly", "show", "export"))

    def test_daily_prints_reports_and_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(parity_commands, "open_parity_runtime", opener(FakeService()))
        result = runner.invoke(app, ["paper", "parity", "daily", "--date", "2026-01-05"])
        assert result.exit_code == 0, result.output
        assert "INCONCLUSIVE" in result.output and "skipped run r9: no config hash" in result.output
        assert "already reported" in result.output

    def test_weekly_prints_the_weeks_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(parity_commands, "open_parity_runtime", opener(FakeService()))
        result = runner.invoke(app, ["paper", "parity", "weekly", "--date", "2026-01-05"])
        assert result.exit_code == 0 and "weekly" in result.output

    async def _stored(self) -> list[ParityReport]:
        rig = Rig().paper(mirrored_paper(0))
        (daily,) = (await rig.service.daily(rig.day)).reports
        return [daily]

    def test_show_and_export_work_from_stored_sessions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        stored = asyncio.run(self._stored())
        monkeypatch.setattr(
            parity_commands, "open_parity_runtime", opener(FakeService(), FakeStore(stored))
        )
        shown = runner.invoke(app, ["paper", "parity", "show", "--strategy", "buy_then_sell"])
        assert shown.exit_code == 0, shown.output
        assert "cumulative" in shown.output and "enough paper sessions" in shown.output

        out = tmp_path / "parity"
        exported = runner.invoke(app, ["paper", "parity", "export", "--out", str(out)])
        assert exported.exit_code == 0, exported.output
        assert sorted(p.suffix for p in out.iterdir()) == [".json", ".md"]
        assert "# Parity: buy_then_sell" in next(out.glob("*.md")).read_text()
        assert SessionParityCodec  # the stored payload round-tripped to get here

    def test_a_strategy_with_nothing_stored_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(parity_commands, "open_parity_runtime", opener(FakeService()))
        shown = runner.invoke(app, ["paper", "parity", "show", "--strategy", "nope"])
        exported = runner.invoke(app, ["paper", "parity", "export"])
        assert "no parity reports for nope" in shown.output
        assert "no stored parity sessions" in exported.output

    def test_a_failure_exits_non_zero_with_the_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        @asynccontextmanager
        async def broken(*_: object, **__: object) -> AsyncIterator[ParityRuntime]:
            raise ValueError("no mongo")
            yield  # pragma: no cover

        monkeypatch.setattr(parity_commands, "open_parity_runtime", broken)
        result = runner.invoke(app, ["paper", "parity", "daily", "--date", "2026-01-05"])
        assert result.exit_code == 1 and "parity daily failed: no mongo" in result.output
