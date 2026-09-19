"""From a parsed YAML mapping to a `ResolvedStrategyConfig` (plan.md §9, EM-67).

Pure: the caller reads the file, this validates and resolves. Order of defence, cheapest first:

1. hygiene over the whole raw tree — a YAML float anywhere (money never touches a float) and any
   credential-shaped key anywhere (secrets never appear in strategy config);
2. the file schema: known keys only, exact types, so a misspelt setting fails at load time;
3. the strategy's OWN parameter schema (strict, from the registry);
4. symbol resolution: `"NSE:RELIANCE-EQ"` is `(exchange, tradingsymbol)`, resolved through the
   `InstrumentResolver` to the id that actually runs, `"NSE:2885"` (`exchange:token`). Both are
   kept.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from emporos.domain.candles import Timeframe
from emporos.domain.instruments import Exchange, InstrumentResolver, UnknownInstrumentError
from emporos.strategies.config import (
    ExecutionSettings,
    ResolvedStrategyConfig,
    RiskSettings,
    SessionSettings,
    StrategyParameters,
    UniverseMember,
)

M = TypeVar("M", bound=BaseModel)

_CREDENTIAL_KEY = re.compile(
    r"password|passwd|secret|token|api[_-]?key|totp|credential|private[_-]?key|"
    r"authorization|bearer|client[_-]?code",
    re.IGNORECASE,
)


class StrategyConfigError(ValueError):
    """A strategy configuration is invalid. The message says where and why."""


class ParametersCatalog(Protocol):
    """What the resolver needs from the strategy registry."""

    def parameters_model(self, name: str) -> type[StrategyParameters]: ...


class ConfigHygiene:
    """Rejects, anywhere in a raw config tree, what no strategy config may contain."""

    def check(self, node: object, path: str = "") -> None:
        if isinstance(node, float):
            raise StrategyConfigError(
                f"{path or 'config'}: {node!r} is a YAML float; quote it (\"{node}\") or use an "
                "integer — money-like values are never floats"
            )
        if isinstance(node, Mapping):
            for key, value in node.items():
                where = f"{path}.{key}" if path else str(key)
                if _CREDENTIAL_KEY.search(str(key)):
                    raise StrategyConfigError(
                        f"{where}: credential-shaped key; secrets never belong in strategy config"
                    )
                self.check(value, where)
        elif isinstance(node, list | tuple):
            for index, item in enumerate(node):
                self.check(item, f"{path}[{index}]")


class StaticUniverse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Annotated[str, Field(pattern="^static$")]
    instruments: tuple[str, ...] = Field(min_length=1)


class StrategyFile(BaseModel):
    """The schema of one `config/strategies/*.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    enabled: StrictBool
    timeframe: Timeframe
    universe: StaticUniverse
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk: RiskSettings
    execution: ExecutionSettings
    session: SessionSettings


class StrategyConfigResolver:
    def __init__(
        self,
        catalog: ParametersCatalog,
        instruments: InstrumentResolver,
        hygiene: ConfigHygiene | None = None,
    ) -> None:
        self._catalog = catalog
        self._instruments = instruments
        self._hygiene = hygiene or ConfigHygiene()

    def resolve(self, raw: Mapping[str, object], source: str = "config") -> ResolvedStrategyConfig:
        try:
            self._hygiene.check(raw)
            file = self._validate(StrategyFile, raw)
            parameters = self._parameters(file)
            universe = self._universe(file.universe)
            return ResolvedStrategyConfig(
                name=file.name,
                enabled=file.enabled,
                timeframe=file.timeframe,
                universe=universe,
                parameters=parameters,
                risk=file.risk,
                execution=file.execution,
                session=file.session,
            )
        except (StrategyConfigError, ValidationError, ValueError) as error:
            raise StrategyConfigError(f"{source}: {error}") from error

    @staticmethod
    def _validate(model: type[M], raw: Mapping[str, object]) -> M:
        try:
            return model.model_validate(raw)
        except ValidationError as error:
            raise StrategyConfigError(_describe(error)) from error

    def _parameters(self, file: StrategyFile) -> StrategyParameters:
        try:
            model = self._catalog.parameters_model(file.name)
        except LookupError as error:
            raise StrategyConfigError(str(error)) from error
        try:
            return model.model_validate(file.parameters)
        except ValidationError as error:
            raise StrategyConfigError(f"parameters: {_describe(error)}") from error

    def _universe(self, spec: StaticUniverse) -> tuple[UniverseMember, ...]:
        return tuple(self._member(symbol) for symbol in spec.instruments)

    def _member(self, symbol: str) -> UniverseMember:
        exchange_name, separator, tradingsymbol = symbol.partition(":")
        if not separator or not tradingsymbol:
            raise StrategyConfigError(f"'{symbol}' must be written EXCHANGE:TRADINGSYMBOL")
        try:
            exchange = Exchange(exchange_name)
        except ValueError:
            raise StrategyConfigError(f"'{symbol}': unknown exchange '{exchange_name}'") from None
        try:
            instrument = self._instruments.by_symbol(exchange, tradingsymbol)
        except UnknownInstrumentError:
            raise StrategyConfigError(f"'{symbol}' is not in the instrument master") from None
        return UniverseMember(symbol=symbol, instrument_id=instrument.instrument_id)


def _describe(error: ValidationError) -> str:
    """One line per problem: `risk.stop_loss_pct: must be greater than 0`."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'config'}: {item['msg']}"
        for item in error.errors()
    )
