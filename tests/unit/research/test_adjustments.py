"""EM-221: the factor ledger and the adjuster, hand-computed. Raw prices are never altered."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from emporos.core.errors import ConfigurationError
from emporos.domain.candles import Candle, Timeframe
from emporos.domain.money import Money
from emporos.research.adjustments import (
    ActionKind,
    AdjustmentFactor,
    AdjustmentLedger,
    PriceAdjuster,
)

X = "NSE:1"
D1, D2, D3, D4 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)


def daily(day: date, open_: str, close: str, volume: int = 1000, instrument: str = X) -> Candle:
    high, low = max(open_, close, key=Decimal), min(open_, close, key=Decimal)
    midnight_ist = datetime(day.year, day.month, day.day, tzinfo=UTC).replace(tzinfo=None)
    ts = datetime.fromisoformat(f"{midnight_ist:%Y-%m-%d}T00:00:00+05:30").astimezone(UTC)
    return Candle(
        instrument, Timeframe.D1, ts, Money.of(open_), Money.of(high), Money.of(low),
        Money.of(close), volume,
    )  # fmt: skip


def factor(
    ex: date, ratio: str, kind: ActionKind = ActionKind.SPLIT, i: str = X
) -> AdjustmentFactor:
    return AdjustmentFactor(i, ex, Decimal(ratio), kind, "test filing")


class TestFactor:
    def test_a_factor_needs_a_source(self) -> None:
        with pytest.raises(ValueError, match="needs a source"):
            AdjustmentFactor(X, D3, Decimal("0.5"), ActionKind.BONUS, " ")

    @pytest.mark.parametrize("ratio", ["0", "-0.5", "1", "NaN"])
    def test_a_ratio_is_positive_and_moves_the_price(self, ratio: str) -> None:
        with pytest.raises(ValueError, match="ratio"):
            AdjustmentFactor(X, D3, Decimal(ratio), ActionKind.SPLIT, "s")


class TestAdjuster:
    def test_a_one_for_five_split_scales_earlier_bars_by_a_fifth_and_volume_by_five(self) -> None:
        raw = [daily(D1, "1000", "1010", 100), daily(D2, "1010", "1000", 200),
               daily(D3, "200", "202", 1000)]  # fmt: skip
        series = PriceAdjuster(AdjustmentLedger([factor(D3, "0.2")])).adjust(X, raw)

        first, second, third = series.adjusted
        assert (first.open.amount, first.close.amount, first.volume) == (200, 202, 500)
        assert (second.open.amount, second.close.amount, second.volume) == (202, 200, 1000)
        assert third == raw[2]  # on the ex-date: already on the new basis
        assert series.cumulative == (Decimal("0.2"), Decimal("0.2"), Decimal(1))

    def test_raw_bars_are_returned_untouched(self) -> None:
        raw = [daily(D1, "1000", "1010"), daily(D3, "200", "202")]

        series = PriceAdjuster(AdjustmentLedger([factor(D3, "0.2")])).adjust(X, raw)

        assert series.raw == tuple(raw)
        assert raw[0].open.amount == Decimal(1000)

    def test_factors_multiply_and_a_bar_before_both_takes_both(self) -> None:
        raw = [daily(D1, "1200", "1200"), daily(D2, "600", "600"), daily(D3, "200", "200")]
        ledger = AdjustmentLedger([factor(D2, "0.5", ActionKind.BONUS), factor(D3, "0.3333")])

        series = PriceAdjuster(ledger).adjust(X, raw)

        assert series.cumulative == (Decimal("0.16665"), Decimal("0.3333"), Decimal(1))

    def test_an_ex_date_on_a_holiday_scales_the_bars_before_it(self) -> None:
        holiday = date(2026, 1, 3)  # between D2 and D3
        raw = [daily(D2, "1000", "1000"), daily(D3, "500", "500")]

        series = PriceAdjuster(AdjustmentLedger([factor(holiday, "0.5")])).adjust(X, raw)

        assert [b.close.amount for b in series.adjusted] == [Decimal(500), Decimal(500)]

    def test_volume_rounds_half_to_even_and_stays_whole(self) -> None:
        raw = [daily(D1, "600", "600", 5), daily(D2, "400", "400", 5)]

        series = PriceAdjuster(AdjustmentLedger([factor(D2, "0.6666")])).adjust(X, raw)

        assert series.adjusted[0].volume == 8  # 5 / 0.6666 = 7.5007

    def test_another_instruments_factor_does_nothing(self) -> None:
        raw = [daily(D1, "100", "100"), daily(D2, "100", "100")]

        series = PriceAdjuster(AdjustmentLedger([factor(D2, "0.5", i="NSE:2")])).adjust(X, raw)

        assert series.adjusted == series.raw

    def test_bars_of_another_instrument_or_out_of_order_are_refused(self) -> None:
        adjuster = PriceAdjuster(AdjustmentLedger())
        with pytest.raises(ValueError, match="one instrument"):
            adjuster.adjust(X, [daily(D1, "1", "1", instrument="NSE:2")])
        with pytest.raises(ValueError, match="oldest first"):
            adjuster.adjust(X, [daily(D2, "1", "1"), daily(D1, "1", "1")])
        with pytest.raises(ValueError, match="one per session"):
            adjuster.adjust(X, [daily(D1, "1", "1"), daily(D1, "1", "1")])

    def test_no_factors_no_change(self) -> None:
        raw = [daily(D1, "100", "101"), daily(D2, "101", "102")]

        assert PriceAdjuster(AdjustmentLedger()).adjust(X, raw).adjusted == tuple(raw)


class TestLedgerFile:
    def test_it_round_trips_and_keeps_ratios_exact(self, tmp_path: Path) -> None:
        ledger = AdjustmentLedger(
            [factor(D3, "0.2"), factor(D2, "0.5", ActionKind.BONUS, i="NSE:2")]
        )
        path = tmp_path / "adjustments.yaml"

        ledger.save(path)
        loaded = AdjustmentLedger.load(path)

        assert loaded.factors_for(X) == ledger.factors_for(X)
        assert loaded.factors_for("NSE:2")[0].ratio == Decimal("0.5")
        assert loaded.content_hash == ledger.content_hash
        assert len(loaded) == 2

    def test_the_hash_changes_when_a_factor_is_added(self) -> None:
        assert (
            AdjustmentLedger([factor(D3, "0.2")]).content_hash
            != AdjustmentLedger([factor(D3, "0.2"), factor(D4, "0.5")]).content_hash
        )

    def test_the_same_factor_twice_is_refused(self) -> None:
        with pytest.raises(ValueError, match="listed twice"):
            AdjustmentLedger([factor(D3, "0.2"), factor(D3, "0.2")])

    def test_two_different_actions_on_one_day_are_allowed(self) -> None:
        ledger = AdjustmentLedger([factor(D3, "0.2"), factor(D3, "0.5", ActionKind.BONUS)])

        assert len(ledger.factors_for(X)) == 2

    @pytest.mark.parametrize(
        "body",
        [
            "factors:\n- {instrument_id: X, ex_date: 2026-01-05, ratio: 0.5, kind: split, "
            "source: s}",
            "factors:\n- {instrument_id: X, ex_date: 2026-01-05, ratio: '0.5', kind: split}",
            "factors:\n- {instrument_id: X, ex_date: 2026-01-05, ratio: '0.5', kind: split, "
            "source: s, why: x}",
            "factor: []",
            "factors:\n- {instrument_id: X, ex_date: 2026-01-05, ratio: half, kind: split, "
            "source: s}",
        ],
    )
    def test_a_malformed_file_is_refused(self, tmp_path: Path, body: str) -> None:
        path = tmp_path / "a.yaml"
        path.write_text(body + "\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="adjustment ledger"):
            AdjustmentLedger.load(path)

    def test_an_empty_file_is_an_empty_ledger(self, tmp_path: Path) -> None:
        path = tmp_path / "a.yaml"
        path.write_text("factors: []\n", encoding="utf-8")

        assert len(AdjustmentLedger.load(path)) == 0

    def test_the_shipped_ledger_loads(self) -> None:
        AdjustmentLedger.load()
