from __future__ import annotations

from tests.support.strategies import ScriptedStrategy, ThresholdStrategy  # re-exported names


class AlphaStrategy(ScriptedStrategy):
    name = "alpha"


__all__ = ["AlphaStrategy", "ScriptedStrategy", "ThresholdStrategy"]
