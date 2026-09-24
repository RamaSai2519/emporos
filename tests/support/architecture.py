"""Static checks over the source tree: what constructs, calls or accepts what.

Used by the tests that turn an architectural rule into a failing build. Each helper takes the root
to scan so the tests can also prove the scanner really catches a violation planted in a scratch
tree — a guard that cannot fail is not a guard.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


@dataclass(frozen=True)
class Hit:
    path: str  # relative to the scanned root, forward slashes
    line: int
    what: str


def python_files(root: Path = SRC) -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(root.rglob("*.py")):
        yield path.relative_to(root).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def calls_named(names: set[str], root: Path = SRC) -> list[Hit]:
    """Every call whose callee is (or ends in) one of `names`, e.g. `X(...)` or `mod.X(...)`."""
    hits = []
    for path, tree in python_files(root):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (name := _called_name(node)) in names:
                hits.append(Hit(path, node.lineno, str(name)))
    return hits


def string_constants(values: set[str], root: Path = SRC) -> list[Hit]:
    """Every string literal that, ignoring case and padding, is exactly one of `values`."""
    hits = []
    for path, tree in python_files(root):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.strip().upper() in values
            ):
                hits.append(Hit(path, node.lineno, node.value))
    return hits


def parameters_annotated(names: set[str], under: str, root: Path = SRC) -> list[Hit]:
    """Every function parameter, in files whose path starts with `under`, annotated with one of
    `names` (bare or dotted; unions and string annotations included)."""
    hits = []
    for path, tree in python_files(root):
        if not path.startswith(under):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                for arg in args:
                    if arg.annotation is not None and _mentions(arg.annotation, names):
                        hits.append(Hit(path, node.lineno, f"{node.name}({arg.arg})"))
    return hits


def _mentions(annotation: ast.expr, names: set[str]) -> bool:
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in names:
            return True
        if isinstance(node, ast.Attribute) and node.attr in names:
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and any(name in node.value.replace("|", " ").split() for name in names)
        ):
            return True
    return False


def class_uses(name: str, root: Path = SRC) -> list[Hit]:
    """Every call that constructs `name` or calls a method on it: `X(...)`, `X.of(...)`."""
    hits = []
    for path, tree in python_files(root):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                func = func.value  # type: ignore[assignment]
            if isinstance(func, ast.Name) and func.id == name:
                hits.append(Hit(path, node.lineno, name))
    return hits
