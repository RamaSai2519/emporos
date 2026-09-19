"""Guards plan.md §6 "Money as Decimal128, never float" for the layers that carry money.

`domain` and `persistence` must not mention `float` (annotations, casts) or use
float literals. This is the lint-style check EM-21 calls for.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "emporos"
MONEY_PACKAGES = ["domain", "persistence"]


def _float_uses(path: Path) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Name) and node.id == "float":
            hits.append(f"{path.name}:{node.lineno} uses `float`")
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            hits.append(f"{path.name}:{node.lineno} float literal {node.value!r}")
    return hits


@pytest.mark.parametrize("package", MONEY_PACKAGES)
def test_money_carrying_packages_never_use_float(package: str) -> None:
    hits = [hit for path in (SRC / package).rglob("*.py") for hit in _float_uses(path)]

    assert hits == []
