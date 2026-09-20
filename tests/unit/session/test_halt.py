"""The reconciler stops trading through the kill switch an operator would use."""

from datetime import datetime

import pytest

from emporos.core.clock import FixedClock
from emporos.risk.kill_switch import KillSwitchControl, SourceReading
from emporos.session.halt import KillSwitchHalt
from tests.support.records import NOW


class Sink:
    name = "sink"

    def __init__(self, ok: bool = True) -> None:
        self.ok, self.engaged = ok, []

    async def read(self) -> SourceReading:
        return SourceReading(halted=bool(self.engaged), reason="")

    async def engage(self, reason: str, set_by: str, at: datetime) -> None:
        if not self.ok:
            raise OSError("down")
        self.engaged.append((reason, set_by, at))

    async def release(self, set_by: str, at: datetime) -> None: ...


async def test_a_halt_engages_the_kill_switch_naming_who_and_why() -> None:
    sink = Sink()
    await KillSwitchHalt(KillSwitchControl([sink], [sink], FixedClock(NOW))).halt("mismatch")
    assert sink.engaged == [("mismatch", "reconciler", NOW)]


async def test_one_working_place_is_enough_but_none_is_an_error() -> None:
    good, bad = Sink(), Sink(ok=False)
    await KillSwitchHalt(KillSwitchControl([bad, good], [bad, good], FixedClock(NOW))).halt("x")
    assert good.engaged
    with pytest.raises(RuntimeError, match="could not engage"):
        await KillSwitchHalt(KillSwitchControl([bad], [bad], FixedClock(NOW))).halt("x")
