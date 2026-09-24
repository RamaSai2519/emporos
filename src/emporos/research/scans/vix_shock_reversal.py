"""The first-hour gap reversal, only on high-INDIA-VIX days (EM-191, cell L4-india-vix-regime).

The rules are `ShockReversalRules` unchanged; the only addition is the day gate (see
`regime_gate`): the INDIA VIX 09:15 open must be at or above `vix_min_percentile` of its own opens
over the previous 252 sessions. Not parity-proven against an engine strategy, so advisory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from emporos.research.scans.base import IntradayScan, ScanExecution
from emporos.research.scans.regime_gate import DayGate, DayGatedRules
from emporos.research.scans.shock_reversal import ShockReversalParameters, ShockReversalRules

__all__ = ["VixReversalParameters", "declared_arms", "vix_shock_reversal_scan"]


@dataclass(frozen=True)
class VixReversalParameters:
    reversal: ShockReversalParameters
    vix_min_percentile: Decimal

    def __post_init__(self) -> None:
        if not Decimal(0) < self.vix_min_percentile < Decimal(1):
            raise ValueError("the VIX percentile must be strictly between 0 and 1")

    @classmethod
    def from_point(cls, point: Mapping[str, str]) -> VixReversalParameters:
        return cls(ShockReversalParameters.from_point(point), Decimal(point["vix_min_percentile"]))

    def as_point(self) -> dict[str, str]:
        return {**self.reversal.as_point(), "vix_min_percentile": str(self.vix_min_percentile)}


def declared_arms(grid: Mapping[str, tuple[str, ...]]) -> list[VixReversalParameters]:
    """Every arm of the declared grid. The declaration must name exactly this cell's parameters."""
    expected = ["gap_threshold_pct", "retrace_min", "vix_min_percentile"]
    if sorted(grid) != sorted(expected):
        raise ValueError(f"the declared grid must name exactly {expected}, not {sorted(grid)}")
    points = product(*(grid[name] for name in expected))
    return [VixReversalParameters.from_point(dict(zip(expected, p, strict=True))) for p in points]


def vix_shock_reversal_scan(
    parameters: VixReversalParameters, execution: ScanExecution, gate: DayGate
) -> IntradayScan:
    return IntradayScan(
        "vix_gated_shock_reversal",
        lambda: DayGatedRules(
            ShockReversalRules(parameters.reversal, execution.no_new_entries_after), gate
        ),
        execution,
    )
