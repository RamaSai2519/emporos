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


def resolve_profile(env: str | None) -> str:
    """The config-overlay profile for a given `ENV` value."""
    if env == PRODUCTION_ENV_VALUE:
        return "production"
    if env == "staging":
        return "staging"
    return "local"


def resolve_db_name(env: str | None) -> str:
    """The Mongo database name for a given `ENV` value. See plan.md §6.0."""
    return "emporos" if env == PRODUCTION_ENV_VALUE else "emporos_dev"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_yaml_config(profile: str, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """`settings.base.yaml` deep-merged with `settings.<profile>.yaml`."""
    base = _load_yaml(config_dir / "settings.base.yaml")
    overlay = _load_yaml(config_dir / f"settings.{profile}.yaml")
    return _deep_merge(base, overlay)


class Settings(BaseSettings):
    """Secrets and process-level config, sourced from the environment / `.env`.

    Non-secret, environment-varying settings (log level, feature flags) live in
    `config/settings.*.yaml` instead — see `load_yaml_config`. `Settings` only
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
        return resolve_profile(self.env)

    @property
    def db_name(self) -> str:
        return resolve_db_name(self.env)

    @property
    def yaml_config(self) -> dict[str, Any]:
        return load_yaml_config(self.profile)


@lru_cache
def get_settings() -> Settings:
    return Settings()
