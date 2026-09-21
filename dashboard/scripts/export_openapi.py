"""Export the real FastAPI contract without database or broker connections."""
from __future__ import annotations

import json
from pathlib import Path

from emporos.api.app import openapi_document


class OpenApiExporter:
    """Writes the backend's route metadata for TypeScript generation."""

    def export(self) -> None:
        target = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"
        target.write_text(json.dumps(openapi_document(), indent=2) + "\n")


if __name__ == "__main__":
    OpenApiExporter().export()
