"""SmartAPI's reply envelope, decoded exactly as observed on the live API.

Recorded live shapes (fixtures are authoritative over the docs):

* success:          `{"status": true,  "message": "SUCCESS", "errorcode": "", "data": ...}`
* business failure: `{"status": false, "message": "...", "errorcode": "AB1050", "data": null}` —
  returned with **HTTP 200**, so the HTTP status alone never means success.
* gateway failure:  `{"success": false, "errorCode": "AG8001", "message": "Invalid Token",
                     "data": ""}` — a different dialect: `success` for `status`, and camelCase
                     `errorCode`. (Also seen as a bare message with neither.)
* some rejections are an HTTP 400 with an empty body.
* rate limiting (recorded live): **HTTP 403 with a plain-text body**
  `Access denied because of exceeding access rate` — not a 429, not JSON.

Prices arrive as JSON floats; they are parsed straight to `Decimal` so no float ever
exists for a price (money is never a float — plan.md §6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

_MAX_TEXT_BODY = 200


@dataclass(frozen=True)
class Envelope:
    status: bool | None
    message: str
    error_code: str
    data: Any
    # True when the body was plain text rather than JSON. Such text is matched against error
    # rules but is never quoted into an error message unless a rule recognised it.
    from_text: bool = False


class EnvelopeDecoder:
    """Turns raw response bytes into an `Envelope`, or `None` when they are not one.

    A short plain-text body (the gateway's rate-limit denial) becomes a status-less text
    envelope so the classifier can recognise it; anything else that is not a JSON object is None."""

    def decode(self, content: bytes) -> Envelope | None:
        if not content.strip():
            return None
        try:
            body = json.loads(content, parse_float=Decimal, parse_constant=self._reject_constant)
        except ValueError:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
            return self._text_envelope(content)
        if not isinstance(body, dict):
            return None
        return Envelope(
            status=self._flag(body),
            message=str(body.get("message") or ""),
            error_code=str(body.get("errorcode") or body.get("errorCode") or ""),
            data=body.get("data"),
        )

    @staticmethod
    def _text_envelope(content: bytes) -> Envelope | None:
        try:
            text = content.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        if not text or len(text) > _MAX_TEXT_BODY or text[0] in "{[" or not text.isprintable():
            return None
        return Envelope(status=None, message=text, error_code="", data=None, from_text=True)

    @staticmethod
    def _flag(body: dict[str, Any]) -> bool | None:
        """The success flag under either dialect's key; absent or non-boolean means unknown."""
        for key in ("status", "success"):
            value = body.get(key)
            if isinstance(value, bool):
                return value
        return None

    @staticmethod
    def _reject_constant(name: str) -> None:
        raise ValueError(f"non-finite JSON constant {name!r}")
