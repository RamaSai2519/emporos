"""Strategy lifecycle and configuration commands (START/STOP/UPDATE_STRATEGY_CONFIG).

Starting launches a NEW run (its config snapshotted and recorded before any market data is seen);
stopping is graceful and never closes positions; a configuration update is schema-validated by the
strategy layer and STORED for the strategy's next start — a running strategy is never re-configured
under its own feet.
"""

from __future__ import annotations

from typing import Any, Protocol

from emporos.persistence.records import StrategyRecord


class RunHost(Protocol):
    async def start_strategy(self, name: str) -> str: ...

    async def stop_strategy(self, name: str) -> str: ...


class ConfigValidator(Protocol):
    def validate(self, name: str, raw: dict[str, Any]) -> dict[str, Any]:
        """Return the validated, normalised config document, or raise `ValueError`."""
        ...


class StrategyCatalogue(Protocol):
    async def get_by_name(self, name: str) -> StrategyRecord | None: ...

    async def replace(self, record: StrategyRecord) -> None: ...


class StrategyController:
    def __init__(
        self, host: RunHost, validator: ConfigValidator, catalogue: StrategyCatalogue
    ) -> None:
        self._host = host
        self._validator = validator
        self._catalogue = catalogue

    async def start(self, name: str) -> str:
        return await self._host.start_strategy(name)

    async def stop(self, name: str) -> str:
        return await self._host.stop_strategy(name)

    async def store_config(self, name: str, config: dict[str, Any]) -> str:
        existing = await self._catalogue.get_by_name(name)
        if existing is None:
            raise ValueError(f"unknown strategy {name!r}")
        document = self._validator.validate(name, config)
        await self._catalogue.replace(StrategyRecord(_id=existing.id, name=name, config=document))
        return f"configuration for {name} stored; it takes effect at the next start"
