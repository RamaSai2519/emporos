"""Enforces the coverage gates from plan.md §18.

`domain`, `risk`, and `execution` must hold >= 90%; every other package >= 70%.
Run `pipenv run test` first (it writes `.coverage`), then this script. Exits 0
iff all gates hold and at least one emporos module was measured.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from coverage import Coverage
from coverage.misc import CoverageException

REPO_ROOT = Path(__file__).resolve().parents[1]
COVERAGE_FILE = REPO_ROOT / ".coverage"
SRC_ROOT = REPO_ROOT / "src" / "emporos"

HIGH_GATE_PACKAGES = ("domain", "risk", "execution")
HIGH_GATE_PERCENT = 90.0
LOW_GATE_PERCENT = 70.0


@dataclass(frozen=True)
class PackageCoverage:
    """Aggregate executable/covered line counts for one emporos package."""

    name: str
    statements: int
    covered: int

    @property
    def percent(self) -> float:
        if self.statements == 0:
            return 100.0
        return 100.0 * self.covered / self.statements


class CoverageReport:
    """Loads `.coverage` and aggregates per-package line coverage."""

    def __init__(
        self,
        source_root: Path = SRC_ROOT,
        coverage_file: Path = COVERAGE_FILE,
    ) -> None:
        self._source_root = source_root
        self._coverage = Coverage(data_file=str(coverage_file))

    def packages(self) -> dict[str, PackageCoverage]:
        self._coverage.load()
        totals: dict[str, list[int]] = {}
        for filename in self._coverage.get_data().measured_files():
            package = self._package_of(filename)
            if package is None:
                continue
            _, statements, _, missing, _ = self._coverage.analysis2(filename)
            statements_count = len(statements)
            covered_count = statements_count - len(set(missing))
            counts = totals.setdefault(package, [0, 0])
            counts[0] += statements_count
            counts[1] += covered_count
        return {
            name: PackageCoverage(name=name, statements=counts[0], covered=counts[1])
            for name, counts in totals.items()
        }

    def _package_of(self, filename: str) -> str | None:
        relative = Path(filename).resolve().relative_to(self._source_root.resolve())
        if not relative.parts:
            return None
        if relative.parts[0] == "__init__.py":
            return "emporos"
        return relative.parts[0]


class CoverageGate:
    """Decides per-package coverage against the plan.md §18 thresholds."""

    def __init__(
        self,
        high_packages: Iterable[str],
        high_percent: float,
        low_percent: float,
    ) -> None:
        self._high_packages = frozenset(high_packages)
        self._high_percent = high_percent
        self._low_percent = low_percent

    def required_for(self, package: str) -> float:
        return self._high_percent if package in self._high_packages else self._low_percent

    def report_rows(self, packages: dict[str, PackageCoverage]) -> list[str]:
        rows: list[str] = []
        for name in sorted(packages):
            coverage = packages[name]
            required = self.required_for(name)
            status = "OK" if coverage.percent >= required else "FAIL"
            rows.append(f"{status:4} {name:<12} {coverage.percent:6.1f}%   (gate {required:g}%)")
        return rows

    def failing(self, packages: dict[str, PackageCoverage]) -> list[str]:
        return [
            name
            for name, coverage in packages.items()
            if coverage.percent < self.required_for(name)
        ]


def main() -> int:
    gate = CoverageGate(HIGH_GATE_PACKAGES, HIGH_GATE_PERCENT, LOW_GATE_PERCENT)
    try:
        packages = CoverageReport().packages()
    except CoverageException as exc:
        print(f"coverage data missing or corrupt: run `pipenv run test` first ({exc})")
        return 1

    if not packages:
        print("no emporos modules measured: run `pipenv run test` first")
        return 1

    print("\n".join(gate.report_rows(packages)))
    failing = gate.failing(packages)
    if failing:
        print(f"\nCoverage gates FAILED: {', '.join(failing)}")
        return 1
    print("\nAll coverage gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
