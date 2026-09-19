"""Reads `config/strategies/*.yaml` into resolved strategy configs (plan.md §9).

File access lives here, in `session`, precisely so that `emporos.strategies` stays free of I/O.
The YAML loader is strict about what PyYAML forgives: a key repeated in one mapping is an error
(PyYAML would silently keep the last), and only `*.yaml` files are read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode, ScalarNode

from emporos.core.config import CONFIG_DIR
from emporos.strategies.config import ResolvedStrategyConfig
from emporos.strategies.resolution import StrategyConfigError, StrategyConfigResolver

STRATEGY_CONFIG_DIR = CONFIG_DIR / "strategies"


class _StrictLoader(yaml.SafeLoader):
    """`SafeLoader` that refuses a mapping with a repeated key."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, ScalarNode):
                continue
            if key_node.value in seen:
                raise ConstructorError(
                    None, None, f"duplicate key {key_node.value!r}", key_node.start_mark
                )
            seen.add(key_node.value)
        return super().construct_mapping(node, deep)


class StrategyConfigLoader:
    def __init__(
        self, resolver: StrategyConfigResolver, directory: Path = STRATEGY_CONFIG_DIR
    ) -> None:
        self._resolver = resolver
        self._directory = directory

    def load_file(self, path: Path) -> ResolvedStrategyConfig:
        try:
            raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
        except (yaml.YAMLError, OSError) as error:
            raise StrategyConfigError(f"{path.name}: {error}") from error
        if not isinstance(raw, dict):
            raise StrategyConfigError(f"{path.name}: a strategy file must be a YAML mapping")
        return self._resolver.resolve(raw, source=path.name)

    def load_all(self) -> list[ResolvedStrategyConfig]:
        """Every strategy file, validated (enabled or not), in file-name order."""
        configs = [self.load_file(path) for path in sorted(self._directory.glob("*.yaml"))]
        names = [config.name for config in configs]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise StrategyConfigError(f"strategy name defined in more than one file: {duplicates}")
        return configs

    def load_enabled(self) -> list[ResolvedStrategyConfig]:
        return [config for config in self.load_all() if config.enabled]
