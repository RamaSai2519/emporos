"""EM-106: the real reference strategy through the real engine — invariants and determinism.

The numbers are not asserted against a hand calculation here (the worked-day tests do that); what
is asserted is what must hold for ANY run: the books balance, nothing is carried overnight,
nothing trades outside the session, and the same input gives byte-identical output."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import time
from decimal import Decimal
from pathlib import Path

from emporos.core.clock import IST
from tests.support.backtest_momentum import momentum_backtest

REPO = Path(__file__).resolve().parents[3]


async def test_momentum_v1_trades_and_the_books_balance() -> None:
    result = await momentum_backtest()

    assert not result.runner.halted and result.alerts == ()
    assert len(result.trades) >= 6  # the wave gives it crossovers to trade
    net = sum((t.net_pnl.amount for t in result.trades), Decimal(0))
    dust = Decimal("1e-15")
    assert abs(result.metrics.ending_equity.amount - (Decimal(1_000_000) + net)) < dust
    assert result.metrics.trades.count == len(result.trades)


async def test_nothing_is_carried_overnight_and_nothing_trades_outside_the_session() -> None:
    result = await momentum_backtest()

    assert result.open_positions_at_end == 0
    for trade in result.trades:
        opened, closed = trade.opened_at.astimezone(IST), trade.closed_at.astimezone(IST)
        assert opened.date() == closed.date()
        assert time(9, 15) < opened.time() <= closed.time() <= time(15, 30)


async def test_entries_respect_no_new_entries_after() -> None:
    result = await momentum_backtest()

    # an entry signal comes only from a bar that closes before 15:00; its order may rest, but the
    # session cancels resting orders at square_off_at (15:15), so no entry fills after that
    assert all(t.opened_at.astimezone(IST).time() <= time(15, 15) for t in result.trades)


async def test_the_strategy_sees_its_positions_through_the_portfolio() -> None:
    result = await momentum_backtest()

    # momentum only exits what it holds: every trade is a LONG round trip, never a stray short
    assert {t.direction.value for t in result.trades} == {"LONG"}
    assert result.counters.gate_rejections == 0 and result.counters.exchange_rejections == 0


def _document_hash(hash_seed: str, tz: str) -> str:
    code = (
        "import hashlib\n"
        "from tests.support.backtest_momentum import document_json\n"
        "print(hashlib.sha256(document_json().encode()).hexdigest())\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "TZ": tz, "PYTHONPATH": str(REPO)}
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
        check=True, timeout=180,
    )  # fmt: skip
    return done.stdout.strip()


def test_fresh_interpreters_produce_byte_identical_documents() -> None:
    hashes = {
        _document_hash(seed, tz)
        for seed, tz in [("0", "UTC"), ("7", "Asia/Kolkata"), ("random", "America/New_York")]
    }

    assert len(hashes) == 1 and len(next(iter(hashes))) == 64
    assert hashlib.sha256(b"").hexdigest() not in hashes
