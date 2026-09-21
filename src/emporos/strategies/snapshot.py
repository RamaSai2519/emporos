"""Config snapshots: a run's fully-resolved configuration, frozen with a content hash (plan.md §9).

At `strategy_run` start the resolved config is turned into a canonical, JSON-safe document and
hashed. The document is what Mongo stores in `strategy_runs.config_snapshot`; the hash lets anyone
prove later that it is the config the run actually used. `restore` rebuilds an identical
`ResolvedStrategyConfig` from the document ALONE — no YAML file, no instrument master — so a run
can be reproduced after the YAML has changed, symbols have been renamed, or tokens reassigned.

Canonical form: strings, ints, bools, None, lists and string-keyed dicts only. Decimals are
normalised strings (`1.0` and `1` are the same config), times are `"HH:MM"`, enums are their value,
and a float anywhere is an error. The hash is SHA-256 over compact, key-sorted JSON.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ValidationError

from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import ParametersCatalog, StrategyConfigError

SCHEMA_VERSION = 1
HASH_PREFIX = "sha256:"

Canonical = str | int | bool | None | list["Canonical"] | dict[str, "Canonical"]


class SnapshotIntegrityError(ValueError):
    """A stored snapshot does not match its recorded hash: it was altered or corrupted."""


@dataclass(frozen=True)
class ConfigSnapshot:
    document: dict[str, Canonical]
    content_hash: str

    @property
    def behaviour_hash(self) -> str:
        """The hash of what the strategy DOES: the same document with `enabled` left out.

        Switching a strategy on does not change its behaviour, so it must not change what a recorded
        backtest verdict is bound to. Everything else (parameters, universe, risk, execution,
        session) does, and editing any of it makes the verdict stale.
        """
        return ConfigSnapshotter.hash_of({k: v for k, v in self.document.items() if k != "enabled"})


class Canonicalizer:
    def canonical(self, value: object) -> Canonical:
        if isinstance(value, bool) or value is None or isinstance(value, int | str):
            return value
        if isinstance(value, float):
            raise StrategyConfigError(f"a float ({value!r}) cannot be part of a config snapshot")
        if isinstance(value, Decimal):
            return self._decimal(value)
        if isinstance(value, Enum):
            return self.canonical(value.value)
        if isinstance(value, time):
            return value.strftime("%H:%M")
        if isinstance(value, BaseModel):
            return self.canonical(dict(value))
        if isinstance(value, Mapping):
            return {str(key): self.canonical(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.canonical(item) for item in value]
        raise StrategyConfigError(f"cannot snapshot a {type(value).__name__}")

    @staticmethod
    def _decimal(value: Decimal) -> str:
        return format(value.normalize(), "f") if value != 0 else "0"


class ConfigSnapshotter:
    def __init__(self, canonicalizer: Canonicalizer | None = None) -> None:
        self._canonicalizer = canonicalizer or Canonicalizer()

    def take(self, config: ResolvedStrategyConfig) -> ConfigSnapshot:
        body = self._canonicalizer.canonical(config.model_dump(mode="python"))
        assert isinstance(body, dict)
        document: dict[str, Canonical] = {"schema_version": SCHEMA_VERSION, **body}
        return ConfigSnapshot(document, self.hash_of(document))

    @staticmethod
    def hash_of(document: Mapping[str, Canonical]) -> str:
        text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def restore(
        self,
        document: Mapping[str, object],
        catalog: ParametersCatalog,
        expected_hash: str | None = None,
    ) -> ResolvedStrategyConfig:
        """Rebuild the config a run used. With `expected_hash`, refuse a document that changed."""
        canonical = self._canonicalizer.canonical(document)
        assert isinstance(canonical, dict)
        if expected_hash is not None and self.hash_of(canonical) != expected_hash:
            raise SnapshotIntegrityError("config snapshot does not match its recorded hash")
        if canonical.get("schema_version") != SCHEMA_VERSION:
            raise StrategyConfigError(
                f"snapshot schema {canonical.get('schema_version')!r} is not {SCHEMA_VERSION}"
            )
        payload: dict[str, object] = {k: v for k, v in canonical.items() if k != "schema_version"}
        try:
            model = catalog.parameters_model(str(payload.get("name")))
            payload["parameters"] = model.model_validate(payload.get("parameters", {}))
            return ResolvedStrategyConfig.model_validate(payload)
        except (ValidationError, LookupError) as error:
            raise StrategyConfigError(f"snapshot cannot be restored: {error}") from error
