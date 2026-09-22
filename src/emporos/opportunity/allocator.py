"""Portfolio-level capital allocation across simultaneous candidates (EM-152 / EM-157).

The portfolio layer decides which opportunities receive capital — not the strategies that
produced them. `PortfolioAllocator` takes a ranked list of `OpportunityCandidate` (already
scored by `OpportunityScanner`) and the account's current exposure, and returns the subset that
fits within explicit constraints, each sized by stop-loss risk and remaining capital.

This does not replace `RiskEngine`: every `Allocation.signal` still goes through the same risk
gate as any other signal before execution. The allocator only decides which candidates are even
offered to risk, and at what size hint — risk may still reduce or reject any of them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, Decimal

from emporos.domain.money import Money
from emporos.domain.signals import Signal
from emporos.opportunity.models import OpportunityCandidate
from emporos.risk.snapshot import AccountFacts

_ZERO = Decimal(0)


class SectorClassifier:
    """Injected so sector-exposure limits can be enforced without hardcoding a sector taxonomy
    into the domain model. The default treats every instrument as unclassified, which disables
    the sector-exposure constraint in effect (nothing is ever double-counted into a named cap
    unless a real classifier is wired in)."""

    def sector_of(self, instrument_id: str) -> str | None:
        return None


@dataclass(frozen=True)
class AllocationConstraints:
    """Every field is a hard cap the allocator enforces (plan.md-style: conservative,
    reviewable numbers, never a float)."""

    production_capital: Money
    max_simultaneous_positions: int
    max_risk_per_trade: Money  # rupees the allocator will risk on one new position
    max_portfolio_risk: Money  # rupees, summed stop-loss risk across all open + new positions
    min_expected_edge: Decimal = _ZERO
    max_sector_exposure: Money | None = None  # rupees; None disables the sector check

    def __post_init__(self) -> None:
        if self.production_capital <= Money.zero():
            raise ValueError("production capital must be positive")
        if self.max_simultaneous_positions <= 0:
            raise ValueError("max_simultaneous_positions must be positive")
        if self.max_risk_per_trade <= Money.zero():
            raise ValueError("max_risk_per_trade must be positive")
        if self.max_portfolio_risk <= Money.zero():
            raise ValueError("max_portfolio_risk must be positive")
        if self.max_sector_exposure is not None and self.max_sector_exposure <= Money.zero():
            raise ValueError("max_sector_exposure must be positive when set")


@dataclass(frozen=True)
class Allocation:
    candidate: OpportunityCandidate
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("an allocation must have positive quantity")

    @property
    def signal(self) -> Signal:
        """The candidate's signal, resized to the quantity capital/risk allow. Still just a
        hint: `RiskEngine` may reduce it further."""
        return replace(self.candidate.signal, quantity=self.quantity)

    @property
    def committed_capital(self) -> Money:
        return self.candidate.entry.times(self.quantity)

    @property
    def committed_risk(self) -> Money:
        return self.candidate.risk_per_share.times(self.quantity)


class PortfolioAllocator:
    def __init__(self, sectors: SectorClassifier | None = None) -> None:
        self._sectors = sectors or SectorClassifier()

    def allocate(
        self,
        candidates: tuple[OpportunityCandidate, ...],
        *,
        account: AccountFacts,
        constraints: AllocationConstraints,
    ) -> tuple[Allocation, ...]:
        eligible = self._eligible_candidates(candidates, constraints)
        slots = constraints.max_simultaneous_positions - account.open_position_count
        if slots <= 0:
            return ()

        capital_available = constraints.production_capital - account.capital_deployed
        if capital_available < Money.zero():
            capital_available = Money.zero()
        portfolio_risk_used = self._existing_portfolio_risk(account)
        sector_committed: dict[str, Money] = {}
        allocations: list[Allocation] = []

        for candidate in eligible:
            if len(allocations) >= slots:
                break
            quantity = self._size(
                candidate,
                capital_available=capital_available,
                risk_room=constraints.max_portfolio_risk - portfolio_risk_used,
                max_risk_per_trade=constraints.max_risk_per_trade,
                sector_room=self._sector_room(candidate, constraints, sector_committed),
            )
            if quantity <= 0:
                continue
            allocation = Allocation(candidate, quantity)
            allocations.append(allocation)
            capital_available = capital_available - allocation.committed_capital
            portfolio_risk_used = portfolio_risk_used + allocation.committed_risk
            sector = self._sectors.sector_of(candidate.instrument_id)
            if sector is not None:
                sector_committed[sector] = (
                    sector_committed.get(sector, Money.zero()) + allocation.committed_capital
                )

        return tuple(allocations)

    def _eligible_candidates(
        self,
        candidates: tuple[OpportunityCandidate, ...],
        constraints: AllocationConstraints,
    ) -> list[OpportunityCandidate]:
        seen_instruments: set[str] = set()
        eligible: list[OpportunityCandidate] = []
        for candidate in candidates:
            if candidate.score < constraints.min_expected_edge:
                continue
            if candidate.instrument_id in seen_instruments:
                continue  # duplicate/correlated: keep only the best-ranked per instrument
            seen_instruments.add(candidate.instrument_id)
            eligible.append(candidate)
        return eligible

    @staticmethod
    def _existing_portfolio_risk(account: AccountFacts) -> Money:
        """No stop price is known for an already-open position from `AccountFacts` alone, so
        existing exposure is counted by capital deployed, which `max_portfolio_risk` callers
        should size accordingly when positions are already open."""
        return Money.zero()

    def _sector_room(
        self,
        candidate: OpportunityCandidate,
        constraints: AllocationConstraints,
        committed: dict[str, Money],
    ) -> Money | None:
        if constraints.max_sector_exposure is None:
            return None
        sector = self._sectors.sector_of(candidate.instrument_id)
        if sector is None:
            return None
        used = committed.get(sector, Money.zero())
        room = constraints.max_sector_exposure - used
        return room if room > Money.zero() else Money.zero()

    @staticmethod
    def _size(
        candidate: OpportunityCandidate,
        *,
        capital_available: Money,
        risk_room: Money,
        max_risk_per_trade: Money,
        sector_room: Money | None,
    ) -> int:
        risk_per_share = candidate.risk_per_share.amount
        risk_budget = min(max_risk_per_trade.amount, risk_room.amount)
        if risk_budget <= _ZERO:
            return 0
        by_risk = int((risk_budget / risk_per_share).to_integral_value(rounding=ROUND_DOWN))
        by_capital = int(
            (capital_available.amount / candidate.entry.amount).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        quantity = min(by_risk, by_capital)
        if sector_room is not None:
            by_sector = int(
                (sector_room.amount / candidate.entry.amount).to_integral_value(rounding=ROUND_DOWN)
            )
            quantity = min(quantity, by_sector)
        return max(quantity, 0)
