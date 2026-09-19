"""Configuration loading: environment-overlay YAML for non-secret settings,
environment variables for secrets, and the `ENV` → database-name resolution
that replaces a per-environment MongoDB deployment (plan.md §6.0 / EM-11).

`ENV=main` is the *only* value that resolves to the production database and
the "production" config profile. Every other value — unset, `local`, `dev`,
`staging`, CI's default — fails safe into the non-production side. This is
deliberate: forgetting to set `ENV` must never reach production.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCTION_ENV_VALUE = "main"

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = REPO_ROOT / "config"


class Environment:
    """Resolves an `ENV` value into the config-overlay profile and Mongo database name.

    Embodies the fail-safe rule that only `ENV=main` selects production.
    """

    def __init__(self, env: str | None) -> None:
        self._env = env

    @property
    def profile(self) -> str:
        if self._env == PRODUCTION_ENV_VALUE:
            return "production"
        if self._env == "staging":
            return "staging"
        return "local"

    @property
    def db_name(self) -> str:
        return "emporos" if self._env == PRODUCTION_ENV_VALUE else "emporos_dev"


class YamlConfigLoader:
    """Deep-merges `settings.base.yaml` with `settings.<profile>.yaml`."""

    def __init__(self, config_dir: Path = CONFIG_DIR) -> None:
        self._config_dir = config_dir

    def load(self, profile: str) -> dict[str, Any]:
        base = self._load_file("settings.base.yaml")
        overlay = self._load_file(f"settings.{profile}.yaml")
        return self._deep_merge(base, overlay)

    def _load_file(self, name: str) -> dict[str, Any]:
        path = self._config_dir / name
        if not path.is_file():
            return {}
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in overlay.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = YamlConfigLoader._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged


class Settings(BaseSettings):
    """Secrets and process-level config, sourced from the environment / `.env`.

    Non-secret, environment-varying settings (log level, feature flags) live in
    `config/settings.*.yaml` instead — see `YamlConfigLoader`. `Settings` only
    owns what must come from the process environment: connection strings and
    credentials.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str | None = Field(default=None, alias="ENV")
    mongo_url: str | None = Field(default=None, alias="MONGO_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    angelone_client_code: str | None = Field(default=None, alias="ANGELONE_CLIENT_CODE")
    angelone_password: str | None = Field(default=None, alias="ANGELONE_PASSWORD")
    angelone_totp_secret: str | None = Field(default=None, alias="ANGELONE_TOTP_SECRET")
    angelone_api_key: str | None = Field(default=None, alias="ANGELONE_API_KEY")

    @property
    def profile(self) -> str:
        return Environment(self.env).profile

    @property
    def db_name(self) -> str:
        return Environment(self.env).db_name

    @property
    def yaml_config(self) -> dict[str, Any]:
        return YamlConfigLoader().load(self.profile)

    @classmethod
    @lru_cache
    def default(cls) -> Settings:
        """The process-wide `Settings`, loaded and cached once (composition root)."""
        return cls()
