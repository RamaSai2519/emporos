"""EM-230: the Track B statistics, the bar, the B1 arms and wiring, the ledger and the runner."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.support.option_chains import SyntheticMarket

from emporos.core.clock import FixedClock
from emporos.domain.research_experiments import ExperimentDeclaration, ExperimentFamily
from emporos.options.backtest import BacktestResult, DayPnl
from emporos.options.fo_costs import FoFeeScheduleLibrary
from emporos.research.option_screen.b1 import (
    B1Arms,
    B1Backtests,
    B1Inputs,
    B1Parameters,
    warmup_end,
)
from emporos.research.option_screen.events import BlackoutFileError, load_blackout_days
from emporos.research.option_screen.ledger import (
    JsonlOptionLedger,
    OptionIdentity,
    OptionRecord,
    write_daily_pnl,
)
from emporos.research.option_screen.report import arm_lines, table
from emporos.research.option_screen.run import ArmResult, B1Screen, daily_returns
from emporos.research.option_screen.screen import ArmOutcome, OptionBar, judge
from emporos.research.option_screen.stats import OptionStats, monthly_t
from emporos.research.swing.bootstrap import BlockBootstrap, RuinReport

D = Decimal
CAPITAL = Decimal(100_000)
GRID = {"k": ("1.0", "1.5"), "filter": ("on", "off"), "ladder_depth": ("2", "3")}


def result_of(
    equities: list[str], start: date = date(2024, 1, 31), open_days: int = 1
) -> BacktestResult:
    days = []
    previous = D(1000)
    for i, e in enumerate(equities):
        # one session per month-end so the monthly series is exactly the equities
        month = start.month + i
        day = date(start.year + (month - 1) // 12, (month - 1) % 12 + 1, 28)
        days.append(DayPnl(day, D(e), D(e) - previous, open_days if i else 0))
        previous = D(e)
    return BacktestResult((), tuple(days), {})


class TestStats:
    def test_monthly_returns_cagr_and_drawdown_from_the_equity_curve(self) -> None:
        result = result_of(["1100", "990", "1089"])  # +10%, -10%, +10%

        stats = OptionStats.of(result, D(1000))

        assert stats.months == 3 and stats.positive_month_share == pytest.approx(2 / 3)
        assert stats.worst_month == pytest.approx(-0.10)
        assert stats.max_drawdown == pytest.approx(0.10)
        assert stats.total_return == pytest.approx(0.089)
        assert stats.net_pnl == pytest.approx(89.0)
        assert stats.days == 3 and stats.days_with_a_spread == 2

    def test_the_amended_months_measures_count_only_months_with_a_spread_open(self) -> None:
        # the first month closes with nothing open (open_days is 0 on the first session)
        stats = OptionStats.of(result_of(["1100", "990", "1089"]), D(1000))

        assert stats.months_with_exposure == 2
        assert stats.positive_month_share_exposed == pytest.approx(0.5)  # one of two exposed
        assert stats.negative_month_share == pytest.approx(1 / 3)  # one of ALL three

    def test_a_run_never_exposed_has_no_exposed_share(self) -> None:
        stats = OptionStats.of(result_of(["1000", "1000"], open_days=0), D(1000))

        assert (stats.months_with_exposure, stats.positive_month_share_exposed) == (0, None)

    def test_a_calendar_year_is_positive_or_not(self) -> None:
        stats = OptionStats.of(result_of(["1100", "1200"]), D(1000))

        assert (stats.years, stats.positive_year_share) == (1, 1.0)

    def test_the_monthly_t_statistic(self) -> None:
        # mean 0.02, sample sd 0.02, three months: t = 0.02 / (0.02 / sqrt(3))
        assert monthly_t([0.0, 0.02, 0.04]) == pytest.approx(3**0.5)
        assert monthly_t([0.01, 0.01]) is None  # no spread to divide by
        assert monthly_t([0.01]) is None

    def test_a_run_without_days_has_no_stats(self) -> None:
        with pytest.raises(ValueError):
            OptionStats.of(BacktestResult((), (), {}), D(1000))


def stats(**kw: float | int | None) -> OptionStats:
    base = dict(
        net_cagr=0.25, total_return=0.5, months=36, positive_month_share=0.7, worst_month=-0.05,
        monthly_t=3.0, years=3, positive_year_share=1.0, max_drawdown=0.10, round_trips=150,
        days=750, days_with_a_spread=600, net_pnl=50_000.0, months_with_exposure=30,
        positive_month_share_exposed=0.8, negative_month_share=0.2,
    )  # fmt: skip
    return OptionStats(**{**base, **kw})  # type: ignore[arg-type]


RUIN = RuinReport(10_000, 20, 252, 1, 0.01, 0.10, 0.2)


def outcome(
    s: OptionStats | None = None, adverse: OptionStats | None = None, ruin: RuinReport = RUIN
) -> ArmOutcome:
    return ArmOutcome(
        BacktestResult((), (), {}),
        s or stats(),
        adverse or stats(net_cagr=0.1),
        ruin,
        date(2017, 10, 1),
        D(100_000),
    )


class TestBar:
    def test_the_thresholds_are_the_plans(self) -> None:
        bar = OptionBar()

        assert (bar.min_net_cagr, bar.cash_rate, bar.min_positive_month_share) == (
            0.18,
            0.065,
            0.60,
        )
        assert bar.max_negative_month_share == 0.40  # the amended months bar (2026-09-25)
        assert (bar.min_worst_month, bar.max_drawdown, bar.min_monthly_t) == (-0.10, 0.25, 2.5)
        assert (bar.min_round_trips, bar.min_positive_year_share) == (100, 0.60)
        assert (bar.min_neighbour_share, bar.aggressive_max_p_drawdown) == (0.50, 0.05)

    def test_an_arm_that_clears_every_bar_passes(self) -> None:
        assert judge(outcome(), 0.75, aggressive=False).passed

    @pytest.mark.parametrize(
        ("change", "fragment"),
        [
            (dict(net_cagr=0.10), "net CAGR >= 18%"),
            (dict(positive_month_share_exposed=0.5), "months with exposure net positive"),
            (dict(positive_month_share_exposed=None), "months with exposure net positive"),
            (dict(negative_month_share=0.41), "all months net negative"),
            (dict(worst_month=-0.11), "worst month"),
            (dict(max_drawdown=0.26), "max drawdown"),
            (dict(monthly_t=2.0), "monthly t"),
            (dict(monthly_t=None), "monthly t"),
            (dict(round_trips=99), "round trips"),
            (dict(positive_year_share=0.5), "calendar years"),
        ],
    )
    def test_each_bar_is_checked_on_its_own(
        self, change: dict[str, float | None], fragment: str
    ) -> None:
        verdict = judge(outcome(stats(**change)), 0.75, aggressive=False)

        assert not verdict.passed and any(fragment in f for f in verdict.failed_checks)

    def test_the_adverse_scenario_must_still_be_positive(self) -> None:
        verdict = judge(outcome(adverse=stats(net_cagr=-0.01)), 0.75, aggressive=False)

        assert "net CAGR > 0 at adverse costs" in verdict.failed_checks

    def test_beating_cash_is_its_own_named_check(self) -> None:
        low = judge(outcome(stats(net_cagr=0.05)), 0.75, aggressive=False)

        assert any("beats cash" in f for f in low.failed_checks)

    def test_the_neighbour_check_fails_until_it_is_computed_and_when_too_few_agree(self) -> None:
        assert not judge(outcome(), None, aggressive=False).passed
        assert not judge(outcome(), 0.49, aggressive=False).passed
        assert judge(outcome(), 0.5, aggressive=False).passed

    def test_only_an_aggressive_arm_needs_the_ruin_bar(self) -> None:
        deep = RuinReport(10_000, 20, 252, 1, 0.06, 0.1, 0.2)

        assert judge(outcome(ruin=deep), 0.75, aggressive=False).passed
        assert not judge(outcome(ruin=deep), 0.75, aggressive=True).passed
        assert judge(outcome(ruin=replace(deep, p_drawdown_30=0.05)), 0.75, aggressive=True).passed


class TestArms:
    def test_the_declared_grid_gives_eight_arms(self) -> None:
        arms = B1Arms.declared(GRID)

        assert len(arms) == 8 and len({a.label for a in arms}) == 8
        assert arms[0].as_point() == {"k": "1.0", "filter": "on", "ladder_depth": "2"}

    def test_a_grid_naming_other_axes_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly"):
            B1Arms.declared({"k": ("1.0",), "filter": ("on",)})

    def test_every_arm_has_three_neighbours_differing_in_one_axis(self) -> None:
        arms = B1Arms.declared(GRID)
        adjacent = B1Arms.adjacent(arms)

        assert {len(v) for v in adjacent.values()} == {3}
        first = arms[0]
        assert set(adjacent[first.label]) == {
            "k=1.5 filter=on depth=2", "k=1.0 filter=off depth=2", "k=1.0 filter=on depth=3"
        }  # fmt: skip

    def test_only_depth_three_is_aggressive(self) -> None:
        assert (
            B1Parameters(D("1.0"), True, 3).aggressive
            and not B1Parameters(D("1.0"), True, 2).aggressive
        )

    @pytest.mark.parametrize(
        "point",
        [
            {"k": "0", "filter": "on", "ladder_depth": "2"},
            {"k": "1", "filter": "maybe", "ladder_depth": "2"},
            {"k": "1", "filter": "on", "ladder_depth": "4"},
        ],
    )
    def test_nonsense_arms_are_refused(self, point: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            B1Parameters.from_point(point)


MARKET = SyntheticMarket()
BLACKOUT = frozenset({date(2024, 12, 20)})


def inputs() -> B1Inputs:
    return B1Inputs(
        MARKET.source(), MARKET.nifty, MARKET.vix, BLACKOUT, MARKET.futures_calendar(),
        FoFeeScheduleLibrary.from_directory().earliest, D(100_000),
    )  # fmt: skip


class TestWarmupAndRun:
    def test_the_first_session_is_the_one_every_rule_has_its_history(self) -> None:
        days = MARKET.days

        assert warmup_end(MARKET.nifty, MARKET.vix) == days[252]  # the 252 earlier VIX closes bind
        with pytest.raises(ValueError, match="not enough"):
            warmup_end({days[0]: D(1)}, {days[0]: D(1)})

    def test_every_arm_runs_and_respects_its_rules(self) -> None:
        screen = B1Screen(inputs(), MARKET.days[-1], BlockBootstrap(paths=50))
        arms = B1Arms.declared(GRID)

        results = screen.run(arms)

        assert len(results) == 8 and screen.first_day == MARKET.days[252]
        weeks = {}
        for d in MARKET.days:
            weeks.setdefault(d.isocalendar()[:2], d)
        first_sessions = set(weeks.values())
        for r in results:
            run = r.outcome.run
            assert run.days[0].day >= screen.first_day
            depth_cap = r.arm.ladder_depth
            assert max(d.open_positions for d in run.days) <= depth_cap
            for t in run.trades:
                assert t.entry_day in first_sessions
                assert 21 <= (t.plan.expiry - t.entry_day).days <= 49
                vertical = t.plan.verticals[0]
                assert vertical.width * (t.units // t.lots) <= 5000
                assert t.lots == 1

    def test_the_run_is_deterministic(self) -> None:
        arms = B1Arms.declared(GRID)[:2]

        def net() -> list[float]:
            screen = B1Screen(inputs(), MARKET.days[-1], BlockBootstrap(paths=20))
            return [r.outcome.stats.net_pnl for r in screen.run(arms)]

        assert net() == net()

    def test_the_regime_filter_can_only_remove_entry_days(self) -> None:
        arms = [B1Parameters(D("1.0"), True, 2), B1Parameters(D("1.0"), False, 2)]
        screen = B1Screen(inputs(), MARKET.days[-1], BlockBootstrap(paths=20))
        on, off = screen.run(arms)

        assert on.outcome.stats.round_trips <= off.outcome.stats.round_trips

    def test_adverse_costs_never_beat_benchmark_costs(self) -> None:
        screen = B1Screen(inputs(), MARKET.days[-1], BlockBootstrap(paths=20))
        for r in screen.run(B1Arms.declared(GRID)[:4]):
            assert r.outcome.adverse_stats.net_pnl <= r.outcome.stats.net_pnl

    def test_an_empty_source_is_refused(self) -> None:
        from emporos.options.chain_source import InMemoryChainSource

        empty = replace(inputs(), chains=InMemoryChainSource([]))
        with pytest.raises(ValueError, match="no days"):
            B1Screen(empty, MARKET.days[-1])

    def test_daily_returns_chain_from_the_capital(self) -> None:
        run = result_of(["1100", "990"])

        assert daily_returns(run, D(1000)) == pytest.approx([0.10, -0.10])

    def test_the_backtests_wire_the_declared_depths(self) -> None:
        backtests = B1Backtests(inputs())
        from emporos.options.slippage import BENCHMARK

        assert backtests.build(B1Parameters(D("1.0"), True, 2), BENCHMARK) is not None
        assert backtests.build(B1Parameters(D("1.5"), False, 3), BENCHMARK) is not None


class TestEvents:
    def test_the_committed_blackout_file_loads_and_has_the_named_days(self) -> None:
        days = load_blackout_days(Path("config/events/b1-blackout-days.yaml"))

        assert {date(2019, 5, 23), date(2024, 6, 4), date(2024, 7, 22), date(2024, 7, 23)} <= days
        assert date(2025, 2, 1) in days and date(2017, 2, 1) in days

    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ('events:\n  - {date: "2024-01-01", kind: rbi_mpc, source: "x"}\n', "unknown kind"),
            ('events:\n  - {date: "2024-01-01", kind: union_budget, source: ""}\n', "no source"),
            ('events:\n  - {date: "nope", kind: union_budget, source: "x"}\n', "bad row"),
            ('events:\n  - {date: "2024-01-01", kind: union_budget}\n', "bad row"),
            ("events: []\n", "no events"),
            ("other: 1\n", "KeyError"),
            (
                'events:\n  - {date: "2024-01-01", kind: union_budget, source: "x"}\n'
                '  - {date: "2024-01-01", kind: union_budget, source: "y"}\n',
                "twice",
            ),
        ],
    )
    def test_a_malformed_file_is_refused(self, tmp_path: Path, body: str, match: str) -> None:
        path = tmp_path / "e.yaml"
        path.write_text(body)

        with pytest.raises(BlackoutFileError, match=match):
            load_blackout_days(path)

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(BlackoutFileError):
            load_blackout_days(tmp_path / "absent.yaml")


class TestLedgerAndReport:
    def one(self) -> tuple[ArmResult, OptionIdentity]:
        arm = B1Parameters(D("1.0"), True, 2)
        result = ArmResult(arm, outcome(), 0.5, judge(outcome(), 0.5, aggressive=False))
        identity = OptionIdentity(
            "b1", arm.as_point(), "NIFTY", date(2017, 10, 1), date(2024, 12, 31), D(100_000)
        )
        return result, identity

    def test_an_arm_is_appended_once_and_counted_by_its_screen_id(self, tmp_path: Path) -> None:
        result, identity = self.one()
        ledger = JsonlOptionLedger(tmp_path / "screens.jsonl")
        record = OptionRecord(identity, result, datetime(2026, 9, 25, tzinfo=UTC), "pnl.csv")

        assert ledger.record(record) is True
        assert ledger.record(record) is False  # the same look is not counted twice
        (line,) = (tmp_path / "screens.jsonl").read_text().splitlines()
        document = json.loads(line)
        assert document["screen_id"] == identity.screen_id and identity.screen_id.startswith("OPT-")
        assert document["track"] == "options" and document["passed"] is True
        assert document["round_trips"] == 150 and document["neighbour_share"] == 0.5

    def test_a_new_ledger_reads_the_ids_already_there(self, tmp_path: Path) -> None:
        result, identity = self.one()
        path = tmp_path / "screens.jsonl"
        record = OptionRecord(identity, result, datetime(2026, 9, 25, tzinfo=UTC), "p")
        JsonlOptionLedger(path).record(record)

        assert JsonlOptionLedger(path).record(record) is False

    def test_a_line_that_is_not_a_screen_record_is_refused(self, tmp_path: Path) -> None:
        from emporos.core.errors import ConfigurationError

        path = tmp_path / "screens.jsonl"
        path.write_text('{"nothing": 1}\n')
        result, identity = self.one()

        with pytest.raises(ConfigurationError, match="not a screen record"):
            JsonlOptionLedger(path).record(
                OptionRecord(identity, result, datetime(2026, 9, 25, tzinfo=UTC), "p")
            )

    def test_identity_depends_on_what_was_looked_at(self) -> None:
        _, identity = self.one()

        assert identity.screen_id == replace(identity).screen_id
        assert identity.screen_id != replace(identity, capital=D(200_000)).screen_id
        assert identity.screen_id != replace(identity, parameters={"k": "1.5"}).screen_id

    def test_the_daily_pnl_file_lists_every_day(self, tmp_path: Path) -> None:
        arm = B1Parameters(D("1.0"), True, 2)
        run = result_of(["1100", "990"])
        result = ArmResult(
            arm,
            ArmOutcome(run, stats(), stats(), RUIN, date(2024, 1, 1), D(1000)),
            0.5,
            judge(outcome(), 0.5, aggressive=False),
        )

        path = write_daily_pnl(tmp_path, "OPT-x", result)

        assert Path(path).read_text().splitlines() == [
            "date,pnl",
            "2024-01-28,100",
            "2024-02-28,-110",
        ]

    def test_the_table_and_details_carry_every_requested_number(self) -> None:
        result, _ = self.one()
        lines = table([result])

        assert lines[0].startswith("arm") and "k=1.0 filter=on depth=2" in lines[1]
        assert "150" in lines[1] and "25.0" in lines[1]
        detail = "\n".join(arm_lines(result))
        assert "skipped: none" in detail and "P(drawdown >= 30%) 0.010" in detail
        assert "failed: none" in detail


class FakeLoader:
    def __init__(self, loaded: B1Inputs) -> None:
        self._loaded = loaded
        self.capitals: list[Decimal] = []

    def load(self, capital: Decimal) -> B1Inputs:
        self.capitals.append(capital)
        return replace(self._loaded, capital=capital)


class FakeDeclarations:
    def __init__(self, capital: Decimal | None = CAPITAL) -> None:
        self._capital = capital

    def load(self, slug: str) -> ExperimentDeclaration:
        return ExperimentDeclaration(
            family=ExperimentFamily.STRATEGY, slug=slug, hypothesis="h", economic_rationale="r",
            falsification="f", parameter_grid=dict(GRID), feature_versions={},
            declared_at=datetime(2026, 9, 25, tzinfo=UTC), position_value=self._capital,
        )  # fmt: skip


class TestRunner:
    def runner(self, tmp_path: Path, capital: Decimal | None = CAPITAL):  # type: ignore[no-untyped-def]
        from emporos.cli.option_screen_commands import OptionScreenRun

        loader = FakeLoader(inputs())
        clock = FixedClock(datetime(2026, 9, 25, tzinfo=UTC))
        ledger = JsonlOptionLedger(tmp_path / "screens.jsonl")
        return OptionScreenRun(
            FakeDeclarations(capital), loader, ledger, tmp_path / "pnl", clock
        ), loader

    def test_it_runs_every_arm_records_them_and_prints_the_table(self, tmp_path: Path) -> None:
        run, loader = self.runner(tmp_path)

        lines = run.run("b1-nifty-put-spread-ladder")

        assert loader.capitals == [D(100_000)]
        assert "b1-nifty-put-spread-ladder" in lines[0] and "capital Rs 100,000" in lines[0]
        assert sum("SCREEN_REJECT" in line or " PASS " in line for line in lines) == 8
        assert len((tmp_path / "screens.jsonl").read_text().splitlines()) == 8
        assert len(list((tmp_path / "pnl").glob("OPT-*.csv"))) == 8

    def test_running_it_again_is_the_same_look(self, tmp_path: Path) -> None:
        run, _ = self.runner(tmp_path)
        run.run("b1-nifty-put-spread-ladder")
        run.run("b1-nifty-put-spread-ladder")

        assert len((tmp_path / "screens.jsonl").read_text().splitlines()) == 8

    def test_an_unknown_cell_or_a_declaration_without_capital_is_refused(
        self, tmp_path: Path
    ) -> None:
        run, _ = self.runner(tmp_path)
        with pytest.raises(ValueError, match="no Track B recipe"):
            run.run("l99-something")
        bare, _ = self.runner(tmp_path, capital=None)
        with pytest.raises(ValueError, match="position_value"):
            bare.run("b1-nifty-put-spread-ladder")
