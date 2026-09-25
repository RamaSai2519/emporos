"""Buying a call or put on the name (EM-240, PROFIT_PLAN §12.3, declaration amendment B).

Swing horizon only, from end-of-day chains (options have no intraday prices here):

* the entry session is the one the cash entry rule would pick (`entry_time`); the contract is
  priced at that day's close, so the position is entered and known from that close on;
* expiry: the nearest with at least 15 calendar days left;
* strike: from the at-the-money strike (nearest the underlying's close) outward in the trade's
  direction, at most 2 strikes out of the money, and the FIRST contract that traded and whose lot
  premium fits the premium budget (Rs 5,000 x the posture scale) is taken;
* the risk engine reviews it like any entry (positions, day and total loss, posture);
* exit at the close of the first later session on which the UNDERLYING reaches the judge's stop or
  target on its daily bar, else after `hold_days` sessions, never later than the session before
  expiry. The exit premium is the contract's mark that day.

A proposal that cannot be placed comes back as its reason (`no_chain`, `no_expiry`,
`no_traded_contract`, `premium_over_budget`, or the risk rule that refused it), and the engine
counts it: the declaration counts refusals by reason."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol

from emporos.core.clock import IST
from emporos.eventtrader.events import MarketEvent
from emporos.eventtrader.pipeline import TradePlan
from emporos.eventtrader.replay.book import Book
from emporos.eventtrader.replay.costs import TradeCosts
from emporos.eventtrader.replay.fills import ExitReason, entry_time, price_level, touch
from emporos.eventtrader.replay.market import MarketData
from emporos.eventtrader.replay.records import Scenario, TradeLeg, TradeRecord
from emporos.eventtrader.risk.engine import RiskEngine
from emporos.eventtrader.risk.models import EntryProposal, Product
from emporos.eventtrader.stages.models import Instrument, Side
from emporos.options.chain import ChainSnapshot, OptionQuote, OptionRight

__all__ = ["ChainPlacer", "NoChains", "OptionChains"]

MIN_DAYS_TO_EXPIRY = 15
MAX_STRIKES_OUT = 2
SESSION_CLOSE = time(15, 30)


class OptionChains(Protocol):
    def snapshot(self, symbol: str, day: date) -> ChainSnapshot | None:
        """The name's option chain at that day's close, or None when there is none."""
        ...


class NoChains:
    """The stand-in for names with no option data: every proposal is refused as `no_chain`."""

    def snapshot(self, symbol: str, day: date) -> ChainSnapshot | None:
        return None


def _close(day: date) -> datetime:
    return datetime.combine(day, SESSION_CLOSE, tzinfo=IST)


class ChainPlacer:
    def __init__(
        self, chains: OptionChains, market: MarketData, risk: RiskEngine, costs: TradeCosts
    ) -> None:
        self._chains, self._market, self._risk, self._costs = chains, market, risk, costs

    def place(
        self,
        event: MarketEvent,
        plan: TradePlan,
        decision_at: datetime,
        book: Book,
        proposal_scale: Decimal,
    ) -> TradeRecord | str:
        if proposal_scale <= 0:
            return "posture_hold"
        placed_at = entry_time(decision_at, self._market)
        if placed_at is None:
            return "no_later_session"
        day = placed_at.astimezone(IST).date()
        chain = self._chains.snapshot(event.symbol, day)
        if chain is None:
            return "no_chain"
        right = OptionRight.CALL if plan.instrument is Instrument.CALL else OptionRight.PUT
        picked = self._pick(chain, right, proposal_scale)
        if isinstance(picked, str):
            return picked
        expiry, quote = picked
        lot = chain.lot_for(expiry)
        entered_at = _close(day)
        proposal = EntryProposal(
            f"{event.instrument_id}:{expiry.isoformat()}:{quote.strike}{right.value}",
            Product.OPTION, Side.LONG, quote.close, None, entered_at, lot,
        )  # fmt: skip
        verdict = self._risk.review(proposal, book.snapshot(entered_at, proposal_scale))
        if not verdict.approved or verdict.sized is None:
            return verdict.refusals[0].rule
        units = verdict.sized.quantity
        exit_day, exit_price, reason = self._exit(
            event, plan, chain, expiry, quote, right, day, plan.hold_days
        )
        leg = TradeLeg(
            proposal.name, plan.instrument, Product.OPTION, Side.LONG, units, entered_at,
            quote.close, _close(exit_day), exit_price,
        )  # fmt: skip
        return TradeRecord(
            event.event_id, proposal.name, event.symbol, plan.instrument, Product.OPTION,
            Side.LONG, units, entered_at, quote.close, _close(exit_day), exit_price, reason, None,
            verdict.sized.risk, (exit_price - quote.close) * units,
            self._costs.cost(leg, Scenario.BENCHMARK), self._costs.cost(leg, Scenario.ADVERSE),
        )  # fmt: skip

    # --- choosing the contract -----------------------------------------------------------------
    def _pick(
        self, chain: ChainSnapshot, right: OptionRight, scale: Decimal
    ) -> tuple[date, OptionQuote] | str:
        expiries = [e for e in chain.expiry_dates if chain.days_to(e) >= MIN_DAYS_TO_EXPIRY]
        if not expiries:
            return "no_expiry"
        expiry = expiries[0]
        strikes = chain.expiries[expiry].strikes
        if not strikes:
            return "no_traded_contract"
        atm = min(range(len(strikes)), key=lambda i: abs(strikes[i] - chain.underlying_close))
        step = 1 if right is OptionRight.CALL else -1  # out of the money: calls up, puts down
        budget = self._risk.limits.option_premium_per_trade * min(scale, Decimal(1))
        traded = 0
        for i in range(MAX_STRIKES_OUT + 1):
            index = atm + step * i
            if not 0 <= index < len(strikes):
                break
            quote = chain.expiries[expiry].quote(strikes[index], right)
            if quote is None or not quote.tradable:
                continue
            traded += 1
            if quote.close * chain.lot_for(expiry) <= budget:
                return expiry, quote
        return "premium_over_budget" if traded else "no_traded_contract"

    # --- leaving -------------------------------------------------------------------------------
    def _exit(
        self,
        event: MarketEvent,
        plan: TradePlan,
        chain: ChainSnapshot,
        expiry: date,
        quote: OptionQuote,
        right: OptionRight,
        entry_day: date,
        hold_days: int,
    ) -> tuple[date, Decimal, ExitReason]:
        """(session, premium, why). The underlying's view: a call is a long, a put a short."""
        view = Side.LONG if right is OptionRight.CALL else Side.SHORT
        # The stop and target are levels on the bars series (adjusted for splits and bonuses), not
        # on the chain's unadjusted underlying: the entry day's last bar close is the reference.
        ref = self._market.last_close(event.instrument_id, _close(entry_day))
        ref = chain.underlying_close if ref is None else ref
        stop = price_level(ref, view, plan.stop_pct, against=True)
        target = price_level(ref, view, plan.target_pct, against=False)
        bars = [
            b
            for b in self._market.daily_bars(event.instrument_id, entry_day, max(hold_days, 1))
            if b.day < expiry
        ]
        if not bars:
            return entry_day, quote.close, ExitReason.END_OF_DATA
        for bar in bars:
            hit = touch(bar.open, bar.high, bar.low, view, stop, target)
            if hit is not None:
                return bar.day, self._mark(event, expiry, quote, bar.day, entry_day), hit[1]
        last = bars[-1].day
        reason = ExitReason.TIME if len(bars) >= hold_days else ExitReason.END_OF_DATA
        return last, self._mark(event, expiry, quote, last, entry_day), reason

    def _mark(
        self, event: MarketEvent, expiry: date, quote: OptionQuote, day: date, entry_day: date
    ) -> Decimal:
        """The contract's mark on `day`, or on the latest earlier session that has one."""
        cursor: date | None = day
        while cursor is not None and cursor > entry_day:
            snapshot = self._chains.snapshot(event.symbol, cursor)
            chain = snapshot.expiries.get(expiry) if snapshot else None
            found = chain.quote(quote.strike, quote.right) if chain else None
            if found is not None:
                return found.mark
            cursor = self._previous_session(cursor)
        return quote.close

    def _previous_session(self, day: date) -> date | None:
        cursor = day
        for _ in range(10):
            cursor = date.fromordinal(cursor.toordinal() - 1)
            if self._market.is_session(cursor):
                return cursor
        return None
