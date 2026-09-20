"""What the worker says about itself on 127.0.0.1 (plan.md §14).

For an SSM-session look, not for the outside world: the security group has no inbound rules and
the listener binds loopback only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from fastapi import FastAPI

from emporos.core.clock import Clock


@dataclass(frozen=True)
class HealthReport:
    time: datetime
    session_state: str
    healthy: bool
    broker_session_ok: bool
    order_feed_ok: bool
    kill_switch_halted: bool
    reconciliation: str
    max_data_staleness_seconds: float | None
    open_orders: int
    unknown_orders: int


class HealthSource(Protocol):
    async def report(self) -> HealthReport: ...


def create_health_app(source: HealthSource, clock: Clock) -> FastAPI:
    app = FastAPI(title="Emporos worker health", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        report = await source.report()
        return asdict(report) | {"time": report.time.isoformat()}

    @app.get("/live")
    async def live() -> dict[str, str]:
        """The process is up and its event loop is answering: nothing more is claimed."""
        return {"status": "alive", "time": clock.now().isoformat()}

    return app
