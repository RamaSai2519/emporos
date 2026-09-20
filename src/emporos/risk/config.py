"""Loads `RiskLimits` from YAML. The only file access in the risk package, kept out of the rules.

Numbers must be quoted strings or integers: a bare YAML float (`2.5`) is refused, because money
must never pass through binary floating point (plan.md §6). Unknown keys are refused too, so a
typo'd limit name cannot silently leave a limit unset.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from emporos.core.config import CONFIG_DIR
from emporos.core.errors import ConfigurationError
from emporos.risk.limits import RiskLimits

DEFAULT_RISK_FILE = CONFIG_DIR / "risk.yaml"


class RiskLimitsLoader:
    def __init__(self, path: Path = DEFAULT_RISK_FILE) -> None:
        self._path = path

    def load(self) -> RiskLimits:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigurationError(f"cannot read risk limits {self._path}: {error}") from error
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{self._path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{self._path} must be a mapping of limit names to numbers")
        try:
            return RiskLimits.model_validate(document)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise ConfigurationError(f"invalid risk limits in {self._path}: {problems}") from error
