"""F&O expiry days, read from the archive itself (EM-244, driver-atlas-plan §3.2 item 1).

An expiry date is a fact known long before it arrives, so it is available from the start of its own
day (`KnownAhead`). The dates are not computed from a rule (the exchange moved the expiry weekday,
shifted expiries around holidays and dropped weekly contracts): they are the distinct expiries the
daily bhavcopy lists. A stock underlying has monthlies only, so one event per expiry date is enough
(`fno_expiry_stock`). An index has its own events per symbol: `monthly` when the archive lists a
future for that expiry, else `weekly`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from emporos.research.cause_ledger.events import CalendarEvent, KnownAhead
from emporos.research.fo_archive_store import FoDayStore

__all__ = ["INDEX_SYMBOLS", "ExpirySource"]

INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")


class ExpirySource:
    def __init__(
        self,
        store: FoDayStore,
        source_url: str,
        checked_on: date,
        label: str,
        symbols: Sequence[str] | None = None,
    ) -> None:
        self._store, self._url, self._checked = store, source_url, checked_on
        self._label, self._symbols = label, symbols

    def events(self) -> Sequence[CalendarEvent]:
        listed: set[tuple[str, str, date]] = set()
        for day in self._store.days():
            listed |= self._store.contracts(day)
        rule = KnownAhead()
        if self._symbols is None:  # a stock archive: the expiry dates alone
            return [
                CalendarEvent(
                    "fno_expiry_stock",
                    "Stock F&O monthly expiry",
                    expiry,
                    rule.available_at(expiry),
                    self._url,
                    self._checked,
                    note=f"distinct expiry in the {self._label} archive",
                )  # fmt: skip
                for expiry in sorted({e for _, _, e in listed})
            ]
        out: list[CalendarEvent] = []
        futures = {(s, e) for s, kind, e in listed if kind == "FUT"}
        for symbol, _, expiry in sorted(listed, key=lambda t: (t[2], t[0])):
            if symbol not in self._symbols:
                continue
            tag = "monthly" if (symbol, expiry) in futures else "weekly"
            out.append(
                CalendarEvent(
                    f"fno_expiry_{symbol.lower()}_{tag}",
                    f"{symbol} {tag} expiry",
                    expiry,
                    rule.available_at(expiry),
                    self._url,
                    self._checked,
                    note=f"distinct expiry in the {self._label} archive",
                )  # fmt: skip
            )
        return sorted({e.event_id: e for e in out}.values(), key=lambda e: (e.event_date, e.kind))


def default_index_source(root: Path, checked_on: date) -> ExpirySource:
    return ExpirySource(
        FoDayStore(root), "https://nsearchives.nseindia.com/content/fo/", checked_on,
        "index F&O bhavcopy", INDEX_SYMBOLS,
    )  # fmt: skip
