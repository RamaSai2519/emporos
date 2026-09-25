"""EM-217: the D1 plan for a worker's quote recorder, and that the recorder is handed no order
methods."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from emporos.broker.paper.market import MarketDataOnly
from emporos.cli.quote_recording import DEFAULT_QUOTES_DIR, d1_plan
from emporos.cli.worker_composition import WorkerTuning
from emporos.core.clock import FixedClock
from emporos.quotes.priority import NoPendingOrders
from emporos.research.d1_universe import D1Manifest, D1Universe


def test_the_plan_records_the_included_and_the_held_out_names_once_each() -> None:
    plan = d1_plan()
    manifest = D1Manifest.load(Path("config/universe/d1/universe.yaml"))

    assert set(plan.instrument_ids) == set(manifest.included) | set(manifest.holdout)
    assert len(plan.instrument_ids) == len(set(plan.instrument_ids))
    assert set(D1Universe.load().instrument_ids) <= set(plan.instrument_ids)
    assert all(i.startswith("NSE:") for i in plan.instrument_ids)
    assert plan.directory == DEFAULT_QUOTES_DIR and plan.settings.interval == timedelta(seconds=60)
    assert plan.settings.batch_size == 50


def test_the_interval_and_directory_are_configurable(tmp_path: Path) -> None:
    plan = d1_plan(tmp_path, 90)

    assert plan.settings.interval == timedelta(seconds=90) and plan.directory == tmp_path


def test_a_worker_records_nothing_unless_asked() -> None:
    assert WorkerTuning().quote_recording is None


class Broker:
    """Has order methods on purpose: the recorder must not be able to reach them."""

    def __init__(self) -> None:
        self.quotes_asked: list[list[str]] = []

    async def get_quote(self, ids: list[str]) -> list[object]:
        self.quotes_asked.append(list(ids))
        return []

    def place_order(self) -> None:  # pragma: no cover - must never be called
        raise AssertionError("an order was placed")


async def test_the_job_polls_through_a_market_data_only_view(tmp_path: Path) -> None:
    plan = d1_plan(tmp_path)
    broker = Broker()
    clock = FixedClock(datetime(2026, 9, 25, 10, 0, tzinfo=UTC).replace(hour=4, minute=45))

    job = plan.job(broker, NoPendingOrders(), clock)  # type: ignore[arg-type]
    await job.action()

    assert job.name == "quote_recorder" and job.interval == plan.settings.interval
    assert sum(len(c) for c in broker.quotes_asked) == len(plan.instrument_ids)
    assert max(len(c) for c in broker.quotes_asked) <= 50
    view = MarketDataOnly(broker)  # type: ignore[arg-type]
    assert not hasattr(view, "place_order")


def test_the_worker_commands_take_the_quote_flags() -> None:
    from typer.testing import CliRunner

    from emporos.cli.worker_commands import worker_app

    result = CliRunner().invoke(worker_app, ["run", "--help"])

    assert "--record-quotes" in result.output and "--quotes-dir" in result.output
    live = CliRunner().invoke(worker_app, ["live", "--help"])
    assert "--record-quotes" in live.output


def test_the_summary_command_reports_a_recorded_day(tmp_path: Path) -> None:
    from datetime import date

    from typer.testing import CliRunner

    from emporos.cli.main import app
    from emporos.core.clock import IST
    from emporos.quotes.row import QuoteRow
    from emporos.quotes.sink import ParquetQuoteSink

    at = datetime(2026, 9, 25, 10, 0, tzinfo=IST)
    sink = ParquetQuoteSink(tmp_path, FixedClock(at))
    sink.append(
        [QuoteRow("NSE:1", at, at, Decimal("100"), Decimal("99.9"), Decimal("100.1"), 5, 6, 1)]
    )
    sink.flush()

    result = CliRunner().invoke(
        app, ["quotes", "summary", "--day", "2026-09-25", "--directory", str(tmp_path)]
    )

    assert result.exit_code == 0 and "1 rows in 1 files, 1 instruments" in result.output
    assert date(2026, 9, 25).isoformat() in result.output
