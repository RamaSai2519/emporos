"""What a Jev experiment must declare about its model before it runs (EM-187).

The experiment declaration (EM-188) is the committed, content-hashed claim. Jev's part of it rides
in the declaration's `parameter_grid`, so the model, its knowledge cutoff and the source that
cutoff was verified against are part of the experiment's identity: changing any of them is a new
experiment. Nothing here knows a cutoff date; it only refuses to proceed without a declared one.

    parameter_grid:
      model: ["<gateway model id>"]
      model_knowledge_cutoff: ["<ISO date>"]
      cutoff_source: ["<where the provider publishes it, and when it was checked>"]
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

from emporos.domain.research_experiments import ExperimentDeclaration, ExperimentFamily
from emporos.jev.config import JevConfig
from emporos.jev.leakage import JevLeakageError

MODEL_KEY = "model"
CUTOFF_KEY = "model_knowledge_cutoff"
CUTOFF_SOURCE_KEY = "cutoff_source"


@dataclass(frozen=True)
class ModelCutoffDeclaration:
    model: str
    knowledge_cutoff: date
    source: str

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise JevLeakageError("a Jev experiment must declare its model")
        if not self.source.strip():
            raise JevLeakageError(
                "the model's knowledge cutoff must be declared with the source it was verified "
                "against"
            )

    @classmethod
    def from_declaration(cls, declaration: ExperimentDeclaration) -> ModelCutoffDeclaration:
        if declaration.family is not ExperimentFamily.JEV_INCREMENTAL:
            raise JevLeakageError(
                f"{declaration.slug} is a {declaration.family.value} experiment, not a Jev one"
            )
        grid = declaration.parameter_grid
        model = _single(grid, MODEL_KEY)
        source = _single(grid, CUTOFF_SOURCE_KEY)
        raw_cutoff = _single(grid, CUTOFF_KEY)
        try:
            cutoff = date.fromisoformat(raw_cutoff)
        except ValueError:
            raise JevLeakageError(
                f"{CUTOFF_KEY} must be an ISO date (YYYY-MM-DD), not {raw_cutoff!r}"
            ) from None
        return cls(model=model, knowledge_cutoff=cutoff, source=source)

    def apply_to(self, config: JevConfig) -> JevConfig:
        """The experiment's config: the declared model and cutoff, everything else as given."""
        return replace(config, model=self.model, model_knowledge_cutoff=self.knowledge_cutoff)


def _single(grid: Mapping[str, tuple[str, ...]], key: str) -> str:
    values = grid.get(key)
    if not values:
        raise JevLeakageError(f"the declaration must state {key} in its parameter_grid")
    if len(values) != 1:
        raise JevLeakageError(f"{key} must be exactly one value, not {list(values)!r}")
    return values[0]
