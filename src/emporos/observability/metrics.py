"""The worker's deliberately small metric set (plan.md §14), published as CloudWatch Embedded
Metric Format log lines.

    WorkerHeartbeat, TicksPerMinute, MaxDataStalenessSeconds, OpenOrders, UnknownOrders,
    DailyPnL, RiskRejections, ReconciliationMismatches

EMF means a metric costs nothing to emit: it is one structured JSON line on stdout, which the
CloudWatch agent turns into a metric (plan.md §14, $0.30/metric/month is why the set is small). A
metric whose value is unknown is OMITTED, never reported as zero: a P&L of 0 and an unknown P&L
mean different things to an alarm.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import Clock

NAMESPACE = "Emporos"
METRIC_NAMES = (
    "WorkerHeartbeat",
    "TicksPerMinute",
    "MaxDataStalenessSeconds",
    "OpenOrders",
    "UnknownOrders",
    "DailyPnL",
    "RiskRejections",
    "ReconciliationMismatches",
)
_LOG = logging.getLogger("emporos.metrics")


class MetricSource(Protocol):
    async def sample(self) -> Mapping[str, float | Decimal | int | None]:
        """Current values by metric name; `None` (or absent) for one that cannot be measured."""
        ...


class EmfMetricsSink:
    def __init__(self, clock: Clock, logger: logging.Logger | None = None) -> None:
        self._clock = clock
        self._logger = logger or _LOG

    def publish(self, values: Mapping[str, float | Decimal | int]) -> str | None:
        """Log one EMF record for the known metrics in `values`; returns it (None if empty)."""
        known = {k: float(v) for k, v in values.items() if k in METRIC_NAMES}
        if not known:
            return None
        record = {
            "_aws": {
                "Timestamp": int(self._clock.now().timestamp() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": [[]],
                        "Metrics": [{"Name": name} for name in sorted(known)],
                    }
                ],
            },
            **dict(sorted(known.items())),
        }
        line = json.dumps(record, sort_keys=False)
        self._logger.info(line)
        return line


class MetricsPublisher:
    """Gathers every source and publishes what they could measure."""

    def __init__(self, sources: Sequence[MetricSource], sink: EmfMetricsSink) -> None:
        self._sources = tuple(sources)
        self._sink = sink

    async def publish(self) -> str | None:
        merged: dict[str, float | Decimal | int] = {}
        for source in self._sources:
            for name, value in (await source.sample()).items():
                if value is not None:
                    merged[name] = value
        return self._sink.publish(merged)
