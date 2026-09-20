"""Trading halts requested by the platform itself, through the same kill switch an operator uses."""

from emporos.risk.kill_switch import KillSwitchControl


class KillSwitchHalt:
    """`TradingHalt` for the reconciler: engage the kill switch and say who did it and why."""

    def __init__(self, control: KillSwitchControl, set_by: str = "reconciler") -> None:
        self._control = control
        self._set_by = set_by

    async def halt(self, reason: str) -> None:
        outcomes = await self._control.engage(reason, self._set_by)
        if not any(outcome.ok for outcome in outcomes):
            # The kill switch could not be set anywhere: that must never pass quietly.
            raise RuntimeError(f"could not engage the kill switch: {reason}")
