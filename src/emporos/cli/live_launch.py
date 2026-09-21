"""`emporos worker run-live`: ask the live gate, print every reason, and stop.

Live orders are not wired: there is no live venue, no order-update socket in a worker, and nothing
has ever touched a real order endpoint (see `docs/live-trading.md`). This command exists so the
GATE is real before the composition it guards is: it evaluates every condition for every strategy
the operator names and reports what is missing. It never starts a worker, never logs in to Angel
One and never places an order, whatever the answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from emporos.risk.kill_switch import KillSwitchMonitor
from emporos.session.launch_gate import LaunchFacts, LaunchPolicy, LaunchRefused


class LiveLaunchOutcome(StrEnum):
    REFUSED = "refused"  # at least one condition does not hold
    NOT_WIRED = "not_wired"  # every condition holds, and live order routing still does not exist


@dataclass(frozen=True)
class LiveLaunchReport:
    outcome: LiveLaunchOutcome
    refusals: dict[str, tuple[str, ...]]  # strategy -> every reason it may not go live

    @property
    def exit_code(self) -> int:
        """Never 0: nothing was started, so the command has not done what `run` would have."""
        return 1 if self.outcome is LiveLaunchOutcome.REFUSED else 2

    def lines(self) -> list[str]:
        out: list[str] = []
        for name, reasons in self.refusals.items():
            out.append(f"{name}: may NOT go live")
            out.extend(f"  - {reason}" for reason in reasons)
        if self.outcome is LiveLaunchOutcome.NOT_WIRED:
            out.append(
                "Every live condition holds, but live order routing is not built: nothing was "
                "started."
            )
        return out


class MonitorSwitchView:
    """The kill switch as the gate reads it: halted when set, and also when it cannot be read."""

    def __init__(self, monitor: KillSwitchMonitor) -> None:
        self._monitor = monitor

    async def halted(self) -> bool:
        return (await self._monitor.refresh()).halted


class LiveLaunchCheck:
    def __init__(self, policy: LaunchPolicy, facts: LaunchFacts) -> None:
        self._policy = policy
        self._facts = facts

    async def run(self, names: Sequence[str]) -> LiveLaunchReport:
        refusals: dict[str, tuple[str, ...]] = {}
        if not names:
            refusals["(none)"] = ("no strategy was named: say which one to take live with --start",)
        for name in names:
            request = self._facts.request_for(name, None)  # live accepts no acknowledgement
            if request is None:
                refusals[name] = (f"unknown strategy {name!r}",)
                continue
            try:
                await self._policy.require(request)
            except LaunchRefused as refused:
                refusals[name] = refused.reasons
        outcome = LiveLaunchOutcome.REFUSED if refusals else LiveLaunchOutcome.NOT_WIRED
        return LiveLaunchReport(outcome, refusals)
