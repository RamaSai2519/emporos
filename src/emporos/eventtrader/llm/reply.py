"""Strict reading of a model's JSON reply into validated values (EM-240).

Anything that is not exactly the declared shape is an `InvalidReply`: a missing key, a wrong type, a
value outside its range or an unknown choice. Nothing is coerced or defaulted, because a decision
made from a guessed field is not the model's decision."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from typing import Any

__all__ = ["InvalidReply", "Reply"]

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class InvalidReply(ValueError):
    """The reply is not the JSON object the stage asked for."""


class Reply:
    def __init__(self, text: str) -> None:
        stripped = text.strip()
        fenced = _FENCE.match(stripped)
        try:
            data = json.loads(fenced.group(1) if fenced else stripped)
        except json.JSONDecodeError as error:
            raise InvalidReply(f"not JSON: {error.msg}") from error
        if not isinstance(data, dict):
            raise InvalidReply("the reply is not a JSON object")
        self._data: dict[str, Any] = data

    def boolean(self, key: str) -> bool:
        value = self._need(key)
        if not isinstance(value, bool):
            raise InvalidReply(f"{key} must be true or false")
        return value

    def choice(self, key: str, allowed: Sequence[str]) -> str:
        value = self._need(key)
        if not isinstance(value, str) or value not in allowed:
            raise InvalidReply(f"{key} must be one of {list(allowed)}")
        return value

    def number(self, key: str, low: float, high: float) -> float:
        value = self._need(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InvalidReply(f"{key} must be a number")
        if math.isnan(value) or not low <= value <= high:
            raise InvalidReply(f"{key} must be between {low} and {high}")
        return float(value)

    def text(self, key: str, max_length: int = 500) -> str:
        value = self._need(key)
        if not isinstance(value, str):
            raise InvalidReply(f"{key} must be text")
        return value[:max_length]

    def _need(self, key: str) -> Any:
        if key not in self._data:
            raise InvalidReply(f"missing {key}")
        return self._data[key]
