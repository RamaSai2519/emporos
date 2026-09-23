"""One session's comparison: signals matched and classified, round trips paired.

The builder takes both sides in the SAME neutral shape (signal points, outcomes by signal ref,
closed trades), so it cannot tell which side is which by anything but where it was put. That is
what the identity test relies on: give it the backtest twice and every delta must be zero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from emporos.backtest.portfolio import ClosedTrade
from emporos.parity.ledger import SessionParity, SignalParity
from emporos.parity.matcher import SignalMatcher
from emporos.parity.models import SideOutcome, SignalPoint
from emporos.parity.outcomes import StatusClassifier
from emporos.parity.trades import TradePairer


@dataclass(frozen=True)
class SideInputs:
    signals: Sequence[SignalPoint]
    outcomes: Mapping[str, SideOutcome]  # by SignalPoint.ref
    trades: Sequence[ClosedTrade]


@dataclass(frozen=True)
class SessionKey:
    strategy: str
    run_id: str
    session_date: date
    config_hash: str
    starting_cash: Decimal


class SessionParityBuilder:
    def __init__(
        self,
        matcher: SignalMatcher | None = None,
        classifier: StatusClassifier | None = None,
        pairer: TradePairer | None = None,
    ) -> None:
        self._matcher = matcher or SignalMatcher()
        self._classifier = classifier or StatusClassifier()
        self._pairer = pairer or TradePairer()

    def build(self, key: SessionKey, paper: SideInputs, backtest: SideInputs) -> SessionParity:
        rows: list[SignalParity] = []
        for match in self._matcher.match(paper.signals, backtest.signals):
            p_out = paper.outcomes.get(match.paper.ref) if match.paper else None
            b_out = backtest.outcomes.get(match.backtest.ref) if match.backtest else None
            status, reason = self._classifier.classify(p_out, b_out)
            anchor = match.paper or match.backtest
            assert anchor is not None
            rows.append(
                SignalParity(
                    anchor.instrument_id, status, reason, match.paper, match.backtest, p_out, b_out
                )
            )
        return SessionParity(
            key.strategy, key.run_id, key.session_date, key.config_hash, key.starting_cash,
            tuple(rows), self._pairer.pair(paper.trades, backtest.trades),
        )  # fmt: skip
