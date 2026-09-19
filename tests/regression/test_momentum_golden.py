"""Golden-file regression for the reference strategy (plan.md §18: any drift fails the build).

The golden file holds every signal momentum_v1 emits over a fixed series. A change to the
indicator maths, the crossover rule, sizing or the reason text shows up here as a diff. If the
change is intended, regenerate the file deliberately and review the diff — never to make a red
build green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.strategies import closes_to_bars, momentum_raw, replay, resolved, wave_closes

pytestmark = pytest.mark.regression

GOLDEN = (
    Path(__file__).resolve().parents[1] / "fixtures" / "strategies" / "momentum_wave.golden.json"
)


async def test_momentum_v1_signals_match_the_golden_file() -> None:
    result = await replay(
        resolved(momentum_raw()),
        closes_to_bars(wave_closes(400, period=40, amplitude=30)),
        fills=True,
    )
    actual = [
        {
            "kind": s.kind.value,
            "side": s.side.value,
            "order_type": s.order_type.value,
            "quantity": s.quantity,
            "limit_price": str(s.limit_price.amount),
            "ts": s.ts.isoformat(),
            "instrument_id": s.instrument_id,
            "reason": s.reason,
        }
        for s in result.signals
    ]

    assert actual == json.loads(GOLDEN.read_text())["signals"]
