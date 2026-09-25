"""A frozen candidate: what was declared, and proof that nothing it stands on has moved (EM-235).

`docs/research/profit/candidates/<name>.yaml` names the arm, the commit that screened it and, for
each file it depends on (declarations, fee schedule, universe manifest, adjustment ledger, ETF
report), the file's sha256. `FrozenCandidate.verify` re-hashes them: a stress or a confirmation run
refuses to start on a candidate whose inputs have changed, so "no parameter changes after this" is
checked by the run itself and not by trust.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from emporos.core.errors import ConfigurationError

__all__ = ["FrozenCandidate"]


def _pinned_files(node: Any) -> Iterator[tuple[str, str]]:
    """Every (file, sha256) pair anywhere in the document."""
    if isinstance(node, Mapping):
        if "file" in node and "sha256" in node:
            yield str(node["file"]), str(node["sha256"])
        for value in node.values():
            yield from _pinned_files(value)
    elif isinstance(node, list):
        for value in node:
            yield from _pinned_files(value)


@dataclass(frozen=True)
class FrozenCandidate:
    name: str
    cell: str
    arm: Mapping[str, str]
    screen_id: str
    code_commit: str
    pinned: Mapping[str, str]  # file -> sha256

    @staticmethod
    def load(path: Path) -> FrozenCandidate:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            return FrozenCandidate(
                str(raw["candidate"]), str(raw["cell"]),
                {str(k): str(v) for k, v in raw["arm"].items()},
                str(raw["screen_id"]), str(raw["code_commit"]), dict(_pinned_files(raw)),
            )  # fmt: skip
        except (OSError, KeyError, TypeError, AttributeError, yaml.YAMLError) as error:
            raise ConfigurationError(f"{path} is not a frozen candidate: {error}") from error

    def verify(self, root: Path = Path()) -> None:
        """Raise when any pinned file is missing or no longer hashes to what was frozen."""
        moved = []
        for file, expected in sorted(self.pinned.items()):
            target = root / file
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if actual != expected:
                moved.append(file)
        if moved:
            raise ConfigurationError(
                f"candidate {self.name} is frozen and these inputs changed or are missing: "
                f"{', '.join(moved)}"
            )
