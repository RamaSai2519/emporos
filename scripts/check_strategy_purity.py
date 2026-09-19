"""Fails the build if strategy code could be non-deterministic or reach the outside world
(plan.md §9: "may not call datetime.now(), may not perform I/O, may not use unseeded randomness").

An ALLOWLIST, not a blocklist: every module under `src/emporos/strategies` may import only pure
standard-library modules, pydantic and `emporos` itself, so a new I/O route (`requests`, `sqlite3`,
`subprocess`, ...) fails the build by default instead of by someone remembering to ban it. On top:

* no wall-clock reads: `datetime.now()/utcnow()/today()` (use `ctx.clock`, which is a different
  receiver and is not flagged);
* no unseeded randomness: `random.Random(seed)` is allowed, every other `random` member is not;
* no `open`, `print`, `input`, `exec`, `eval`, `__import__` calls;
* no environment access through `os` is possible, because `os` is not on the allowlist.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = REPO_ROOT / "src" / "emporos" / "strategies"

ALLOWED_MODULES = frozenset(
    {
        "__future__", "abc", "bisect", "collections", "copy", "dataclasses", "datetime",
        "decimal", "enum", "functools", "hashlib", "itertools", "json", "logging", "math",
        "operator", "random", "re", "statistics", "types", "typing",
        "pydantic", "emporos",
    }
)  # fmt: skip
FORBIDDEN_CALLS = frozenset({"open", "print", "input", "exec", "eval", "__import__"})
CLOCK_READS = frozenset({"now", "utcnow", "today", "fromtimestamp", "utcfromtimestamp"})
CLOCK_RECEIVERS = frozenset({"datetime", "date"})
SEEDED_RANDOM = "Random"


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


class PurityScanner:
    """Finds the ways one Python source could break determinism or reach outside itself."""

    def scan(self, path: Path) -> list[Violation]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return [Violation(path, line, reason) for line, reason in self._findings(tree)]

    def _findings(self, tree: ast.AST) -> Iterator[tuple[int, str]]:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield from self._import(node.lineno, alias.name)
            elif isinstance(node, ast.ImportFrom):
                yield from self._import_from(node)
            elif isinstance(node, ast.Call):
                yield from self._call(node)
            elif isinstance(node, ast.Attribute):
                yield from self._random_member(node)

    def _import(self, line: int, module: str) -> Iterator[tuple[int, str]]:
        if module.split(".")[0] not in ALLOWED_MODULES:
            yield line, f"imports '{module}', which is not on the pure-module allowlist"

    def _import_from(self, node: ast.ImportFrom) -> Iterator[tuple[int, str]]:
        if node.level:  # a relative import stays inside the package
            return
        module = node.module or ""
        yield from self._import(node.lineno, module)
        if module == "random":
            for alias in node.names:
                if alias.name != SEEDED_RANDOM:
                    yield node.lineno, f"imports random.{alias.name}: only random.Random(seed)"

    def _call(self, node: ast.Call) -> Iterator[tuple[int, str]]:
        function = node.func
        if isinstance(function, ast.Name) and function.id in FORBIDDEN_CALLS:
            yield node.lineno, f"calls {function.id}()"
        if (
            isinstance(function, ast.Attribute)
            and function.attr in CLOCK_READS
            and self._receiver_name(function.value) in CLOCK_RECEIVERS
        ):
            yield node.lineno, f"reads the wall clock with {function.attr}(): use ctx.clock"

    def _random_member(self, node: ast.Attribute) -> Iterator[tuple[int, str]]:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "random"
            and node.attr != SEEDED_RANDOM
        ):
            yield node.lineno, f"uses random.{node.attr}: unseeded randomness"

    @staticmethod
    def _receiver_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


class StrategyPurityCheck:
    def __init__(self, root: Path = STRATEGIES_DIR, scanner: PurityScanner | None = None) -> None:
        self._root = root
        self._scanner = scanner or PurityScanner()

    def violations(self) -> list[Violation]:
        return [v for path in sorted(self._root.rglob("*.py")) for v in self._scanner.scan(path)]


def main() -> int:
    violations = StrategyPurityCheck().violations()
    for violation in violations:
        print(violation)
    if violations:
        print(f"\n{len(violations)} determinism/I-O violation(s) in strategy code.")
        return 1
    print("Strategy code is pure: no clock reads, unseeded randomness or I/O.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
