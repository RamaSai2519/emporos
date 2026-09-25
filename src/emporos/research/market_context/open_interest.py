"""F&O open-interest lines for a name, from the stock F&O bhavcopy (PROFIT_PLAN §12.2, EM-239).

The bhavcopy of day `d` is published after the close, so a decision on day `D` may read files dated
strictly before `D` (an event after 18:00 on `D` still cannot: the file's publication time is not
recorded, so the whole day is treated as unknown). The stored dataset (`fo_stock_v1`) thins options
to the monthly expiries within 15% of the underlying, so the put/call ratio is over that band.

Lines (all from the last bhavcopy before the decision day, `oi_asof` its ISO date):
* `fut_open_interest`: open interest of all the stock's futures expiries, in units (summed across
  expiries so a roll from the near to the next month does not read as a collapse);
* `fut_oi_change_pct`: that total over the total on the bhavcopy before it;
* `fut_oi_vs_5d_avg`: that total over the mean of the five bhavcopies before it (needs 3);
* `fut_settle_change_pct`: the nearest unexpired future's settlement over its own on the bhavcopy
  before (price and open interest together say build-up or unwinding);
* `fut_basis_pct`: that future's settlement over the underlying's level (UDiFF files only);
* `put_call_oi_ratio`: put open interest over call open interest in the stored band (needs calls).
A name with no futures on the last bhavcopy, or whose last bhavcopy is over a week old, gets no
lines; a number that cannot be computed is left out."""

from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict, defaultdict
from collections.abc import Sequence
from datetime import date
from statistics import mean

from emporos.options.chain import OptionRight
from emporos.research.fo_archive_rows import IndexContractRow, InstrumentKind
from emporos.research.fo_archive_store import FoDayStore

__all__ = ["FoOpenInterest"]

MAX_AGE_DAYS = 7
MIN_AVERAGE_DAYS = 3
AVERAGE_DAYS = 5


class FoOpenInterest:
    def __init__(
        self, store: FoDayStore, days: Sequence[date] | None = None, cache_days: int = 8
    ) -> None:
        self._store = store
        self._days = sorted(days if days is not None else store.days())
        self._cache_days = cache_days
        self._held: OrderedDict[date, dict[str, list[IndexContractRow]]] = OrderedDict()

    def lines(self, symbol: str, decision_day: date) -> dict[str, float | str]:
        known = self._days[: bisect_left(self._days, decision_day)]
        if not known or (decision_day - known[-1]).days > MAX_AGE_DAYS:
            return {}
        last = known[-1]
        rows = self._rows(last, symbol)
        futures = [r for r in rows if r.kind is InstrumentKind.FUTURE]
        if not futures:
            return {}
        total = sum(r.open_interest for r in futures)
        out: dict[str, float | str] = {
            "oi_asof": last.isoformat(),
            "fut_open_interest": float(total),
        }
        earlier = known[-2] if len(known) > 1 else None
        before = self._future_rows(earlier, symbol) if earlier is not None else []
        if before and (previous := sum(r.open_interest for r in before)):
            out["fut_oi_change_pct"] = (total / previous - 1) * 100
        history = [
            sum(r.open_interest for r in past)
            for day in known[-1 - AVERAGE_DAYS : -1]
            if (past := self._future_rows(day, symbol))
        ]
        if len(history) >= MIN_AVERAGE_DAYS and (average := mean(history)):
            out["fut_oi_vs_5d_avg"] = total / average
        near = min((r for r in futures if r.expiry > last), key=lambda r: r.expiry, default=None)
        if near is not None:
            self._near_lines(out, near, before)
        self._put_call(out, rows)
        return out

    @staticmethod
    def _near_lines(
        out: dict[str, float | str], near: IndexContractRow, before: Sequence[IndexContractRow]
    ) -> None:
        then = next((r for r in before if r.expiry == near.expiry), None)
        if then is not None and then.settle:
            out["fut_settle_change_pct"] = float((near.settle / then.settle - 1) * 100)
        if near.underlying:
            out["fut_basis_pct"] = float((near.settle / near.underlying - 1) * 100)

    @staticmethod
    def _put_call(out: dict[str, float | str], rows: Sequence[IndexContractRow]) -> None:
        calls = sum(r.open_interest for r in rows if r.right is OptionRight.CALL)
        puts = sum(r.open_interest for r in rows if r.right is OptionRight.PUT)
        if calls > 0:
            out["put_call_oi_ratio"] = puts / calls

    def _future_rows(self, day: date, symbol: str) -> list[IndexContractRow]:
        return [r for r in self._rows(day, symbol) if r.kind is InstrumentKind.FUTURE]

    def _rows(self, day: date, symbol: str) -> list[IndexContractRow]:
        """A day's rows of one symbol; each day is read once and the last few are kept."""
        if day not in self._held:
            by_symbol: dict[str, list[IndexContractRow]] = defaultdict(list)
            for row in self._store.read(day):
                by_symbol[row.symbol].append(row)
            self._held[day] = by_symbol
            if len(self._held) > self._cache_days:
                self._held.popitem(last=False)
        else:
            self._held.move_to_end(day)
        return self._held[day].get(symbol, [])
