"""The operator command catalogue (plan.md §15.1): what can be asked and what a valid ask is.

Adding a command is adding a `CommandType`, a parameter model in `COMMAND_SCHEMAS`, and a handler
(`control.handlers`): the submitter, the processor and the API itself do not change (open/closed).
Parameter models reject unknown keys and refuse floats for money, so a malformed command is
refused at the door instead of half-executed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from emporos.domain.orders import OrderSide


class CommandType(StrEnum):
    SET_KILL_SWITCH = "SET_KILL_SWITCH"
    SQUARE_OFF_ALL = "SQUARE_OFF_ALL"
    CLOSE_POSITION = "CLOSE_POSITION"
    CANCEL_ORDER = "CANCEL_ORDER"
    PLACE_MANUAL_ORDER = "PLACE_MANUAL_ORDER"
    START_STRATEGY = "START_STRATEGY"
    STOP_STRATEGY = "STOP_STRATEGY"
    UPDATE_STRATEGY_CONFIG = "UPDATE_STRATEGY_CONFIG"
    TRIGGER_BACKFILL = "TRIGGER_BACKFILL"
    RUN_BACKTEST = "RUN_BACKTEST"
    RECONCILE_NOW = "RECONCILE_NOW"
    SET_TRADING_MODE = "SET_TRADING_MODE"


class CommandStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXECUTING = "EXECUTING"
    DONE = "DONE"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        return self in {self.DONE, self.FAILED, self.REJECTED, self.EXPIRED}


def _exact(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise ValueError(f"must be a quoted string or an integer, not {value!r}")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{value!r} is not a number") from None
    if not number.is_finite():
        raise ValueError("must be finite")
    return number


Price = Annotated[Decimal, BeforeValidator(_exact), Field(gt=0)]


class Params(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SetKillSwitchParams(Params):
    halted: bool
    reason: str = ""


class SquareOffAllParams(Params):
    reason: str = "operator square-off"


class ClosePositionParams(Params):
    instrument_id: str = Field(min_length=1)
    reason: str = "operator close"


class CancelOrderParams(Params):
    order_id: str = Field(min_length=1)


class PlaceManualOrderParams(Params):
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: int = Field(strict=True, gt=0)
    limit_price: Price
    reason: str = Field(min_length=1)


class StrategyNameParams(Params):
    name: str = Field(min_length=1)


class StartStrategyParams(StrategyNameParams):
    # The standing (`rejected`, `stale`, ...) the operator was shown and typed back. The worker
    # refuses a strategy that is not validated unless this names its standing.
    acknowledge: str | None = Field(default=None, min_length=1)


class UpdateStrategyConfigParams(Params):
    name: str = Field(min_length=1)
    config: dict[str, Any]
    force: bool = False  # only a forced update is allowed mid-session


class TriggerBackfillParams(Params):
    instrument_ids: list[str] = Field(min_length=1)
    days: int = Field(strict=True, gt=0, le=365)


class RunBacktestParams(Params):
    strategy: str = Field(min_length=1)
    start: date
    end: date


class NoParams(Params):
    pass


class SetTradingModeParams(Params):
    mode: str


COMMAND_SCHEMAS: Mapping[CommandType, type[Params]] = MappingProxyType(
    {
        CommandType.SET_KILL_SWITCH: SetKillSwitchParams,
        CommandType.SQUARE_OFF_ALL: SquareOffAllParams,
        CommandType.CLOSE_POSITION: ClosePositionParams,
        CommandType.CANCEL_ORDER: CancelOrderParams,
        CommandType.PLACE_MANUAL_ORDER: PlaceManualOrderParams,
        CommandType.START_STRATEGY: StartStrategyParams,
        CommandType.STOP_STRATEGY: StrategyNameParams,
        CommandType.UPDATE_STRATEGY_CONFIG: UpdateStrategyConfigParams,
        CommandType.TRIGGER_BACKFILL: TriggerBackfillParams,
        CommandType.RUN_BACKTEST: RunBacktestParams,
        CommandType.RECONCILE_NOW: NoParams,
        CommandType.SET_TRADING_MODE: SetTradingModeParams,
    }
)


class InvalidCommandError(ValueError):
    """The command's type or parameters are not valid; nothing was recorded."""


def parse_command(command_type: str, params: Mapping[str, Any]) -> tuple[CommandType, Params]:
    try:
        kind = CommandType(command_type)
    except ValueError:
        raise InvalidCommandError(f"unknown command type {command_type!r}") from None
    try:
        return kind, COMMAND_SCHEMAS[kind].model_validate(dict(params))
    except ValidationError as error:
        raise InvalidCommandError(f"invalid {kind.value} parameters: {error.errors()}") from None
