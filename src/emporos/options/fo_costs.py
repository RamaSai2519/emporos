"""Statutory and broker charges on index-option trades, from a dated schedule (EM-226).

The schedule lives in `config/fees/fo/*.yaml` (never hardcoded; a rate change is a new file, and old
runs keep the rates they traded under). It is a separate directory from the cash-equity schedules on
purpose: those carry different fields and are parsed by `FeeScheduleLibrary`.

Each leg fill is its own order (a spread is not one order at the broker), so a four-leg iron condor
pays brokerage eight times over a round trip. STT is charged on the premium of a SELL, and, at
expiry,
on the intrinsic value of a long leg that finishes in the money."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from emporos.core.config import CONFIG_DIR
from emporos.domain.orders import OrderSide
from emporos.options.spread import LegFill

__all__ = [
    "FoCharges",
    "FoCostModel",
    "FoFeeError",
    "FoFeeSchedule",
    "FoFeeScheduleLibrary",
]

_HUNDRED = Decimal(100)
_CRORE = Decimal(10_000_000)


class FoFeeError(ValueError):
    """A fee file is missing, malformed, or has a rate that is not an exact string."""


@dataclass(frozen=True)
class FoFeeSchedule:
    name: str
    effective_from: date
    verified: bool
    brokerage_flat_per_order: Decimal
    stt_sell_percent: Decimal
    stt_exercise_percent: Decimal
    exchange_transaction_percent: Decimal
    sebi_per_crore: Decimal
    stamp_duty_buy_percent: Decimal
    ipft_percent: Decimal
    gst_percent: Decimal


@dataclass(frozen=True)
class FoCharges:
    brokerage: Decimal = Decimal(0)
    stt: Decimal = Decimal(0)
    exchange: Decimal = Decimal(0)
    sebi: Decimal = Decimal(0)
    stamp: Decimal = Decimal(0)
    ipft: Decimal = Decimal(0)
    gst: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return (
            self.brokerage
            + self.stt
            + self.exchange
            + self.sebi
            + self.stamp
            + self.ipft
            + self.gst
        )

    def scaled(self, factor: Decimal) -> FoCharges:
        return FoCharges(
            self.brokerage * factor, self.stt * factor, self.exchange * factor,
            self.sebi * factor, self.stamp * factor, self.ipft * factor, self.gst * factor,
        )  # fmt: skip

    def __add__(self, other: FoCharges) -> FoCharges:
        return FoCharges(
            self.brokerage + other.brokerage, self.stt + other.stt, self.exchange + other.exchange,
            self.sebi + other.sebi, self.stamp + other.stamp, self.ipft + other.ipft,
            self.gst + other.gst,
        )  # fmt: skip


class FoCostModel:
    """Prices charges under one schedule; `fee_multiplier` is the adverse scenario's 1.5x."""

    def __init__(self, schedule: FoFeeSchedule, fee_multiplier: Decimal = Decimal(1)) -> None:
        if fee_multiplier < 1:
            raise ValueError("a scenario may not charge less than the statutory fees")
        self._s = schedule
        self._multiplier = fee_multiplier

    @property
    def schedule(self) -> FoFeeSchedule:
        return self._s

    def order(self, fill: LegFill) -> FoCharges:
        """The charges on one executed order."""
        s = self._s
        turnover = fill.turnover
        brokerage = s.brokerage_flat_per_order
        exchange = turnover * s.exchange_transaction_percent / _HUNDRED
        sebi = turnover * s.sebi_per_crore / _CRORE
        ipft = turnover * s.ipft_percent / _HUNDRED
        selling = fill.leg.side is OrderSide.SELL
        stt = turnover * s.stt_sell_percent / _HUNDRED if selling else Decimal(0)
        stamp = Decimal(0) if selling else turnover * s.stamp_duty_buy_percent / _HUNDRED
        gst = (brokerage + exchange + sebi + ipft) * s.gst_percent / _HUNDRED
        return FoCharges(brokerage, stt, exchange, sebi, stamp, ipft, gst).scaled(self._multiplier)

    def orders(self, fills: Iterable[LegFill]) -> FoCharges:
        total = FoCharges()
        for fill in fills:
            total = total + self.order(fill)
        return total

    def expiry_stt(self, long_intrinsic_units: Decimal) -> FoCharges:
        """STT on the intrinsic value (in rupees, already times units) of long legs that settle in
        the money. Short legs settling in the money pay nothing here."""
        stt = long_intrinsic_units * self._s.stt_exercise_percent / _HUNDRED
        return FoCharges(stt=stt).scaled(self._multiplier)


class FoFeeScheduleLibrary:
    def __init__(self, schedules: list[FoFeeSchedule]) -> None:
        if not schedules:
            raise FoFeeError("no F&O fee schedules were found")
        self._schedules = sorted(schedules, key=lambda s: s.effective_from)

    @classmethod
    def from_directory(cls, directory: Path = CONFIG_DIR / "fees" / "fo") -> FoFeeScheduleLibrary:
        parser = FoFeeScheduleParser()
        return cls([parser.parse(path) for path in sorted(directory.glob("*.yaml"))])

    def for_date(self, day: date) -> FoFeeSchedule:
        in_force = [s for s in self._schedules if s.effective_from <= day]
        if not in_force:
            raise FoFeeError(f"no F&O fee schedule is in force on {day.isoformat()}")
        return in_force[-1]

    @property
    def earliest(self) -> FoFeeSchedule:
        """What a run over days before any schedule may explicitly assume."""
        return self._schedules[0]


class FoFeeScheduleParser:
    def parse(self, path: Path) -> FoFeeSchedule:
        try:
            raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            return FoFeeSchedule(
                name=str(raw["name"]),
                effective_from=date.fromisoformat(str(raw["effective_from"])),
                verified=raw.get("verified", False) is True,
                brokerage_flat_per_order=self._exact(raw, "brokerage_flat_per_order"),
                stt_sell_percent=self._exact(raw, "stt_sell_percent"),
                stt_exercise_percent=self._exact(raw, "stt_exercise_percent"),
                exchange_transaction_percent=self._exact(raw, "exchange_transaction_percent"),
                sebi_per_crore=self._exact(raw, "sebi_per_crore"),
                stamp_duty_buy_percent=self._exact(raw, "stamp_duty_buy_percent"),
                ipft_percent=self._exact(raw, "ipft_percent"),
                gst_percent=self._exact(raw, "gst_percent"),
            )
        except FoFeeError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise FoFeeError(f"{path.name}: {error!r}") from error

    @staticmethod
    def _exact(raw: Mapping[str, Any], key: str) -> Decimal:
        """Rates are quoted strings; a YAML float would already have lost exactness."""
        value = raw[key]
        if not isinstance(value, str):
            raise FoFeeError(f"{key}: a rate must be a quoted string, got {value!r}")
        try:
            rate = Decimal(value)
        except InvalidOperation:
            raise FoFeeError(f"{key}: {value!r} is not a number") from None
        if rate < 0:
            raise FoFeeError(f"{key}: a rate cannot be negative")
        return rate
