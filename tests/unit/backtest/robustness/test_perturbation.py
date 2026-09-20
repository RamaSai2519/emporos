"""Neighbours come from the declared grid; each runs on the same test window as the choice."""

from decimal import Decimal

from emporos.backtest.robustness.perturbation import (
    NeighbourRun,
    NeighbourSelector,
    PerturbationReport,
    PerturbationRunner,
)
from emporos.backtest.tuning import NET_PNL, ParameterCandidate
from tests.unit.backtest.test_walkforward_run import (
    CANDIDATES,
    RecordingBacktester,
    base_spec,
    independent_score,
    runner,
    windows,
)

D = Decimal


def c(name: str, **overrides: int) -> ParameterCandidate:
    return ParameterCandidate(name, overrides)


class TestNeighbours:
    def test_a_one_at_a_time_grid_gives_only_the_single_parameter_changes(self) -> None:
        chosen = c("mid", a=2, b=5)
        grid = [chosen, c("a_up", a=3, b=5), c("b_up", a=2, b=6), c("both", a=3, b=6)]

        names = [n.name for n in NeighbourSelector().of(chosen, grid)]

        assert names == ["a_up", "b_up"]

    def test_a_grid_that_moves_several_parameters_at_once_uses_all_the_others(self) -> None:
        chosen = c("x", a=1, b=1)
        grid = [chosen, c("y", a=2, b=2), c("z", a=3, b=3)]

        assert [n.name for n in NeighbourSelector().of(chosen, grid)] == ["y", "z"]

    def test_the_chosen_candidate_is_never_its_own_neighbour(self) -> None:
        chosen = c("x", a=1)

        assert NeighbourSelector().of(chosen, [chosen]) == ()

    def test_a_missing_override_counts_as_a_difference(self) -> None:
        chosen = c("x", a=1)

        assert [n.name for n in NeighbourSelector().of(chosen, [chosen, c("y", a=1, b=2)])] == ["y"]


class TestReport:
    def test_the_share_of_neighbours_that_make_money(self) -> None:
        report = PerturbationReport(
            (
                NeighbourRun(0, "a", D(5)),
                NeighbourRun(0, "b", D(-5)),
                NeighbourRun(1, "a", D(0)),
                NeighbourRun(1, "b", D(9)),
            )  # fmt: skip
        )

        assert report.profitable_share == D("0.5")  # breakeven is not a profit

    def test_no_runs_means_no_share(self) -> None:
        assert PerturbationReport(()).profitable_share is None


async def test_each_neighbour_is_backtested_on_the_test_window_of_the_choice() -> None:
    backtester = RecordingBacktester()
    walk = await runner(backtester, objective=NET_PNL).run(base_spec(), CANDIDATES, windows())

    report = await PerturbationRunner(RecordingBacktester()).evaluate(
        base_spec(), walk.outcomes, CANDIDATES
    )

    expected_runs = sum(len(NeighbourSelector().of(o.chosen, CANDIDATES)) for o in walk.outcomes)
    assert len(report.runs) == expected_runs > 0
    by_name = {cand.name: cand for cand in CANDIDATES}
    for run in report.runs:
        window = walk.outcomes[run.window].window.test
        assert run.net_pnl == await independent_score(by_name[run.candidate], window)
