"""Loads `RiskLimits` from YAML. The only file access in the risk package, kept out of the rules.

Numbers must be quoted strings or integers: a bare YAML float (`2.5`) is refused, because money
must never pass through binary floating point (plan.md §6). Unknown keys are refused too, so a
typo'd limit name cannot silently leave a limit unset.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import ValidationError

from emporos.core.config import CONFIG_DIR
from emporos.core.errors import ConfigurationError
from emporos.risk.limits import RiskLimits

DEFAULT_RISK_FILE = CONFIG_DIR / "risk.yaml"


class RiskTier(StrEnum):
    """Which set of caps a worker trades under. Live starts in the stricter tier (EM-189)."""

    STANDARD = "standard"
    LIVE_CONSERVATIVE = "live_conservative"


_TIER_FILES = {
    RiskTier.STANDARD: "risk.yaml",
    RiskTier.LIVE_CONSERVATIVE: "risk.live_conservative.yaml",
}


class RiskLimitsLoader:
    def __init__(self, path: Path = DEFAULT_RISK_FILE, *, ceiling: Path | None = None) -> None:
        self._path = path
        self._ceiling = ceiling

    @classmethod
    def for_tier(cls, tier: RiskTier, directory: Path = CONFIG_DIR) -> RiskLimitsLoader:
        """The loader for a tier. A non-standard tier must sit within the standard limits."""
        path = directory / _TIER_FILES[tier]
        ceiling = None if tier is RiskTier.STANDARD else directory / _TIER_FILES[RiskTier.STANDARD]
        return cls(path, ceiling=ceiling)

    def load(self) -> RiskLimits:
        limits = self._read(self._path)
        if self._ceiling is not None and not limits.is_within(self._read(self._ceiling)):
            raise ConfigurationError(
                f"risk tier {self._path} is looser than {self._ceiling}: a stricter tier "
                "may never raise a cap"
            )
        return limits

    @staticmethod
    def _read(path: Path) -> RiskLimits:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigurationError(f"cannot read risk limits {path}: {error}") from error
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{path} must be a mapping of limit names to numbers")
        try:
            limits = RiskLimits.model_validate(document)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
            raise ConfigurationError(f"invalid risk limits in {path}: {problems}") from error
        if contradictions := limits.consistency_problems():
            raise ConfigurationError(
                f"inconsistent risk limits in {path}: {'; '.join(contradictions)}"
            )
        return limits
