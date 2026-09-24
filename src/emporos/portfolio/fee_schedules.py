"""Loads dated fee schedules from `config/fees/*.yaml` (plan.md §10: never hardcoded).

Each file is one schedule with an `effective_from` date; `for_date` returns the one in force on a
day, so a rate change is a new file, and old sessions keep the rates they were traded under.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from emporos.core.config import CONFIG_DIR
from emporos.domain.fees import FeeSchedule, TradeProduct
from emporos.domain.instruments import Exchange
from emporos.domain.money import Money


class FeeScheduleError(ValueError):
    """A fee file is missing, malformed, or has a rate that is not an exact string."""


class FeeScheduleLibrary:
    def __init__(self, schedules: list[FeeSchedule]) -> None:
        if not schedules:
            raise FeeScheduleError("no fee schedules were found")
        self._schedules = sorted(schedules, key=lambda s: s.effective_from)

    @classmethod
    def from_directory(
        cls,
        directory: Path = CONFIG_DIR / "fees",
        product: TradeProduct = TradeProduct.INTRADAY,
    ) -> FeeScheduleLibrary:
        """The schedules of ONE product. The default is intraday so that a delivery file added to
        the directory can never become the schedule an intraday reader finds in force."""
        parser = FeeScheduleParser()
        parsed = [parser.parse(path) for path in sorted(directory.glob("*.yaml"))]
        return cls([schedule for schedule in parsed if schedule.product is product])

    @property
    def earliest(self) -> FeeSchedule:
        """The oldest schedule: what a run over days before any schedule may explicitly assume."""
        return self._schedules[0]

    def for_date(self, day: date) -> FeeSchedule:
        in_force = [s for s in self._schedules if s.effective_from <= day]
        if not in_force:
            raise FeeScheduleError(f"no fee schedule is in force on {day.isoformat()}")
        return in_force[-1]


class FeeScheduleParser:
    def parse(self, path: Path) -> FeeSchedule:
        try:
            raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            brokerage = raw["brokerage"]
            return FeeSchedule(
                name=str(raw["name"]),
                effective_from=date.fromisoformat(str(raw["effective_from"])),
                brokerage_flat=Money(self._exact(brokerage["flat"])),
                brokerage_percent=self._exact(brokerage["percent"]),
                brokerage_minimum=Money(self._exact(brokerage["minimum"])),
                stt_sell_percent=self._exact(raw["stt_sell_percent"]),
                exchange_transaction_percent={
                    Exchange(name): self._exact(rate)
                    for name, rate in raw["exchange_transaction_percent"].items()
                },
                sebi_per_crore=Money(self._exact(raw["sebi_per_crore"])),
                stamp_duty_buy_percent=self._exact(raw["stamp_duty_buy_percent"]),
                gst_percent=self._exact(raw["gst_percent"]),
                verified=raw.get("verified", False) is True,
                product=TradeProduct(str(raw.get("product", TradeProduct.INTRADAY.value))),
                stt_buy_percent=self._exact(raw.get("stt_buy_percent", "0")),
                dp_charge_per_sale=Money(self._exact(raw.get("dp_charge_per_sale", "0"))),
            )
        except FeeScheduleError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise FeeScheduleError(f"{path.name}: {error!r}") from error

    @staticmethod
    def _exact(value: object) -> Decimal:
        """Rates are quoted strings; a YAML float would already have lost exactness."""
        if not isinstance(value, str):
            raise FeeScheduleError(f"a rate must be a quoted string, got {value!r}")
        try:
            return Decimal(value)
        except InvalidOperation:
            raise FeeScheduleError(f"{value!r} is not a number") from None
