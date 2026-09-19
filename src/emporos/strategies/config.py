"""The resolved, validated configuration a strategy runs with (plan.md §9).

Pure data: no file access here. A YAML file is read elsewhere and turned into one of these by the
resolver (`resolution.py`); a stored snapshot is turned back into one by the same schema, so a run
started from YAML and a run reproduced from its snapshot see identical objects.

Exactness rules. Percentages, rates and money amounts are `Decimal`, accepted only from a quoted
string or an integer — a YAML float has already lost exactness and is refused. Every model rejects
unknown keys, so a misspelt setting fails loudly instead of silently taking a default.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SerializeAsAny, model_validator

from emporos.domain.candles import Timeframe


def _exact_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError(f"must be a quoted string or an integer, not {value!r}")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{value!r} is not a number") from None
    if not result.is_finite():
        raise ValueError("must be finite")
    return result


def _clock_time(value: object) -> time:
    """`"15:00"` only. Unquoted `15:00` is a base-60 integer in YAML 1.1 (900), which pydantic
    would read as 00:15 — so anything but a quoted HH:MM string is refused."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError(f'must be a quoted "HH:MM" string, not {value!r}')
    try:
        return time(int(value[:2]), int(value[3:]))
    except ValueError:
        raise ValueError(f"{value!r} is not a valid time of day") from None


ExactDecimal = Annotated[Decimal, BeforeValidator(_exact_decimal)]
ClockTime = Annotated[time, BeforeValidator(_clock_time)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StrategyParameters(_Section):
    """Base for a strategy's own parameters. Each strategy declares a subclass (strict, frozen)."""


class RiskSettings(_Section):
    """Per-strategy limits the risk engine (Phase 11) enforces. Strategies size within them."""

    max_position_value: ExactDecimal = Field(gt=0)  # rupees
    max_open_positions: PositiveInt
    stop_loss_pct: ExactDecimal = Field(gt=0, lt=100)
    target_pct: ExactDecimal = Field(gt=0)


class ExecutionSettings(_Section):
    """How the execution engine (Phase 12) works this strategy's orders."""

    limit_buffer_bps: ExactDecimal = Field(ge=0)  # marketable-limit offset (Decision 8)
    reprice_after_seconds: PositiveInt
    max_reprices: NonNegativeInt


class SessionSettings(_Section):
    no_new_entries_after: ClockTime  # IST
    square_off_at: ClockTime  # IST

    @model_validator(mode="after")
    def _entries_stop_before_square_off(self) -> SessionSettings:
        if self.no_new_entries_after >= self.square_off_at:
            raise ValueError("no_new_entries_after must be earlier than square_off_at")
        return self


class UniverseMember(_Section):
    """One instrument as written in the config and as it resolved. The resolved id is what runs;
    the symbol is kept so a snapshot says what the operator asked for."""

    symbol: str  # "NSE:RELIANCE-EQ" — exchange, then tradingsymbol
    instrument_id: str  # "NSE:2885" — exchange, then token


class ResolvedStrategyConfig(_Section):
    name: str = Field(min_length=1)
    enabled: bool
    timeframe: Timeframe
    universe: tuple[UniverseMember, ...] = Field(min_length=1)
    parameters: SerializeAsAny[StrategyParameters]
    risk: RiskSettings
    execution: ExecutionSettings
    session: SessionSettings

    @model_validator(mode="after")
    def _universe_has_no_duplicates(self) -> ResolvedStrategyConfig:
        ids = [member.instrument_id for member in self.universe]
        if len(set(ids)) != len(ids):
            raise ValueError("the universe lists an instrument twice")
        return self

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(member.instrument_id for member in self.universe)
