"""SmartAPI's reply envelope, decoded exactly as observed on the live API.

Recorded live shapes (fixtures are authoritative over the docs):

* success:          `{"status": true,  "message": "SUCCESS", "errorcode": "", "data": ...}`
* business failure: `{"status": false, "message": "...", "errorcode": "AB1050", "data": null}` —
  returned with **HTTP 200**, so the HTTP status alone never means success.
* gateway failure:  `{"message": "Invalid Token", "errorcode": ""}` — no `status` key at all.
* some rejections are an HTTP 400 with an empty body.

Prices arrive as JSON floats; they are parsed straight to `Decimal` so no float ever
exists for a price (money is never a float — plan.md §6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Envelope:
    status: bool | None
    message: str
    error_code: str
    data: Any


class EnvelopeDecoder:
    """Turns raw response bytes into an `Envelope`, or `None` when they are not one."""

    def decode(self, content: bytes) -> Envelope | None:
        if not content.strip():
            return None
        try:
            body = json.loads(content, parse_float=Decimal, parse_constant=self._reject_constant)
        except ValueError:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
            return None
        if not isinstance(body, dict):
            return None
        status = body.get("status")
        return Envelope(
            status=status if isinstance(status, bool) else None,
            message=str(body.get("message") or ""),
            error_code=str(body.get("errorcode") or ""),
            data=body.get("data"),
        )

    @staticmethod
    def _reject_constant(name: str) -> None:
        raise ValueError(f"non-finite JSON constant {name!r}")
