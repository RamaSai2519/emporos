"""Fails the build if strategy code hardcodes an instrument token (plan.md §8).

Strategies reference instruments by `(exchange, tradingsymbol)` and resolve them
through the `InstrumentResolver`. A hardcoded token would silently point at the
wrong instrument after the exchange reuses it. Two things are flagged in
`src/emporos/strategies`:

* a string literal made only of digits, three or more long (how tokens are published);
* a number or digit-string assigned to a name, or passed as a keyword, containing "token".
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STRATEGIES_DIR = REPO_ROOT / "src" / "emporos" / "strategies"
_DIGITS = re.compile(r"\d{3,}")


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


class TokenLiteralScanner:
    """Finds token-looking literals in one Python source."""

    def scan(self, path: Path) -> list[Violation]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        return [Violation(path, line, reason) for line, reason in self._findings(tree)]

    def _findings(self, tree: ast.AST) -> Iterator[tuple[int, str]]:
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and self._is_digit_string(node.value):
                yield node.lineno, f"digit-only string literal {node.value!r} looks like a token"
            for name, value in self._named_values(node):
                if "token" in name.lower() and self._is_numeric_constant(value):
                    yield value.lineno, f"numeric literal assigned to '{name}'"

    @staticmethod
    def _named_values(node: ast.AST) -> Iterator[tuple[str, ast.expr]]:
        if isinstance(node, ast.keyword) and node.arg:
            yield node.arg, node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            yield node.target.id, node.value

    @staticmethod
    def _is_digit_string(value: object) -> bool:
        return isinstance(value, str) and _DIGITS.fullmatch(value) is not None

    @staticmethod
    def _is_numeric_constant(node: ast.expr) -> bool:
        if not isinstance(node, ast.Constant):
            return False
        return (isinstance(node.value, int) and not isinstance(node.value, bool)) or (
            isinstance(node.value, str) and node.value.isdigit()
        )


class StrategyTokenCheck:
    def __init__(self, root: Path = STRATEGIES_DIR, scanner: TokenLiteralScanner | None = None):
        self._root = root
        self._scanner = scanner or TokenLiteralScanner()

    def violations(self) -> list[Violation]:
        return [v for path in sorted(self._root.rglob("*.py")) for v in self._scanner.scan(path)]


def main() -> int:
    violations = StrategyTokenCheck().violations()
    for violation in violations:
        print(violation)
    if violations:
        print(f"\n{len(violations)} hardcoded instrument token(s) in strategy code.")
        return 1
    print("No hardcoded instrument tokens in strategy code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
