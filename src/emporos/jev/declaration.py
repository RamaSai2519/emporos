"""What a Jev experiment must declare about its model before it runs (EM-187).

The experiment declaration (EM-188) is the committed, content-hashed claim. Jev's part of it rides
in the declaration's `parameter_grid`, so the model, its knowledge cutoff and the source that
cutoff was verified against are part of the experiment's identity: changing any of them is a new
experiment. Nothing here knows a cutoff date; it only refuses to proceed without a declared one.

    parameter_grid:
      model: ["<gateway model id>"]
      model_knowledge_cutoff: ["<ISO date>"]
      cutoff_source: ["<where the provider publishes it, and when it was checked>"]
      inr_per_1k_tokens: ["<rupees, a quoted number>"]
      prompt_version: ["v1"]
      prompt_hash: ["<sha256 of the system prompt>"]
      mode: ["confirmation", "ranking"]        # the arms the run may try
      threshold: ["0.5", "0.6", "0.7"]          # the confidence thresholds it may try
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation

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


INR_KEY = "inr_per_1k_tokens"
PROMPT_VERSION_KEY = "prompt_version"
PROMPT_HASH_KEY = "prompt_hash"
MODE_KEY = "mode"
THRESHOLD_KEY = "threshold"


@dataclass(frozen=True)
class JevExperimentDeclaration:
    """Everything a Jev experiment fixed before it ran: the model and its cutoff, what a token
    costs, which prompt, and the modes and thresholds it is allowed to try."""

    model: ModelCutoffDeclaration
    inr_per_1k_tokens: Decimal
    prompt_version: str
    prompt_hash: str
    modes: tuple[str, ...]
    thresholds: tuple[Decimal, ...]

    @classmethod
    def from_declaration(cls, declaration: ExperimentDeclaration) -> JevExperimentDeclaration:
        grid = declaration.parameter_grid
        rate = _decimal(_single(grid, INR_KEY), INR_KEY)
        if rate < 0:
            raise JevLeakageError(f"{INR_KEY} cannot be negative")
        thresholds = tuple(_decimal(v, THRESHOLD_KEY) for v in _many(grid, THRESHOLD_KEY))
        if any(not (Decimal(0) <= t <= Decimal(1)) for t in thresholds):
            raise JevLeakageError(f"every {THRESHOLD_KEY} must be between 0 and 1")
        return cls(
            model=ModelCutoffDeclaration.from_declaration(declaration),
            inr_per_1k_tokens=rate,
            prompt_version=_single(grid, PROMPT_VERSION_KEY),
            prompt_hash=_single(grid, PROMPT_HASH_KEY),
            modes=_many(grid, MODE_KEY),
            thresholds=thresholds,
        )

    def select(
        self, modes: Sequence[str] | None, thresholds: Sequence[Decimal] | None
    ) -> tuple[tuple[str, ...], tuple[Decimal, ...]]:
        """The requested subset of the declared grid; anything outside it was not pre-declared."""
        chosen_modes = tuple(modes) if modes else self.modes
        chosen_thresholds = tuple(thresholds) if thresholds else self.thresholds
        undeclared = [m for m in chosen_modes if m not in self.modes]
        undeclared += [str(t) for t in chosen_thresholds if t not in self.thresholds]
        if undeclared:
            raise JevLeakageError(
                f"not in the declared grid: {', '.join(undeclared)}; a value that was not "
                "declared before the run is a new experiment"
            )
        return chosen_modes, chosen_thresholds


def _many(grid: Mapping[str, tuple[str, ...]], key: str) -> tuple[str, ...]:
    values = grid.get(key)
    if not values:
        raise JevLeakageError(f"the declaration must state {key} in its parameter_grid")
    return values


def _decimal(text: str, key: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        raise JevLeakageError(f"{key} must be a number, not {text!r}") from None
