"""EM-132: the depth report reads as a table, and says plainly when nothing was found."""

from __future__ import annotations

from datetime import date, datetime

from emporos.cli.history_probe_commands import _last_closed_weekday, render
from emporos.core.clock import IST
from emporos.domain.candles import Timeframe
from emporos.history.depth import DepthFinding, ProbeReport, SpanFinding, TimeframeFinding


def test_the_report_is_a_table_with_one_row_per_interval() -> None:
    report = ProbeReport(
        "NSE:3045",
        date(2026, 9, 18),
        (
            TimeframeFinding(
                SpanFinding(Timeframe.M5, 100, 120, 100),
                DepthFinding(Timeframe.M5, date(2021, 6, 15), 5),
                31,
            ),
            TimeframeFinding(
                SpanFinding(Timeframe.D1, 730, None, None), DepthFinding(Timeframe.D1, None, 0), 9
            ),
        ),
    )

    text = render(report)

    assert "NSE:3045, as of 2026-09-18" in text
    assert "| 5m | 100 d | 120 d | 2021-06-15 | 31 |" in text
    assert "| 1d | 730 d | none up to the ladder's top | no data | 9 |" in text


def test_the_default_day_is_the_last_closed_weekday() -> None:
    monday_morning = datetime(2026, 9, 21, 4, 0, tzinfo=IST)
    wednesday = datetime(2026, 9, 23, 20, 0, tzinfo=IST)

    assert _last_closed_weekday(monday_morning) == date(2026, 9, 18)  # the Friday before
    assert _last_closed_weekday(wednesday) == date(2026, 9, 22)
