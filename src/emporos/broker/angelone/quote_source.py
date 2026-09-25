"""The one Angel One call a quote recorder needs: FULL-mode quotes by instrument id (EM-236).

Not a broker. It holds the REST api and nothing else: no order method exists on it, no instrument
master is read (the token is in the id, `"NSE:<token>"`), and no socket is opened."""

from __future__ import annotations

from collections.abc import Sequence

from emporos.broker.angelone.api import AngelOneApi, QuoteMode
from emporos.broker.angelone.mapping import AccountMapper
from emporos.broker.errors import BrokerRejectedError
from emporos.broker.models import Quote

__all__ = ["AngelOneQuoteSource"]

_BATCH = 50  # Angel One's quote call takes at most 50 symbols per exchange


class AngelOneQuoteSource:
    def __init__(self, api: AngelOneApi, account: AccountMapper | None = None) -> None:
        self._api = api
        self._account = account or AccountMapper()

    async def get_quote(self, instrument_ids: Sequence[str]) -> list[Quote]:
        tokens: dict[str, list[str]] = {}
        for instrument_id in instrument_ids:
            exchange, _, token = instrument_id.partition(":")
            if not exchange or not token:
                raise BrokerRejectedError(f"not an instrument id: {instrument_id!r}")
            tokens.setdefault(exchange, []).append(token)
        by_id: dict[str, Quote] = {}
        for exchange, listed in tokens.items():
            for start in range(0, len(listed), _BATCH):
                response = await self._api.quotes(
                    QuoteMode.FULL, {exchange: listed[start : start + _BATCH]}
                )
                for entry in response.fetched:
                    quote = self._account.quote(entry)
                    by_id[quote.instrument_id] = quote
        return [by_id[i] for i in instrument_ids if i in by_id]
