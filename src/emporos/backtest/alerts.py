"""Alerts raised during a backtest are kept and reported, not sent anywhere."""

from __future__ import annotations


class CollectedAlerts:
    """An `AlertSink` that remembers what it was told, so a report can say a strategy was halted."""

    def __init__(self) -> None:
        self._alerts: list[tuple[str, str]] = []

    def raise_alert(self, name: str, message: str) -> None:
        self._alerts.append((name, message))

    @property
    def alerts(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._alerts)
