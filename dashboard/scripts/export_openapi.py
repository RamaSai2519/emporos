"""Export route metadata only; never construct a broker, database client or running service."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from emporos.api.app import ApiServices, create_app


class OpenApiExporter:
    """Constructs the HTTP route graph without invoking any request handlers."""

    def export(self) -> None:
        context = cast(ApiServices, SimpleNamespace(cors_origins=(), queries=None))
        document = create_app(context).openapi()
        target = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"
        target.write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    OpenApiExporter().export()
