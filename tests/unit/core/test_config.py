from pathlib import Path

import pytest

from emporos.core.config import (
    Settings,
    load_yaml_config,
    resolve_db_name,
    resolve_profile,
)


@pytest.mark.parametrize(
    ("env", "expected_db"),
    [
        (None, "emporos_dev"),
        ("", "emporos_dev"),
        ("local", "emporos_dev"),
        ("dev", "emporos_dev"),
        ("staging", "emporos_dev"),
        ("ci", "emporos_dev"),
        ("main", "emporos"),
    ],
)
def test_resolve_db_name_only_main_is_production(env: str | None, expected_db: str) -> None:
    assert resolve_db_name(env) == expected_db


@pytest.mark.parametrize(
    ("env", "expected_profile"),
    [
        (None, "local"),
        ("local", "local"),
        ("dev", "local"),
        ("staging", "staging"),
        ("main", "production"),
    ],
)
def test_resolve_profile(env: str | None, expected_profile: str) -> None:
    assert resolve_profile(env) == expected_profile


def test_load_yaml_config_merges_base_and_profile_overlay() -> None:
    config = load_yaml_config("production")
    assert config["log_level"] == "WARNING"
    assert config["timezone"] == "Asia/Kolkata"  # from base, not overridden


def test_load_yaml_config_missing_overlay_falls_back_to_base(tmp_path: Path) -> None:
    (tmp_path / "settings.base.yaml").write_text("log_level: INFO\n")
    config = load_yaml_config("nonexistent-profile", config_dir=tmp_path)
    assert config == {"log_level": "INFO"}


def test_settings_env_unset_defaults_to_non_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV", raising=False)
    settings = Settings(_env_file=None)
    assert settings.env is None
    assert settings.db_name == "emporos_dev"
    assert settings.profile == "local"


def test_settings_env_main_resolves_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "main")
    settings = Settings(_env_file=None)
    assert settings.db_name == "emporos"
    assert settings.profile == "production"


def test_settings_reads_mongo_url_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGO_URL", "mongodb+srv://example/")
    settings = Settings(_env_file=None)
    assert settings.mongo_url == "mongodb+srv://example/"
