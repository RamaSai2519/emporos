"""EM-243: the Move Ledger and the Crossing Ledger on hand-built markets."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest
from tests.unit.research.atlas.synthetic import (
    SESSIONS,
    market_and_stock,
    panel_from,
    sessions,
    stock_panel,
)

from emporos.core.clock import IST
from emporos.research.atlas.crossings import CrossingRules, CrossingScanner
from emporos.research.atlas.decompose import Decomposer
from emporos.research.atlas.events import EventClass, MoveEvent, OnsetKind
from emporos.research.atlas.ledger import LedgerRules, MoveLedgerBuilder
from emporos.research.atlas.panel import InstrumentPanel
from emporos.research.atlas.returns import returns_of
from emporos.research.cause_ledger.groups import GroupMember, YamlGroupMap

DAYS = sessions()
M, S = "NSE:99926000", "NSE:1"
NO_GROUPS = YamlGroupMap([])


def build(
    stock: InstrumentPanel, market: InstrumentPanel, groups: YamlGroupMap = NO_GROUPS,
    rules: LedgerRules | None = None, extra: dict[str, InstrumentPanel] | None = None,
    symbols: dict[str, str] | None = None, sector: dict[str, tuple[str, str]] | None = None,
) -> list[MoveEvent]:  # fmt: skip
    builder = MoveLedgerBuilder(DAYS, groups, rules=rules or LedgerRules(first_event=DAYS[130]))
    panels = {M: market, S: stock, **(extra or {})}
    return builder.build(builder.fit(panels, M, sector or {}, symbols or {"AAA": S}))


def world(seed: int = 1):  # type: ignore[no-untyped-def]
    market, gaps, idio, idio_gaps = market_and_stock(seed)
    return market, gaps, idio, idio_gaps


class TestDecomposition:
    def test_the_beta_is_recovered_from_the_sessions_before_each_day(self) -> None:
        market, gaps, idio, idio_gaps = world()
        m = returns_of(panel_from(market, gaps))
        s = returns_of(stock_panel(market, gaps, idio, idio_gaps, beta=1.5))

        deco = Decomposer().decompose(s, [m])

        assert np.isnan(deco.betas[10, 0])  # not enough history yet
        assert abs(deco.betas[250, 0] - 1.5) < 0.1
        assert np.nanstd(deco.resid[130:]) < 0.75 * np.nanstd(s.daily[130:])

    def test_a_day_in_the_betas_window_is_never_the_day_it_explains(self) -> None:
        market, gaps, idio, idio_gaps = world()
        idio[200, :] += 0.004  # a huge move ON day 200
        m = returns_of(panel_from(market, gaps))
        s = returns_of(stock_panel(market, gaps, idio, idio_gaps))

        deco = Decomposer().decompose(s, [m])

        assert deco.resid[200] > 5 * deco.sigma[200]  # not absorbed by a beta that saw it


class TestMoveEvents:
    def test_an_intraday_move_is_an_event_with_its_onset_after_a_quarter_of_it(self) -> None:
        market, gaps, idio, idio_gaps = world()
        idio[200, 20:30] += 0.006  # +6% over ten bars from bar 20
        events = build(stock_panel(market, gaps, idio, idio_gaps), panel_from(market, gaps))

        [event] = [
            e for e in events if e.event_class is EventClass.STOCK_DAILY and e.day == DAYS[200]
        ]

        assert event.direction == 1 and event.z > 2.5 and event.resid_pct > 5
        assert event.onset_kind is OnsetKind.INTRADAY
        onset = event.onset_at
        assert onset is not None
        assert 20 <= (onset.hour * 60 + onset.minute - 555) // 5 - 1 <= 23  # a quarter in
        assert (
            event.peak_at is not None
            and event.peak_at.hour * 60 + event.peak_at.minute >= 9 * 60 + 15 + 5 * 29
        )

    def test_a_gap_that_is_most_of_the_move_is_an_overnight_onset(self) -> None:
        market, gaps, idio, idio_gaps = world()
        idio_gaps[210] += -0.05
        events = build(stock_panel(market, gaps, idio, idio_gaps), panel_from(market, gaps))

        [event] = [
            e for e in events if e.event_class is EventClass.STOCK_DAILY and e.day == DAYS[210]
        ]

        assert event.direction == -1 and event.onset_kind is OnsetKind.OVERNIGHT
        assert event.onset_at == datetime(
            DAYS[210].year, DAYS[210].month, DAYS[210].day, 9, 15, tzinfo=IST
        )

    def test_a_fifteen_minute_jump_is_recorded_even_on_a_quiet_day(self) -> None:
        market, gaps, idio, idio_gaps = world()
        idio[220, 39:42] += 0.004
        events = build(stock_panel(market, gaps, idio, idio_gaps), panel_from(market, gaps))

        jumps = [e for e in events if e.event_class is EventClass.STOCK_JUMP and e.day == DAYS[220]]

        assert len(jumps) == 1 and jumps[0].direction == 1 and jumps[0].onset_kind is OnsetKind.JUMP

    def test_the_forward_drift_is_measured_from_the_events_close(self) -> None:
        market, gaps, idio, idio_gaps = world()
        idio[200, 20:30] += 0.006
        idio_gaps[201:204] += 0.01  # the move goes on for three sessions
        stock = stock_panel(market, gaps, idio, idio_gaps)
        [event] = [
            e for e in build(stock, panel_from(market, gaps))
            if e.event_class is EventClass.STOCK_DAILY and e.day == DAYS[200]
        ]  # fmt: skip

        raw = stock.day_close[203] / stock.day_close[200] - 1
        assert event.forward_raw_pct[1] == pytest.approx(raw * 100, rel=1e-6)
        assert event.forward_resid_pct[1] > 2  # about +3% of the residual in three sessions

    def test_the_span_starts_where_it_is_told_to(self) -> None:
        market, gaps, idio, idio_gaps = world()
        idio[150, 10:20] += 0.01
        stock = stock_panel(market, gaps, idio, idio_gaps)

        early = build(stock, panel_from(market, gaps), rules=LedgerRules(first_event=DAYS[160]))

        assert all(e.day >= DAYS[160] for e in early)

    def test_a_market_move_and_a_sector_move(self) -> None:
        market, gaps, idio, idio_gaps = world()
        market[230, :] -= 0.0004  # about -3% over the day
        sector_idio = np.random.default_rng(5).normal(0, 0.0002, market.shape)
        sector_idio[240, 10:40] += 0.002
        sector = panel_from(market + sector_idio, gaps)
        events = build(
            stock_panel(market, gaps, idio, idio_gaps), panel_from(market, gaps),
            extra={"NSE:99926008": sector}, sector={"AAA": ("NSE:99926008", "NIFTY IT")},
        )  # fmt: skip

        assert any(
            e.event_class is EventClass.MARKET and e.day == DAYS[230] and e.direction == -1
            for e in events
        )
        [sec] = [e for e in events if e.event_class is EventClass.SECTOR and e.day == DAYS[240]]
        assert sec.name == "NIFTY IT" and sec.direction == 1

    def test_the_placebo_is_as_big_as_the_stock_events_quiet_and_seeded(self) -> None:
        market, gaps, idio, idio_gaps = world()
        idio[200, 20:30] += 0.006
        stock = stock_panel(market, gaps, idio, idio_gaps)
        first = build(stock, panel_from(market, gaps))
        again = build(stock, panel_from(market, gaps))

        stock_events = [
            e for e in first if e.event_class in (EventClass.STOCK_DAILY, EventClass.STOCK_JUMP)
        ]
        placebo = [e for e in first if e.event_class is EventClass.PLACEBO]
        assert len(placebo) == len(stock_events) > 0
        assert all(abs(e.z) < 0.5 for e in placebo)
        assert [e.event_id for e in first] == [e.event_id for e in again]

    def test_a_group_peer_moving_explains_the_name_and_does_not_make_it_an_event(self) -> None:
        market, gaps, idio, idio_gaps = world()
        shared = np.random.default_rng(9).normal(0, 0.0004, market.shape)
        shared[220, 10:40] += 0.003
        other = np.random.default_rng(11).normal(0, 0.0004, market.shape)
        a = stock_panel(market, gaps, idio * 0.3 + shared, idio_gaps)
        b = stock_panel(market, gaps, other * 0.3 + shared, idio_gaps)
        groups = YamlGroupMap(
            [
                GroupMember("g", "AAA", None, None, "http://x", date(2026, 1, 1)),
                GroupMember("g", "BBB", None, None, "http://x", date(2026, 1, 1)),
            ]
        )
        with_groups = build(
            a, panel_from(market, gaps), groups, extra={"NSE:2": b},
            symbols={"AAA": S, "BBB": "NSE:2"},
        )  # fmt: skip
        without = build(
            a, panel_from(market, gaps), extra={"NSE:2": b}, symbols={"AAA": S, "BBB": "NSE:2"}
        )

        def resid(events: list[MoveEvent]) -> float:
            [e] = [
                e for e in events
                if (e.name, e.day, e.event_class) == ("AAA", DAYS[220], EventClass.STOCK_DAILY)
            ]  # fmt: skip
            return abs(e.resid_pct)

        assert resid(with_groups) < 0.25 * resid(without)  # the peer's move took most of it
        assert next(e.group for e in with_groups if e.name == "AAA" and e.group) == "g"


class TestCrossings:
    def fitted(self, idio_edit):  # type: ignore[no-untyped-def]
        market, gaps, idio, idio_gaps = world()
        idio_edit(idio, idio_gaps)
        m = returns_of(panel_from(market, gaps))
        own_panel = stock_panel(market, gaps, idio, idio_gaps)
        own = returns_of(own_panel)
        return own, Decomposer().decompose(own, [m]), own_panel

    def test_the_first_bar_over_the_level_per_direction_and_k_is_the_crossing(self) -> None:
        own, deco, panel = self.fitted(
            lambda idio, gaps: idio.__setitem__((200, slice(30, 40)), idio[200, 30:40] + 0.003)
        )
        scanner = CrossingScanner(DAYS, CrossingRules(first_day=DAYS[150]))

        block = scanner.intraday(("AAA", S, "", ""), own, deco, "stock")
        rows = [i for i, d in enumerate(block.columns["day"]) if d == DAYS[200]]
        ups = [i for i in rows if block.columns["direction"][i] == 1]

        assert {block.columns["k"][i] for i in ups} == {1.5, 2.0}
        low = min(ups, key=lambda i: block.columns["k"][i])
        crossed_slot = (int(block.columns["crossed_minute"][low]) - 555) // 5 - 1  # type: ignore[call-overload]
        first = int(np.flatnonzero(deco.path[200] >= 1.5 * deco.sigma15[200])[0])
        assert crossed_slot == first

    def test_forward_moves_start_at_the_bar_after_the_crossing_and_are_in_its_direction(
        self,
    ) -> None:
        own, deco, panel = self.fitted(
            lambda idio, gaps: idio.__setitem__((200, slice(30, 33)), idio[200, 30:33] + 0.004)
        )
        block = CrossingScanner(DAYS, CrossingRules(first_day=DAYS[150])).intraday(
            ("AAA", S, "", ""), own, deco, "stock"
        )
        i = next(
            j for j, d in enumerate(block.columns["day"])
            if (d, block.columns["direction"][j], block.columns["k"][j]) == (DAYS[200], 1, 2.0)
        )  # fmt: skip
        crossed = (int(block.columns["crossed_minute"][i]) - 555) // 5 - 1  # type: ignore[call-overload]
        r = crossed + 1

        assert block.columns["entry_price"][i] == pytest.approx(panel.close[200, r])
        expected = (panel.close[200, r + 3] / panel.close[200, r] - 1) * 100
        assert block.columns["raw_15m_pct"][i] == pytest.approx(expected)
        expected_1d = (panel.day_close[201] / panel.close[200, r] - 1) * 100
        assert block.columns["raw_1d_pct"][i] == pytest.approx(expected_1d)

    def test_a_down_crossing_reports_continuation_as_positive(self) -> None:
        own, deco, panel = self.fitted(
            lambda idio, gaps: idio.__setitem__((200, slice(30, 40)), idio[200, 30:40] - 0.003)
        )
        block = CrossingScanner(DAYS, CrossingRules(first_day=DAYS[150])).intraday(
            ("AAA", S, "", ""), own, deco, "stock"
        )
        i = next(
            j
            for j, d in enumerate(block.columns["day"])
            if d == DAYS[200] and block.columns["direction"][j] == -1
        )

        crossed = (int(block.columns["crossed_minute"][i]) - 555) // 5 - 1  # type: ignore[call-overload]
        r = crossed + 1
        raw = (panel.close[200, min(r + 3, 74)] / panel.close[200, r] - 1) * -100
        assert block.columns["raw_15m_pct"][i] == pytest.approx(raw)

    def test_a_close_at_two_sigma_is_a_swing_crossing_entered_at_the_next_open(self) -> None:
        own, deco, panel = self.fitted(
            lambda idio, gaps: idio.__setitem__((200, slice(0, 75)), idio[200] + 0.0009)
        )
        block = CrossingScanner(DAYS, CrossingRules(first_day=DAYS[150])).swing(
            ("AAA", S, "", ""), own, deco
        )

        [i] = [j for j, d in enumerate(block.columns["day"]) if d == DAYS[200]]
        assert block.columns["kind"][i] == "swing_stock" and block.columns["direction"][i] == 1
        assert block.columns["entry_price"][i] == pytest.approx(panel.open0[201])
        expected = (panel.day_close[203] / panel.open0[201] - 1) * 100
        assert block.columns["raw_3d_pct"][i] == pytest.approx(expected)
        assert np.isnan(block.columns["raw_15m_pct"][i])  # a swing has no intraday path

    def test_nothing_before_the_first_day_and_nothing_past_the_end_of_the_data(self) -> None:
        own, deco, _ = self.fitted(
            lambda idio, gaps: idio.__setitem__(
                (SESSIONS - 2, slice(30, 40)), idio[SESSIONS - 2, 30:40] + 0.004
            )
        )
        block = CrossingScanner(DAYS, CrossingRules(first_day=DAYS[SESSIONS - 3])).intraday(
            ("AAA", S, "", ""), own, deco, "stock"
        )

        assert all(d >= DAYS[SESSIONS - 3] for d in block.columns["day"])  # type: ignore[operator]
        late = [i for i, d in enumerate(block.columns["day"]) if d == DAYS[SESSIONS - 2]]
        assert late and all(np.isnan(block.columns["raw_5d_pct"][i]) for i in late)  # type: ignore[arg-type]
