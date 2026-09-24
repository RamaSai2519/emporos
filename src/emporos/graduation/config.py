"""`config/graduation.yaml`: how fresh broker evidence must be, and what data assumptions are
tolerated. Exact numbers, unknown keys refused, like every other config document."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from emporos.core.config import CONFIG_DIR
from emporos.core.errors import ConfigurationError
from emporos.strategies.config import PositiveInt

DEFAULT_GRADUATION_FILE = CONFIG_DIR / "graduation.yaml"


class GraduationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    broker_verification_max_age_days: PositiveInt
    allowed_assumed_instruments: tuple[str, ...] = Field(default=())

    @property
    def broker_verification_max_age(self) -> timedelta:
        return timedelta(days=self.broker_verification_max_age_days)


class GraduationSettingsLoader:
    def __init__(self, path: Path = DEFAULT_GRADUATION_FILE) -> None:
        self._path = path

    def load(self) -> GraduationSettings:
        try:
            document = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read {self._path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{self._path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{self._path} must be a mapping")
        try:
            return GraduationSettings.model_validate(document)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise ConfigurationError(
                f"invalid graduation settings in {self._path}: {problems}"
            ) from error
