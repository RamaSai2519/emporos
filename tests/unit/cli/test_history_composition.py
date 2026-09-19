"""The history composer wires every part to the same repository/coverage/calendar, and the CLI
history commands report outcomes and exit codes honestly (through their runtime seam)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from typer.testing import CliRunner

from emporos.cli import main as cli_main
from emporos.cli.history_composition import HistoryComposer, resolve_symbols
from emporos.cli.history_runtime import HistoryRuntime
from emporos.core.clock import FixedClock
from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Timeframe
from emporos.domain.instruments import Exchange
from emporos.history.calendar import StoredTradingCalendar
from emporos.instruments.cache import InstrumentCache
from emporos.persistence.candle_cold import ParquetCandleArchive
from emporos.persistence.candles import CandleRepository
from tests.support.fakes import (
    InMemoryCandleStore,
    InMemoryCoverageStore,
    InMemoryObjectStore,
    make_instrument,
)
from tests.support.history import BrokerHistory

SBIN = make_instrument("3045", symbol="SBIN-EQ")
NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
runner = CliRunner()


class MemoryCalendarStore:
    def __init__(self) -> None:
        self.days: dict[date, bool] = {}

    async def load_all(self) -> dict[date, bool]:
        return dict(self.days)

    async def save(self, days: dict[date, bool]) -> None:  # type: ignore[override]
        self.days.update(days)


def composer(history: BrokerHistory | None = None) -> tuple[HistoryComposer, InMemoryCandleStore]:
    store = InMemoryCandleStore()
    repository = CandleRepository(store, ParquetCandleArchive(InMemoryObjectStore()))
    return (
        HistoryComposer(
            source=history or BrokerHistory(),
            repository=repository,
            coverage=InMemoryCoverageStore(),
            calendar_store=MemoryCalendarStore(),
            calendar=StoredTradingCalendar(),
            clock=FixedClock(NOW),
        ),
        store,
    )


async def test_the_composed_stack_backfills_then_finds_nothing_to_reconcile() -> None:
    stack = composer()[0].build()

    report = await stack.backfill.run([SBIN], date(2026, 9, 15), date(2026, 9, 16))
    assert report.ok and report.candles_written == 2 * 375

    reports = await stack.reconciler.reconcile([SBIN], date(2026, 9, 15), date(2026, 9, 16))
    assert reports[SBIN.instrument_id].gaps_found == 0  # backfill and detector share one truth


async def test_the_composed_stack_writes_only_through_the_repository() -> None:
    built, store = composer()
    await built.build().backfill.run([SBIN], date(2026, 9, 15), date(2026, 9, 15))
    assert {tf for (_, tf, _) in store.bars()} == {
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
    }


def test_symbols_resolve_and_unknown_ones_are_named() -> None:
    cache = InstrumentCache([SBIN])
    assert resolve_symbols(cache, Exchange.NSE, ["SBIN-EQ"]) == [SBIN]
    with pytest.raises(ConfigurationError, match="NOPE-EQ"):
        resolve_symbols(cache, Exchange.NSE, ["SBIN-EQ", "NOPE-EQ"])
    with pytest.raises(ConfigurationError):
        resolve_symbols(cache, Exchange.NSE, [])


def fake_runtime(history: BrokerHistory | None = None):  # type: ignore[no-untyped-def]
    built, _ = composer(history)

    @asynccontextmanager
    async def opened(settings: object) -> AsyncIterator[HistoryRuntime]:
        yield HistoryRuntime(built.build(), InstrumentCache([SBIN]))

    return opened


def test_the_backfill_command_reports_and_exits_zero_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_main, "open_history_runtime", fake_runtime())

    result = runner.invoke(cli_main.app, ["history", "backfill", "-s", "SBIN-EQ", "-d", "10"])

    assert result.exit_code == 0
    assert "0 chunk(s) failed" in result.output and "candle(s) written" in result.output


def test_an_incomplete_backfill_exits_two_so_it_can_be_rerun_to_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = BrokerHistory()
    history.fail_requests = set(range(1, 10))  # every chunk is denied
    monkeypatch.setattr(cli_main, "open_history_runtime", fake_runtime(history))

    result = runner.invoke(cli_main.app, ["history", "backfill", "-s", "SBIN-EQ", "-d", "10"])

    assert result.exit_code == 2 and "chunk(s) failed" in result.output


def test_ten_failed_chunks_is_not_mistaken_for_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the exit code comes from the report, not from matching text in the summary."""
    history = BrokerHistory()
    history.fail_requests = set(range(1, 100))
    monkeypatch.setattr(cli_main, "open_history_runtime", fake_runtime(history))

    result = runner.invoke(cli_main.app, ["history", "backfill", "-s", "SBIN-EQ", "-d", "400"])

    assert result.exit_code == 2


def test_the_reconcile_command_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_main, "open_history_runtime", fake_runtime())
    result = runner.invoke(cli_main.app, ["history", "reconcile", "-s", "SBIN-EQ", "-d", "5"])
    assert result.exit_code == 0 and "reconcile:" in result.output


def test_an_unknown_symbol_is_a_clean_error_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_main, "open_history_runtime", fake_runtime())
    result = runner.invoke(cli_main.app, ["history", "backfill", "-s", "NOPE-EQ"])
    assert result.exit_code == 1 and "NOPE-EQ" in result.output


def test_a_symbol_is_required() -> None:
    assert runner.invoke(cli_main.app, ["history", "backfill"]).exit_code != 0


def test_history_help_lists_the_commands() -> None:
    result = runner.invoke(cli_main.app, ["history", "--help"])
    for command in ("backfill", "reconcile", "seed-calendar"):
        assert command in result.output
