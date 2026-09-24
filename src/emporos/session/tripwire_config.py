"""The tripwire's thresholds, from the `tripwire:` section of `config/settings.*.yaml`.

Numbers are integers or quoted strings, never a YAML float, and an unknown key is refused, so a
typo'd threshold cannot silently leave an anomaly unwatched.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from emporos.core.errors import ConfigurationError
from emporos.strategies.config import ExactDecimal, PositiveInt


class TripwireSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    poll_seconds: PositiveInt
    feed_drop_halt_seconds: PositiveInt
    stale_instrument_fraction: ExactDecimal = Field(gt=0, lt=1)
    unknown_order_seconds: PositiveInt
    rejection_burst_count: PositiveInt
    rejection_burst_seconds: PositiveInt
    order_feed_down_seconds: PositiveInt

    @property
    def poll(self) -> timedelta:
        return timedelta(seconds=self.poll_seconds)

    @property
    def feed_drop_halt(self) -> timedelta:
        return timedelta(seconds=self.feed_drop_halt_seconds)

    @property
    def stale_fraction(self) -> Decimal:
        return self.stale_instrument_fraction

    @property
    def unknown_order(self) -> timedelta:
        return timedelta(seconds=self.unknown_order_seconds)

    @property
    def rejection_window(self) -> timedelta:
        return timedelta(seconds=self.rejection_burst_seconds)

    @property
    def order_feed_down(self) -> timedelta:
        return timedelta(seconds=self.order_feed_down_seconds)


class TripwireSettingsLoader:
    def __init__(self, yaml_config: Mapping[str, Any]) -> None:
        self._config = yaml_config

    def load(self) -> TripwireSettings:
        section = self._config.get("tripwire")
        if not isinstance(section, dict):
            raise ConfigurationError("settings have no `tripwire:` section: the worker needs one")
        try:
            return TripwireSettings.model_validate(section)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise ConfigurationError(f"invalid tripwire settings: {problems}") from error
