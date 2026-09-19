"""Composition root for strategies: the one place that finds strategy classes and wires the
registry (plan.md §9: adding a strategy is adding a class and a YAML file, nothing more).

Discovery imports every module of a package and collects the concrete `Strategy` subclasses DEFINED
there (not re-exports), in name order, so the registry is identical on every run.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import ModuleType

import emporos.strategies.builtin as builtin_package
from emporos.strategies.base import Strategy
from emporos.strategies.registry import StrategyRegistry


class StrategyDiscovery:
    def discover(self, package: ModuleType) -> list[type[Strategy]]:
        found: dict[str, type[Strategy]] = {}
        for info in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
            module = importlib.import_module(info.name)
            for _, member in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(member, Strategy)
                    and member.__module__ == module.__name__
                    and not inspect.isabstract(member)
                ):
                    found[f"{member.name}"] = member
        return [found[name] for name in sorted(found)]


def build_registry(package: ModuleType = builtin_package) -> StrategyRegistry:
    """A fresh registry holding every strategy in `package` (the built-ins by default)."""
    registry = StrategyRegistry()
    registry.register_all(StrategyDiscovery().discover(package))
    return registry
