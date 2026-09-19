"""A strategy must be structurally unable to reach a broker (plan.md §9, EM-66 acceptance).

Three independent proofs, none of which runs the strategy:
1. the context's fields are an exact, reviewed list — adding one is a deliberate test change;
2. walking everything reachable from a built context finds no broker object, no object from
   `emporos.broker`/`emporos.persistence`, and no order-writing method;
3. importing the strategy framework in a fresh interpreter loads no broker or storage module.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import typing
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from emporos.broker.base import Broker
from emporos.core.clock import FixedClock
from emporos.strategies.context import StrategyContext
from tests.support.in_memory_broker import InMemoryBroker
from tests.support.strategies import T0, make_context

ORDER_VERBS = ("place_order", "modify_order", "cancel_order", "subscribe", "login", "logout")
FORBIDDEN_MODULE_PREFIXES = ("emporos.broker", "emporos.persistence", "emporos.execution")
EXPECTED_FIELDS = {
    "run_id", "config", "clock", "logger", "history", "positions", "rng",
}  # fmt: skip


def _reachable(root: object, seen: set[int] | None = None) -> Iterator[object]:
    """Every object reachable through attributes, dataclass fields, slots and containers."""
    seen = set() if seen is None else seen
    if id(root) in seen:
        return
    seen.add(id(root))
    yield root
    if isinstance(root, dict):
        children: list[Any] = [*root.keys(), *root.values()]
    elif isinstance(root, list | tuple | set | frozenset):
        children = list(root)
    else:
        children = list(_attribute_values(root))
    for child in children:
        yield from _reachable(child, seen)


def _attribute_values(obj: object) -> Iterator[object]:
    names = set(getattr(obj, "__dict__", {}))
    for cls in type(obj).__mro__:
        names.update(getattr(cls, "__slots__", ()))
    for name in names:
        if name.startswith("__"):
            continue
        try:
            yield getattr(obj, name)
        except AttributeError:
            continue


def test_the_context_has_exactly_the_reviewed_fields() -> None:
    assert {f.name for f in dataclasses.fields(StrategyContext)} == EXPECTED_FIELDS


def test_no_field_is_annotated_with_a_broker_or_storage_type() -> None:
    hints = typing.get_type_hints(StrategyContext)
    for name, hint in hints.items():
        for part in (hint, *typing.get_args(hint)):
            module = getattr(part, "__module__", "")
            assert not module.startswith(FORBIDDEN_MODULE_PREFIXES), f"{name}: {part}"


def test_nothing_reachable_from_a_built_context_is_a_broker_or_can_place_an_order() -> None:
    ctx = make_context(FixedClock(T0))

    for obj in _reachable(ctx):
        assert not isinstance(obj, Broker | InMemoryBroker), f"reaches a broker: {obj!r}"
        module = type(obj).__module__
        assert not module.startswith(FORBIDDEN_MODULE_PREFIXES), f"{type(obj)} from {module}"
        for verb in ORDER_VERBS:
            assert not hasattr(obj, verb), f"{type(obj).__name__}.{verb} exists"


def test_the_context_is_immutable_so_a_strategy_cannot_attach_a_broker_to_it() -> None:
    ctx = make_context(FixedClock(T0))
    for attempt in ("broker", "history"):
        try:
            setattr(ctx, attempt, object())
        except (AttributeError, TypeError):
            continue
        raise AssertionError(f"could set ctx.{attempt}")


def test_importing_the_strategy_framework_loads_no_broker_or_storage_module() -> None:
    code = (
        "import sys, pkgutil, importlib, emporos.strategies as pkg\n"
        "for m in pkgutil.walk_packages(pkg.__path__, 'emporos.strategies.'):\n"
        "    importlib.import_module(m.name)\n"
        "bad = sorted(n for n in sys.modules if n.startswith("
        "('emporos.broker', 'emporos.persistence', 'emporos.execution', 'emporos.marketdata', "
        "'pymongo', 'httpx', 'boto3', 'yaml')))\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == ""


def test_the_fixed_clock_in_the_context_is_the_only_source_of_now() -> None:
    clock = FixedClock(T0)
    ctx = make_context(clock)
    clock.advance(timedelta(minutes=3))
    assert ctx.clock.now() == T0 + timedelta(minutes=3)
