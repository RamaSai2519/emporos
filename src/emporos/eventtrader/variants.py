"""The Dev variants the declaration counts (EM-240): the pipeline settings each one runs with."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["VARIANTS", "VariantSpec", "variant"]


@dataclass(frozen=True)
class VariantSpec:
    name: str
    triage_threshold: int
    panel: bool
    posture: bool


VARIANTS = {
    v.name: v
    for v in (
        VariantSpec("v1_t60", 60, True, False),
        VariantSpec("v2_t75", 75, True, False),
        VariantSpec("v3_nopanel_t60", 60, False, False),
        VariantSpec("v4_posture_t60", 60, True, True),
        VariantSpec("v5_posture_t75", 75, True, True),
    )
}  # v6_arbiter is the arbiter A/B on the best of these, and has its own gate


def variant(name: str) -> VariantSpec:
    try:
        return VARIANTS[name]
    except KeyError:
        raise ValueError(f"unknown variant {name!r}; known: {', '.join(VARIANTS)}") from None
