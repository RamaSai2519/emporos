"""`JevConfig` — every experimental control the epic asks for (EM-152 / EM-160), validated at
construction so a bad setting fails at startup, not mid-run. `enabled=False` (the default) is
what makes Jev genuinely optional: nothing downstream needs to branch on whether the config file
even mentions Jev.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.core.errors import ConfigurationError
from emporos.jev.models import CONFIRMATION, RANKING, STRATEGY_SELECTION

_MODES = frozenset({CONFIRMATION, RANKING, STRATEGY_SELECTION})


@dataclass(frozen=True)
class JevConfig:
    enabled: bool = False
    mode: str = CONFIRMATION
    confidence_threshold: float = 0.6
    # Live trading must default to fail-closed (EM-161): a Jev failure blocks the candidate
    # rather than letting it through unreviewed. Research/backtest callers may opt into
    # fail-open explicitly, since a stalled experiment is a worse outcome there than a false
    # rejection.
    fail_open: bool = False
    max_concurrency: int = 4
    timeout_seconds: float = 5.0
    max_retries: int = 2
    base_url: str = "https://gateway.ai.vercel.sh"
    model: str = "openai/gpt-4o-mini"
    # Experiment provenance (EM-187). Live config is not forced to carry these; a Jev EXPERIMENT
    # refuses to run without a declared cutoff (`emporos.jev.leakage`), and cost analysis refuses
    # to run without a declared rate rather than silently pricing Jev at zero.
    model_knowledge_cutoff: date | None = None
    inr_per_1k_tokens: Decimal | None = None

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ConfigurationError(
                f"unknown Jev mode {self.mode!r}; must be one of {sorted(_MODES)}"
            )
        if not (0.0 <= self.confidence_threshold <= 1.0):
            raise ConfigurationError("Jev confidence_threshold must be between 0 and 1")
        if self.max_concurrency < 1:
            raise ConfigurationError("Jev max_concurrency must be at least 1")
        if self.timeout_seconds <= 0:
            raise ConfigurationError("Jev timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ConfigurationError("Jev max_retries cannot be negative")
        if not self.base_url:
            raise ConfigurationError("Jev base_url cannot be empty")
        if not self.model:
            raise ConfigurationError("Jev model cannot be empty")
        if self.inr_per_1k_tokens is not None and self.inr_per_1k_tokens < 0:
            raise ConfigurationError("Jev inr_per_1k_tokens cannot be negative")


class JevCredentialsMissing(ConfigurationError):
    """Jev is enabled but no Vercel Gateway API key is configured. Raised at startup, with an
    actionable message, never discovered mid-run as a mysterious 401."""

    def __init__(self) -> None:
        super().__init__(
            "Jev is enabled but VERCEL_GATEWAY_KEY is not set. Set it in the root .env for local "
            "development, or in the deployment environment's secret configuration in production."
        )


def require_credentials(config: JevConfig, api_key: str | None) -> str:
    """Validates configuration at startup (EM-160: "provide actionable errors when Jev is
    enabled but credentials/configuration are missing"). Only called when `config.enabled`."""
    if not api_key:
        raise JevCredentialsMissing()
    return api_key
