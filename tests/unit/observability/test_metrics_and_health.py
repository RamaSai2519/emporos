"""Metrics are emitted as EMF lines, an unknown value is omitted, and health is a plain report."""

import json
import logging
from decimal import Decimal

import httpx

from emporos.core.clock import FixedClock
from emporos.observability.health import HealthReport, create_health_app
from emporos.observability.metrics import METRIC_NAMES, EmfMetricsSink, MetricsPublisher
from tests.support.records import NOW


class Source:
    def __init__(self, **values: object) -> None:
        self.values = values

    async def sample(self) -> dict[str, object]:
        return self.values  # type: ignore[return-value]


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def sink() -> tuple[EmfMetricsSink, Capture]:
    logger = logging.getLogger("test.metrics")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    capture = Capture()
    logger.handlers = [capture]
    return EmfMetricsSink(FixedClock(NOW), logger), capture


def test_the_plan_lists_exactly_eight_metrics() -> None:
    assert len(METRIC_NAMES) == 8 and "WorkerHeartbeat" in METRIC_NAMES


def test_a_record_is_valid_embedded_metric_format() -> None:
    emf, capture = sink()
    line = emf.publish({"WorkerHeartbeat": 1, "DailyPnL": Decimal("-12.5"), "OpenOrders": 2})
    assert capture.lines == [line]
    record = json.loads(line or "")
    directive = record["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "Emporos"
    assert [m["Name"] for m in directive["Metrics"]] == [
        "DailyPnL",
        "OpenOrders",
        "WorkerHeartbeat",
    ]
    assert record["DailyPnL"] == -12.5 and record["_aws"]["Timestamp"] == int(
        NOW.timestamp() * 1000
    )


def test_an_unknown_metric_name_is_dropped_and_nothing_publishes_nothing() -> None:
    emf, capture = sink()
    assert emf.publish({"MadeUp": 1}) is None and capture.lines == []


async def test_the_publisher_merges_sources_and_omits_what_could_not_be_measured() -> None:
    emf, capture = sink()
    publisher = MetricsPublisher(
        [Source(WorkerHeartbeat=1, DailyPnL=None), Source(OpenOrders=3, DailyPnL=None)], emf
    )
    record = json.loads(await publisher.publish() or "")
    assert record["WorkerHeartbeat"] == 1 and record["OpenOrders"] == 3
    assert "DailyPnL" not in record  # unknown is not zero
    assert len(capture.lines) == 1


async def test_the_health_endpoint_reports_state_and_is_json() -> None:
    report = HealthReport(NOW, "TRADING", True, True, True, False, "CLEAN", 1.5, 2, 0)

    class Health:
        async def report(self) -> HealthReport:
            return report

    app = create_health_app(Health(), FixedClock(NOW))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w") as c:
        body = (await c.get("/health")).json()
        assert body["session_state"] == "TRADING" and body["max_data_staleness_seconds"] == 1.5
        assert (await c.get("/live")).json()["status"] == "alive"
        assert (await c.get("/docs")).status_code == 404  # no interactive docs on a diagnostic port
