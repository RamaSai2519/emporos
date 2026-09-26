"""EM-244: Black-76 IV: round trips, parity forwards, no lookahead, ATM/25-delta metrics."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal

import pytest

from emporos.core.clock import IST
from emporos.options.chain import OptionRight
from emporos.research.cause_ledger.events import CalendarEvent
from emporos.research.fo_archive_rows import ArchiveFormat, IndexContractRow, InstrumentKind
from emporos.research.iv.black76 import Right, black76_delta, black76_price, implied_vol
from emporos.research.iv.metrics import SliceMetricsCalculator
from emporos.research.iv.rates import CalendarRepoRate
from emporos.research.iv.slices import ChainSliceBuilder

DAY, EXPIRY = date(2024, 6, 3), date(2024, 6, 27)
FORWARD, RATE = 22_000.0, 0.065
YEARS = (EXPIRY - DAY).days / 365


def smile(strike: float) -> float:
    """A skewed smile: puts (low strikes) richer than calls."""
    return 0.14 + 0.00004 * (FORWARD - strike) + 0.000000006 * (strike - FORWARD) ** 2


def row(kind: InstrumentKind, strike: float | None, right: str | None, settle: float, **kw: object
        ) -> IndexContractRow:  # fmt: skip
    return IndexContractRow(
        day=kw.get("day", DAY),  # type: ignore[arg-type]
        symbol="NIFTY", kind=kind, expiry=kw.get("expiry", EXPIRY),  # type: ignore[arg-type]
        strike=None if strike is None else Decimal(str(strike)),
        right=None if right is None else OptionRight(right), open=Decimal(0), high=Decimal(0),
        low=Decimal(0), close=Decimal(str(settle)), settle=Decimal(str(round(settle, 2))),
        contracts=int(kw.get("contracts", 10)), turnover=Decimal(0), open_interest=1000,
        change_in_oi=0, underlying=Decimal(str(FORWARD)), lot_size=25, source=ArchiveFormat.UDIFF,
    )  # fmt: skip


def chain_rows(with_future: bool = True) -> list[IndexContractRow]:
    rows = [row(InstrumentKind.FUTURE, None, None, FORWARD)] if with_future else []
    for k in range(20_000, 24_050, 50):
        v = smile(float(k))
        for right in (Right.CALL, Right.PUT):
            price = black76_price(FORWARD, float(k), YEARS, RATE, v, right)
            rows.append(row(InstrumentKind.OPTION, float(k), right.value, price))
    return rows


class TestBlack76:
    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    @pytest.mark.parametrize("strike", [20_000.0, 21_500.0, 22_000.0, 22_600.0, 24_000.0])
    @pytest.mark.parametrize("vol", [0.08, 0.15, 0.4])
    def test_the_implied_vol_round_trips_the_price_within_half_a_percent(
        self, right: Right, strike: float, vol: float
    ) -> None:
        price = black76_price(FORWARD, strike, YEARS, RATE, vol, right)
        intrinsic = (
            max(FORWARD - strike, 0.0) if right is Right.CALL else max(strike - FORWARD, 0.0)
        ) * math.exp(-RATE * YEARS)
        if price - intrinsic < 2.0:
            pytest.skip("a deep in-the-money option is all intrinsic: its price carries no vol")

        found = implied_vol(round(price, 2), FORWARD, strike, YEARS, RATE, right)

        assert found is not None and found == pytest.approx(vol, rel=0.005)

    def test_put_call_parity_holds_for_the_model(self) -> None:
        call = black76_price(FORWARD, 21_800.0, YEARS, RATE, 0.17, Right.CALL)
        put = black76_price(FORWARD, 21_800.0, YEARS, RATE, 0.17, Right.PUT)

        assert call - put == pytest.approx(math.exp(-RATE * YEARS) * (FORWARD - 21_800.0))

    def test_a_price_outside_the_no_arbitrage_band_has_no_vol(self) -> None:
        assert implied_vol(0.01, FORWARD, 18_000.0, YEARS, RATE, Right.CALL) is None  # < intrinsic
        assert implied_vol(FORWARD * 2, FORWARD, 22_000.0, YEARS, RATE, Right.CALL) is None
        assert implied_vol(100.0, FORWARD, 22_000.0, 0.0, RATE, Right.CALL) is None

    def test_deltas_have_the_right_signs_and_ranges(self) -> None:
        call = black76_delta(FORWARD, 22_000.0, YEARS, RATE, 0.15, Right.CALL)
        put = black76_delta(FORWARD, 22_000.0, YEARS, RATE, 0.15, Right.PUT)
        assert 0.4 < call < 0.6 and -0.6 < put < -0.4


class TestSlices:
    def test_the_future_is_the_forward_when_the_archive_has_it(self) -> None:
        (chain,) = ChainSliceBuilder().build(DAY, "NIFTY", chain_rows(), RATE)

        assert chain.forward == FORWARD and chain.forward_source == "future" and chain.has_future

    def test_without_a_future_the_parity_forward_recovers_it(self) -> None:
        (chain,) = ChainSliceBuilder().build(DAY, "NIFTY", chain_rows(with_future=False), RATE)

        assert chain.forward_source == "parity"
        assert chain.forward == pytest.approx(FORWARD, rel=0.0005)

    def test_a_row_from_another_day_is_refused_so_no_later_price_can_enter(self) -> None:
        rows = [
            *chain_rows(),
            row(InstrumentKind.FUTURE, None, None, 23_000.0, day=date(2024, 6, 4)),
        ]

        with pytest.raises(ValueError, match="no later prices"):
            ChainSliceBuilder().build(DAY, "NIFTY", rows, RATE)

    def test_an_expired_contract_and_an_untraded_quote_are_left_out(self) -> None:
        rows = [
            *chain_rows(),
            row(InstrumentKind.OPTION, 22_000.0, "CE", 5.0, expiry=DAY),  # expires today
            row(InstrumentKind.OPTION, 22_050.0, "CE", 999.0, contracts=0),  # carried close
        ]

        (chain,) = ChainSliceBuilder().build(DAY, "NIFTY", rows, RATE)

        assert chain.expiry == EXPIRY
        assert all(q.settle != 999.0 for q in chain.quotes)


class TestMetrics:
    def metrics(self):  # type: ignore[no-untyped-def]
        (chain,) = ChainSliceBuilder().build(DAY, "NIFTY", chain_rows(), RATE)
        return chain, SliceMetricsCalculator().compute(chain, RATE)

    def test_atm_iv_is_the_smiles_atm_vol_and_the_straddle_is_the_implied_move(self) -> None:
        chain, m = self.metrics()

        assert m is not None and m.atm_strike == 22_000.0
        assert m.atm_iv == pytest.approx(smile(22_000.0), rel=0.005)
        assert m.straddle is not None and m.implied_move == pytest.approx(m.straddle / FORWARD)
        assert 0.02 < m.implied_move < 0.06  # about 0.8 * sigma * sqrt(T)

    def test_the_25_delta_skew_is_positive_when_puts_are_richer(self) -> None:
        _, m = self.metrics()

        assert m is not None and m.put25_iv is not None and m.call25_iv is not None
        assert m.skew25 is not None and m.skew25 > 0
        assert m.put25_iv == pytest.approx(smile(21_000.0), abs=0.02)  # roughly the 25-delta put

    def test_a_chain_that_does_not_bracket_25_delta_has_no_skew(self) -> None:
        rows = [r for r in chain_rows() if r.strike is None or 21_950 <= float(r.strike) <= 22_050]
        (chain,) = ChainSliceBuilder().build(DAY, "NIFTY", rows, RATE)

        m = SliceMetricsCalculator().compute(chain, RATE)

        assert m is not None and m.skew25 is None and m.atm_iv is not None


class TestRepo:
    def event(self, when: date, value: str) -> CalendarEvent:
        return CalendarEvent(
            "rbi_mpc", "d", when, datetime(when.year, when.month, when.day, 10, tzinfo=IST),
            "https://x", date(2026, 9, 26), value,
        )  # fmt: skip

    def test_the_rate_is_the_latest_decision_on_or_before_the_day(self) -> None:
        repo = CalendarRepoRate(
            [self.event(date(2018, 6, 6), "6.25"), self.event(date(2018, 8, 1), "6.50")]
        )

        assert repo.rate(date(2018, 6, 5)) == 0.0625  # before the first: the first
        assert repo.rate(date(2018, 6, 6)) == 0.0625
        assert repo.rate(date(2018, 7, 31)) == 0.0625
        assert repo.rate(date(2018, 8, 1)) == 0.065

    def test_a_calendar_without_repo_decisions_is_refused(self) -> None:
        with pytest.raises(ValueError, match="repo"):
            CalendarRepoRate([])
