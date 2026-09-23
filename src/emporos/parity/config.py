"""The parity bounds, loaded from `config/parity.yaml`: exact numbers, unknown keys refused."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from emporos.core.config import CONFIG_DIR
from emporos.core.errors import ConfigurationError
from emporos.strategies.config import ExactDecimal, PositiveInt

DEFAULT_PARITY_FILE = CONFIG_DIR / "parity.yaml"


class ParityThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    min_sessions: PositiveInt
    min_matched_trades: PositiveInt
    min_signal_agreement: ExactDecimal = Field(gt=0, le=1)
    max_fill_rate_drop: ExactDecimal = Field(ge=0, le=1)
    max_slippage_increase_bps: ExactDecimal = Field(ge=0)
    max_expectancy_drop_fraction: ExactDecimal = Field(ge=0)
    max_drawdown_increase_fraction: ExactDecimal = Field(ge=0)
    max_win_rate_drop: ExactDecimal = Field(ge=0, le=1)
    max_turnover_ratio: ExactDecimal = Field(gt=Decimal(0))


class ParityThresholdsLoader:
    def __init__(self, path: Path = DEFAULT_PARITY_FILE) -> None:
        self._path = path

    def load(self) -> ParityThresholds:
        try:
            document = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read parity bounds {self._path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{self._path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{self._path} must be a mapping of bound names to numbers")
        try:
            return ParityThresholds.model_validate(document)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise ConfigurationError(
                f"invalid parity bounds in {self._path}: {problems}"
            ) from error
