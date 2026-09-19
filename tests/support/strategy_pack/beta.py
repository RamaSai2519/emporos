from __future__ import annotations

from abc import abstractmethod

from tests.support.strategies import ScriptedStrategy


class _AbstractBase(ScriptedStrategy):
    name = "abstract_base"

    @abstractmethod
    def extra(self) -> None: ...


class BetaStrategy(ScriptedStrategy):
    name = "beta"


class NotAStrategy:
    name = "nope"
