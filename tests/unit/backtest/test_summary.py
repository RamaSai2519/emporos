"""EM-108: the text summary is rendered from the same document the golden files hold."""

from __future__ import annotations

from emporos.backtest.document import BacktestDocument
from emporos.backtest.summary import BacktestSummary
from tests.support.backtest_engine import WORKED_DAY, bars, config, run


async def summary_of_the_worked_day() -> str:
    result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5))
    return BacktestSummary().render(BacktestDocument().render(result))


async def test_assumptions_come_before_any_number() -> None:
    text = await summary_of_the_worked_day()

    assert text.index("ASSUMPTIONS") < text.index("RESULT")
    assert "NO RISK ENGINE" in text and "fee schedule NOT reconciled" in text


async def test_the_headline_numbers_of_the_worked_day() -> None:
    text = await summary_of_the_worked_day()

    assert "start 100000.00  end 100015.83  over 1 trading days" in text
    assert "total return 0.02%" in text  # 15.83 / 100,000 = 0.01583%
    assert "trades 1  win rate 100.00%" in text
    assert "net P&L 15.83  (gross 28.00, charges 12.17)" in text
    assert "Sharpe n/a" in text  # one day: no variance to divide by
    assert "2026-01  " in text and "ACTIVITY  2 signals, 2 orders, 2 fills" in text


async def test_a_halted_strategy_is_called_out() -> None:
    result = await run(bars(WORKED_DAY), config(buy_at=2, sell_at=5, fail_at=3))

    text = BacktestSummary().render(BacktestDocument().render(result))

    assert "STRATEGY HALTED" in text and "blew up" in text


def test_percent_and_plain_round_half_even_and_show_missing_as_n_a() -> None:
    assert BacktestSummary.percent("0.125") == "12.50%"
    assert BacktestSummary.percent("0.00005") == "0.00%"  # half-even on 0.005%
    assert BacktestSummary.percent("0.00015") == "0.02%"  # 0.015% -> 0.02 (even)
    assert BacktestSummary.percent(None) == "n/a" and BacktestSummary.plain(None) == "n/a"
    assert BacktestSummary.plain("4.582575694") == "4.58"


async def test_the_direction_cross_tabs_are_rendered_not_just_the_flat_sections() -> None:
    text = await summary_of_the_worked_day()

    for title in (
        "by direction and instrument",
        "by direction and time of day",
        "by direction and regime",
    ):
        assert title in text
    # the worked day is a single LONG trade, so only the LONG side carries it
    assert "LONG" in text and "SHORT" not in text
