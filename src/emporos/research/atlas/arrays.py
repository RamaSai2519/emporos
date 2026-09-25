"""The array types the atlas uses, in one place. numpy's own stubs are beyond the pinned mypy (see
pyproject), so an array is `Any` to the type checker; the names say what each one holds."""

from __future__ import annotations

from typing import Any

__all__ = ["Bools", "Floats", "Ints"]

Floats = Any  # a float64 array
Ints = Any  # an int64 array
Bools = Any  # a boolean array
