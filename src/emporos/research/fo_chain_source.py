"""The stored F&O archive as a `ChainSource` for the option backtester (EM-225 to EM-226).

One underlying at a time. Each day's snapshot is built from that day's stored index-option rows:
* lot size: the value in force that day (`ContractSpec`), exact or inferred; a day without one is
  not offered, because a backtest must never guess a position's size.
* underlying close: the level the file itself carries (UDiFF), else the index's own daily close from
  its 5-minute series; a day with neither is not offered.
* on a monthly expiry day the expiring future's settlement price is the exchange's final
  settlement level of the index (`ChainSnapshot.settlements`); the 5-minute close is NOT used to
  settle, being up to about 0.1% off the official close.
* a leg's close counts as a price only if the contract TRADED that day (contracts > 0): the file
  carries a stale close for a contract that did not, and `OptionQuote.tradable` refuses it.
Options only; futures rows are not part of a spread's chain."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal

from emporos.options.chain import ChainSnapshot, ExpiryChain, OptionQuote, OptionRight
from emporos.research.fo_archive_rows import IndexContractRow, InstrumentKind
from emporos.research.fo_archive_store import FoDayStore
from emporos.research.fo_contract_specs import ContractSpec, LotSource

__all__ = ["BhavcopyChainSource"]

DEFAULT_TICK = Decimal("0.05")


class BhavcopyChainSource:
    def __init__(
        self,
        store: FoDayStore,
        specs: Sequence[ContractSpec],
        symbol: str,
        strike_step: Decimal,
        index_closes: Mapping[date, Decimal],
        tick_size: Decimal = DEFAULT_TICK,
    ) -> None:
        if strike_step <= 0 or tick_size <= 0:
            raise ValueError("the strike step and the tick size must be positive")
        self._store = store
        self._symbol = symbol
        self._step = strike_step
        self._tick = tick_size
        self._closes = index_closes
        self._specs = {s.day: s for s in specs if s.symbol == symbol and s.lot_size is not None}
        stored = set(store.days())
        self._days = sorted(
            d for d, s in self._specs.items() if d in stored and self._has_level(d, s)
        )
        self._offered = frozenset(self._days)

    def _has_level(self, day: date, spec: ContractSpec) -> bool:
        # a UDiFF day carries the level in its own rows; an older day needs the index series
        return day in self._closes or spec.lot_source is LotSource.EXCHANGE

    def days(self) -> Sequence[date]:
        return self._days

    def snapshot(self, day: date) -> ChainSnapshot | None:
        spec = self._specs.get(day)
        if spec is None or spec.lot_size is None or day not in self._offered:
            return None
        own = self._store.read(day, self._symbol)
        rows = [r for r in own if r.kind is InstrumentKind.OPTION]
        level = self._level(day, rows)
        if not rows or level is None:
            return None
        return ChainSnapshot(
            day,
            self._symbol,
            level,
            spec.lot_size,
            self._step,
            self._tick,
            self._expiries(rows),
            self._settlements(day, own),
        )

    @staticmethod
    def _settlements(day: date, rows: Sequence[IndexContractRow]) -> dict[date, Decimal]:
        """On an expiry day, the expiring future's settlement price IS the index's final
        settlement level (the exchange settles both on it), so it is the number an expiring index
        option settles against. Only monthly expiries have a future, so only they appear."""
        return {
            r.expiry: r.settle
            for r in rows
            if r.kind is InstrumentKind.FUTURE and r.expiry == day and r.settle > 0
        }

    def _level(self, day: date, rows: Sequence[IndexContractRow]) -> Decimal | None:
        carried = [r.underlying for r in rows if r.underlying is not None and r.underlying > 0]
        if carried:
            return statistics.median(carried)
        return self._closes.get(day)

    @staticmethod
    def _expiries(rows: Sequence[IndexContractRow]) -> dict[date, ExpiryChain]:
        by_expiry: dict[date, dict[tuple[Decimal, OptionRight], OptionQuote]] = defaultdict(dict)
        for row in rows:
            assert row.strike is not None and row.right is not None
            by_expiry[row.expiry][(row.strike, row.right)] = OptionQuote(
                row.strike, row.right, row.close, row.settle, row.open_interest, row.contracts
            )
        return {e: ExpiryChain(e, quotes) for e, quotes in sorted(by_expiry.items())}

    def underlying_agreement(self, tolerance: Decimal) -> list[tuple[date, Decimal, Decimal]]:
        """Days where the archive's own index level and the index series differ by more than
        `tolerance` (a fraction), as (day, archive level, series close): a check of the join, not
        a filter."""
        off: list[tuple[date, Decimal, Decimal]] = []
        for day in self._days:
            close = self._closes.get(day)
            carried = [
                r.underlying
                for r in self._store.read(day, self._symbol)
                if r.underlying is not None
            ]
            if close is None or not carried:
                continue
            level = statistics.median(carried)
            if abs(level - close) / close > tolerance:
                off.append((day, level, close))
        return off
