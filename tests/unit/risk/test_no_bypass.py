"""No order path bypasses the risk engine (EM-75, plan.md §11).

Three facts, each a failing build if it stops being true:

1. Only `emporos.risk` can create a `RiskApprovedSignal` (or the seal that makes one).
2. Only the execution gateway — one file — calls a broker's order-writing methods.
3. Nothing in `emporos.execution` accepts a raw `Signal`: its entry points take the approval.

Together: to reach a broker an order must pass through execution; execution takes only an
approval; and only the risk engine can issue one. The scanner is itself tested against a planted
violation, so these cannot pass vacuously.
"""

from __future__ import annotations

from pathlib import Path

from tests.support.architecture import calls_named, parameters_annotated, python_files

MINTING = {"RiskApprovedSignal", "ApprovalSeal", "SealIssuer"}
ORDER_WRITES = {"place_order", "modify_order", "cancel_order"}
# The one file outside the broker package that may call a broker's order-writing methods.
GATEWAY = "emporos/execution/gateway.py"


def outside(prefix: str, hits: list) -> list[str]:  # type: ignore[type-arg]
    return [f"{h.path}:{h.line} {h.what}" for h in hits if not h.path.startswith(prefix)]


def test_only_the_risk_package_creates_an_approval() -> None:
    assert outside("emporos/risk/", calls_named(MINTING)) == []


def test_the_approval_really_is_created_in_the_risk_package_so_the_scan_is_not_vacuous() -> None:
    inside = [h for h in calls_named(MINTING) if h.path.startswith("emporos/risk/")]
    assert {h.what for h in inside} >= {"RiskApprovedSignal", "ApprovalSeal", "SealIssuer"}


def test_only_the_execution_gateway_calls_a_brokers_order_methods() -> None:
    hits = [h for h in calls_named(ORDER_WRITES) if not h.path.startswith("emporos/broker/")]
    assert sorted({h.path for h in hits}) in ([], [GATEWAY]), hits


def test_nothing_in_execution_accepts_a_raw_signal() -> None:
    assert parameters_annotated({"Signal"}, "emporos/execution/") == []


def test_the_scanner_catches_a_planted_violation(tmp_path: Path) -> None:
    pkg = tmp_path / "emporos" / "strategies"
    pkg.mkdir(parents=True)
    (pkg / "sneaky.py").write_text(
        "from emporos.risk.approval import RiskApprovedSignal\n"
        "def cheat(signal, broker):\n"
        "    return RiskApprovedSignal(signal, 'x', 'y', None, ('z',), None)\n"
        "async def place(broker, request):\n"
        "    await broker.place_order(request)\n"
    )
    execution = tmp_path / "emporos" / "execution"
    execution.mkdir()
    (execution / "engine.py").write_text(
        "from emporos.domain.signals import Signal\n"
        "async def execute(signal: Signal) -> None: ...\n"
        "async def maybe(signal: 'Signal | None') -> None: ...\n"
    )

    assert [h.what for h in calls_named(MINTING, tmp_path)] == ["RiskApprovedSignal"]
    assert [h.what for h in calls_named(ORDER_WRITES, tmp_path)] == ["place_order"]
    flagged = parameters_annotated({"Signal"}, "emporos/execution/", tmp_path)
    assert [h.what for h in flagged] == ["execute(signal)", "maybe(signal)"]


def test_the_scan_covers_the_whole_source_tree() -> None:
    files = {path for path, _ in python_files()}
    assert "emporos/risk/engine.py" in files and "emporos/broker/base.py" in files
