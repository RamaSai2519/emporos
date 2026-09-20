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

    # Overrides the upstream instrument-master URL (tests / mirrors); unset uses Angel One's.
    instrument_master_url: str | None = Field(default=None, alias="INSTRUMENT_MASTER_URL")

    # Cold storage (candle Parquet archive, backups). `S3_ENDPOINT_URL` points at a local
    # S3-compatible target (MinIO/moto) in development; unset means real AWS S3.
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")
    s3_access_key_id: str | None = Field(default=None, alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = Field(default=None, alias="S3_SECRET_ACCESS_KEY")
    aws_region: str = Field(default="ap-south-1", alias="AWS_REGION")

    angelone_client_code: str | None = Field(default=None, alias="ANGELONE_CLIENT_CODE")
    angelone_password: str | None = Field(default=None, alias="ANGELONE_PASSWORD")
    angelone_totp_secret: str | None = Field(default=None, alias="ANGELONE_TOTP_SECRET")
    angelone_api_key: str | None = Field(default=None, alias="ANGELONE_API_KEY")
    # The `X-Client*` headers. Only order APIs check them against the registered static IP, so
    # they are optional for login/quotes/history and must be the worker's real ones for orders.
    angelone_local_ip: str | None = Field(default=None, alias="ANGELONE_CLIENT_LOCAL_IP")
    angelone_public_ip: str | None = Field(default=None, alias="ANGELONE_CLIENT_PUBLIC_IP")
    angelone_mac_address: str | None = Field(default=None, alias="ANGELONE_CLIENT_MAC_ADDRESS")

    # Real orders are inert unless this is exactly "true" (plan.md §11 TradingModeGuard). Kept as
    # text so that "1", "yes" or a typo can never enable live trading by accident.
    live_trading_flag: str | None = Field(default=None, alias="LIVE_TRADING_ENABLED")
    # The kill switch's file sentinel: while this file exists, all new orders are halted.
    kill_switch_file: str | None = Field(default=None, alias="KILL_SWITCH_FILE")

    # The control-plane API. The signing secret comes from the environment here and from SSM
    # Parameter Store in production; it is never written to Git. CORS origins are comma-separated.
    api_jwt_secret: str | None = Field(default=None, alias="API_JWT_SECRET")
    api_cors_origins: str | None = Field(default=None, alias="API_CORS_ORIGINS")
    # The account whose books the API shows (the paper account id, or the live client code).
    api_account_id: str | None = Field(default=None, alias="API_ACCOUNT_ID")

    @property
    def live_trading_enabled(self) -> bool:
        return self.live_trading_flag == "true"

    @property
    def kill_switch_path(self) -> Path:
        if self.kill_switch_file:
            return Path(self.kill_switch_file).expanduser()
        return Path.home() / ".emporos" / "HALT"

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
