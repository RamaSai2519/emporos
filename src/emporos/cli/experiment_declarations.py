"""Loads an experiment declaration from `config/experiments/<slug>.yaml` (EM-188).

A declaration is what was claimed BEFORE the run, so it is read as strictly as the risk limits and
the strategy configs are: unknown keys are refused (a typo'd field cannot silently go missing), a
bare YAML float is refused (a parameter value must be an integer or a quoted string, so it means
exactly what it says), and the file's name must be its slug (one file, one experiment).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from emporos.cli.experiment_provenance import GitRepository
from emporos.core.config import CONFIG_DIR
from emporos.core.errors import ConfigurationError
from emporos.domain.research_experiments import ExperimentDeclaration, ExperimentFamily

DEFAULT_DECLARATIONS_DIR = CONFIG_DIR / "experiments"

_KEYS = frozenset(
    {
        "family",
        "slug",
        "hypothesis",
        "economic_rationale",
        "falsification",
        "parameter_grid",
        "feature_versions",
        "declared_at",
        "position_value",
    }
)
_REQUIRED = _KEYS - {"parameter_grid", "feature_versions", "position_value"}


class ExperimentDeclarationLoader:
    def load(self, path: Path) -> ExperimentDeclaration:
        document = self._read(path)
        unknown = sorted(set(document) - _KEYS)
        if unknown:
            raise ConfigurationError(f"{path}: unknown key(s) {', '.join(unknown)}")
        missing = sorted(_REQUIRED - set(document))
        if missing:
            raise ConfigurationError(f"{path}: missing {', '.join(missing)}")
        if document["slug"] != path.stem:
            raise ConfigurationError(
                f"{path}: the slug {document['slug']!r} must be the file's name ({path.stem!r})"
            )
        try:
            return ExperimentDeclaration(
                family=self._family(path, document["family"]),
                slug=self._text(path, "slug", document["slug"]),
                hypothesis=self._text(path, "hypothesis", document["hypothesis"]),
                economic_rationale=self._text(
                    path, "economic_rationale", document["economic_rationale"]
                ),
                falsification=self._text(path, "falsification", document["falsification"]),
                parameter_grid=self._grid(path, document.get("parameter_grid") or {}),
                feature_versions=self._versions(path, document.get("feature_versions") or {}),
                declared_at=self._moment(path, document["declared_at"]),
                position_value=self._money(path, document.get("position_value")),
            )
        except ValueError as error:
            raise ConfigurationError(f"{path}: {error}") from error

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read declaration {path}: {error}") from error
        except yaml.YAMLError as error:
            raise ConfigurationError(f"{path} is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConfigurationError(f"{path} must be a mapping of declaration fields")
        return document

    @staticmethod
    def _family(path: Path, value: object) -> ExperimentFamily:
        try:
            return ExperimentFamily(str(value))
        except ValueError:
            names = ", ".join(f.value for f in ExperimentFamily)
            raise ConfigurationError(f"{path}: family must be one of {names}") from None

    @staticmethod
    def _text(path: Path, name: str, value: object) -> str:
        if not isinstance(value, str):
            raise ConfigurationError(f"{path}: {name} must be text")
        return value

    @staticmethod
    def _scalar(path: Path, where: str, value: object) -> str:
        if isinstance(value, bool | float) or not isinstance(value, int | str):
            raise ConfigurationError(
                f"{path}: {where} must be an integer or a quoted string, not {value!r}"
            )
        return str(value)

    def _grid(self, path: Path, raw: object) -> dict[str, tuple[str, ...]]:
        if not isinstance(raw, dict):
            raise ConfigurationError(f"{path}: parameter_grid must map names to lists of values")
        grid: dict[str, tuple[str, ...]] = {}
        for name, values in raw.items():
            if not isinstance(values, list) or not values:
                raise ConfigurationError(f"{path}: parameter_grid.{name} must be a non-empty list")
            grid[str(name)] = tuple(self._scalar(path, f"parameter_grid.{name}", v) for v in values)
        return grid

    def _versions(self, path: Path, raw: object) -> dict[str, str]:
        if not isinstance(raw, dict):
            raise ConfigurationError(f"{path}: feature_versions must map names to versions")
        return {
            str(name): self._scalar(path, f"feature_versions.{name}", version)
            for name, version in raw.items()
        }

    def _money(self, path: Path, value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(self._scalar(path, "position_value", value))
        except InvalidOperation:
            raise ConfigurationError(f"{path}: position_value is not a number: {value!r}") from None

    @staticmethod
    def _moment(path: Path, value: object) -> datetime:
        try:
            moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except ValueError:
            raise ConfigurationError(f"{path}: declared_at is not an ISO-8601 time") from None
        if moment.tzinfo is None:
            raise ConfigurationError(f"{path}: declared_at needs a UTC offset, e.g. +05:30")
        return moment


class DeclarationGate:
    """Loads a declaration and, unless told otherwise, refuses one that was not committed first: a
    claim that can still be edited after the result is known is not a pre-declaration."""

    def __init__(self, loader: ExperimentDeclarationLoader, git: GitRepository) -> None:
        self._loader = loader
        self._git = git

    def load(self, path: Path, *, allow_uncommitted: bool = False) -> ExperimentDeclaration:
        declared = self._loader.load(path)
        if not allow_uncommitted and not self._git.is_committed(path):
            raise ConfigurationError(
                f"{path} is not committed: commit the declaration before the run "
                "(or pass --allow-uncommitted-declaration, which forfeits the proof)"
            )
        return declared
