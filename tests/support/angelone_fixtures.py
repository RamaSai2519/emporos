"""Loads the recorded SmartAPI fixtures (`tests/fixtures/angelone/`).

`recorded` fixtures were captured from the live API and scrubbed
(scripts/record_angelone_fixtures.py);
`synthetic` ones (under `synthetic/`) are hand-written for conditions that cannot be induced on
demand, such as the upstream rate-limit defect, and say so in their `provenance`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "angelone"


@dataclass(frozen=True)
class Recording:
    name: str
    provenance: str
    method: str
    path: str
    request_body: Any
    header_names: tuple[str, ...]
    status: int
    body: Any
    body_text: str | None

    @classmethod
    def load(cls, path: Path) -> Recording:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=raw["name"],
            provenance=raw["provenance"],
            method=raw["request"]["method"],
            path=raw["request"]["path"],
            request_body=raw["request"]["body"],
            header_names=tuple(raw["request"]["header_names"]),
            status=raw["response"]["status"],
            body=raw["response"]["body"],
            body_text=raw["response"]["body_text"],
        )

    def response(self) -> httpx.Response:
        content = self.body_text if self.body_text is not None else json.dumps(self.body)
        return httpx.Response(self.status, content=content.encode())

    @property
    def data(self) -> Any:
        return self.body["data"]


class FixtureLibrary:
    def __init__(self, directory: Path = FIXTURE_DIR) -> None:
        self._directory = directory

    def recorded(self) -> dict[str, Recording]:
        return self._load(self._directory)

    def synthetic(self) -> dict[str, Recording]:
        return self._load(self._directory / "synthetic")

    def get(self, name: str) -> Recording:
        return self.recorded()[name]

    @staticmethod
    def _load(directory: Path) -> dict[str, Recording]:
        recordings = (Recording.load(p) for p in sorted(directory.glob("*.json")))
        return {r.name: r for r in recordings}
