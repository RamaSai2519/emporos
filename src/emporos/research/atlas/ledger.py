"""The Move Ledger: every stock, sector and market move of the span, and a quiet placebo (EM-243).

Nothing here looks at a price after the day it describes except the forward drift the ledger is
FOR; nothing after `last` at all (the caller never loads it). Class rules (plan §2):

* stock move: a session with |residual| >= 2.5 sigma, or a 15-minute block (09:15-09:30, ...) with
  |residual| >= 3 sigma of that block's own trailing scale (at most one jump a session: the largest);
* sector move: a sector index session with |residual against NIFTY| >= 2 sigma;
* market move: a NIFTY session with |return| >= 1.2%, or an open gap >= 0.8%;
* placebo: as many quiet name-sessions (|residual| < 0.5 sigma) as there are stock moves, drawn
  with a seeded generator from the sessions in date order."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

from emporos.core.clock import IST
from emporos.research.atlas.decompose import Decomposer, Decomposition
from emporos.research.atlas.events import FORWARD_SESSIONS, EventClass, MoveEvent, OnsetKind
from emporos.research.atlas.onset import NOTHING, Onset, OnsetAnalyzer
from emporos.research.atlas.panel import FIRST_SLOT_MINUTE, SLOT_MINUTES, InstrumentPanel
from emporos.research.atlas.returns import Returns, returns_of
from emporos.research.cause_ledger.groups import GroupMap

__all__ = ["Fits", "Fitted", "LedgerRules", "MoveLedgerBuilder", "SeriesId"]

PCT = 100.0


@dataclass(frozen=True)
class LedgerRules:
    stock_sigma: float = 2.5
    jump_sigma: float = 3.0
    sector_sigma: float = 2.0
    market_return: float = 0.012
    market_gap: float = 0.008
    quiet_sigma: float = 0.5
    seed: int = 20260926
    first_event: date = date(2017, 11, 1)


@dataclass(frozen=True)
class SeriesId:
    name: str  # trading symbol, or the index's name
    instrument_id: str
    sector: str = ""
    group: str = ""


@dataclass(frozen=True)
class Fitted:
    who: SeriesId
    own: Returns
    regressors: list[Returns]
    deco: Decomposition


@dataclass(frozen=True)
class Fits:
    """Every series decomposed once: NIFTY, each sector index, each stock."""

    market: Fitted
    sectors: list[Fitted]
    stocks: list[Fitted]


class MoveLedgerBuilder:
    def __init__(
        self,
        sessions: tuple[date, ...],
        groups: GroupMap,
        decomposer: Decomposer | None = None,
        analyzer: OnsetAnalyzer | None = None,
        rules: LedgerRules | None = None,
    ) -> None:
        self._sessions = sessions
        self._groups = groups
        self._decomposer = decomposer or Decomposer()
        self._analyzer = analyzer or OnsetAnalyzer()
        self._rules = rules or LedgerRules()

    def fit(
        self,
        panels: Mapping[str, InstrumentPanel],
        market_id: str,
        sector_of: Mapping[str, tuple[str, str]],  # symbol -> (sector index id, its name)
        symbols: Mapping[str, str],  # trading symbol -> instrument id
    ) -> Fits:
        returns = {i: returns_of(p) for i, p in panels.items()}
        market = returns[market_id]
        market_fit = Fitted(
            SeriesId("NIFTY", market_id), market, [], self._decomposer.decompose(market, [])
        )
        sectors = [
            Fitted(
                SeriesId(name, index_id),
                returns[index_id],
                [market],
                self._decomposer.decompose(returns[index_id], [market]),
            )  # fmt: skip
            for index_id, name in sorted(set(sector_of.values()))
            if index_id in returns
        ]
        return Fits(market_fit, sectors, self._stock_fits(returns, market, sector_of, symbols))

    def build(self, fits: Fits) -> list[MoveEvent]:
        events = self._market(fits.market)
        for sector in fits.sectors:
            events += self._scan(sector, EventClass.SECTOR, self._rules.sector_sigma, jumps=False)
        stock: list[MoveEvent] = []
        for fitted in fits.stocks:
            stock += self._scan(fitted, EventClass.STOCK_DAILY, self._rules.stock_sigma, True)
        events += stock
        events += self._placebo(fits.stocks, len(stock))
        return sorted(events, key=lambda e: (e.day, e.event_class.value, e.name))

    # --- the three kinds of series -----------------------------------------------------------
    def _market(self, fitted: Fitted) -> list[MoveEvent]:
        market = fitted.own
        rows = np.flatnonzero(
            (np.abs(market.daily) >= self._rules.market_return)
            | (np.abs(market.gap) >= self._rules.market_gap)
        )
        return [
            self._daily_event(
                fitted, EventClass.MARKET, int(d), float(np.sign(market.daily[d]) or 1)
            )
            for d in rows
            if self._in_span(int(d)) and not np.isnan(market.daily[d])
        ]

    def _stock_fits(
        self,
        returns: Mapping[str, Returns],
        market: Returns,
        sector_of: Mapping[str, tuple[str, str]],
        symbols: Mapping[str, str],
    ) -> list[Fitted]:
        from emporos.research.atlas.group_index import GroupIndexer

        indexer = GroupIndexer(self._groups, self._sessions, symbols, returns)
        fits: list[Fitted] = []
        for symbol, instrument_id in sorted(symbols.items()):
            if instrument_id not in returns:
                continue
            regressors = [market]
            sector = sector_of.get(symbol)
            if sector is not None and sector[0] in returns:
                regressors.append(returns[sector[0]])
            group = indexer.for_symbol(symbol)
            if group is not None:
                regressors.append(group)
            who = SeriesId(
                symbol,
                instrument_id,
                sector[1] if sector else "",
                indexer.label(symbol),
            )
            own = returns[instrument_id]
            fits.append(Fitted(who, own, regressors, self._decomposer.decompose(own, regressors)))
        return fits

    # --- events ------------------------------------------------------------------------------
    def _scan(
        self, fitted: Fitted, event_class: EventClass, threshold: float, jumps: bool
    ) -> list[MoveEvent]:
        deco = fitted.deco
        events: list[MoveEvent] = []
        with np.errstate(invalid="ignore"):
            big = np.flatnonzero(np.abs(deco.resid) >= threshold * deco.sigma)
        for d in big:
            if self._in_span(int(d)):
                events.append(
                    self._daily_event(fitted, event_class, int(d), float(np.sign(deco.resid[d])))
                )
        if jumps:
            events += self._jumps(fitted)
        return events

    def _jumps(self, fitted: Fitted) -> list[MoveEvent]:
        """The largest 15-minute block of a session, if it is >= 3 sigma of that block's own scale
        (the open's noise is measured against the open's, not against midday's)."""
        deco = fitted.deco
        events: list[MoveEvent] = []
        for d in range(len(deco.sigma15)):
            if not self._in_span(d):
                continue
            with np.errstate(invalid="ignore"):
                z = np.abs(deco.blocks[d]) / deco.block_sigma[d]
            if np.isnan(z).all():
                continue
            j = int(np.nanargmax(z))
            if z[j] >= self._rules.jump_sigma:
                events.append(self._jump_event(fitted, d, j))
        return events

    def _placebo(self, fits: list[Fitted], count: int) -> list[MoveEvent]:
        pool = [
            (day, i, d)
            for i, f in enumerate(fits)
            for d in range(len(f.deco.sigma))
            if self._in_span(d)
            and (day := self._sessions[d]) is not None
            and not np.isnan(f.deco.resid[d])
            and abs(f.deco.resid[d]) < self._rules.quiet_sigma * f.deco.sigma[d]
            and not np.isnan(f.deco.path[d]).all()
        ]
        pool.sort(key=lambda t: (t[0], fits[t[1]].who.name))
        drawn = random.Random(self._rules.seed).sample(pool, min(count, len(pool)))
        return [
            self._daily_event(
                fits[i], EventClass.PLACEBO, d, float(np.sign(fits[i].deco.resid[d]) or 1)
            )
            for _, i, d in sorted(drawn)
        ]

    def _in_span(self, d: int) -> bool:
        return self._sessions[d] >= self._rules.first_event

    # --- one event ---------------------------------------------------------------------------
    def _daily_event(
        self, fitted: Fitted, event_class: EventClass, d: int, direction: float
    ) -> MoveEvent:
        deco = fitted.deco
        onset = self._analyzer.daily(deco.path[d], float(deco.gap_resid[d]), int(direction))
        resid = deco.resid[d]
        market_like = event_class is EventClass.MARKET
        return self._event(
            fitted, event_class, d, int(direction), float(resid), float(deco.sigma[d]), onset,
            raw_z=market_like,
        )  # fmt: skip

    def _jump_event(self, fitted: Fitted, d: int, j: int) -> MoveEvent:
        deco = fitted.deco
        direction = int(np.sign(deco.blocks[d, j]) or 1)
        onset = self._analyzer.jump(deco.path[d], float(deco.gap_resid[d]), 3 * j, direction)
        return self._event(
            fitted, EventClass.STOCK_JUMP, d, direction, float(deco.blocks[d, j]),
            float(deco.block_sigma[d, j]), onset, raw_z=False,
        )  # fmt: skip

    def _event(
        self, fitted: Fitted, event_class: EventClass, d: int, direction: int, size: float,
        sigma: float, onset: Onset, raw_z: bool,
    ) -> MoveEvent:  # fmt: skip
        who, deco, day = fitted.who, fitted.deco, self._sessions[d]
        forward_raw, forward_resid = self._forward(fitted, d)
        betas = deco.betas[d]
        return MoveEvent(
            f"MV:{event_class.value}:{who.name}:{day.isoformat()}:{d}", event_class, who.name,
            who.instrument_id, who.sector, who.group, day, direction,
            _pct(fitted.own.daily[d]), _pct(size), _pct(sigma), size / sigma if sigma else np.nan,
            _pct(deco.gap_resid[d]), onset.kind, self._at(day, onset.slot, onset.kind),
            self._peak(day, onset), _pct(onset.after_15m), _pct(onset.after_60m),
            _pct(onset.after_close), forward_raw, forward_resid, *self._named_betas(fitted, betas),
        )  # fmt: skip

    @staticmethod
    def _named_betas(fitted: Fitted, betas: np.ndarray) -> tuple[float, float, float]:
        """(market, sector, group), NaN where the name has no such term."""
        have = [betas[j] if j < len(betas) else np.nan for j in range(3)]
        has_sector = bool(fitted.who.sector)
        has_group = bool(fitted.who.group)
        market = float(have[0])
        sector = float(have[1]) if has_sector else np.nan
        group_index = 1 + int(has_sector)
        group = float(have[group_index]) if has_group and group_index < len(betas) else np.nan
        return market, sector, group

    def _forward(self, fitted: Fitted, d: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
        own, betas = fitted.own.level, fitted.deco.betas[d]
        raw: list[float] = []
        resid: list[float] = []
        for h in FORWARD_SESSIONS:
            if d + h >= len(own) or np.isnan(own[d]) or np.isnan(own[d + h]):
                raw.append(np.nan)
                resid.append(np.nan)
                continue
            move = own[d + h] / own[d] - 1
            explained = sum(
                betas[j] * (r.level[d + h] / r.level[d] - 1)
                for j, r in enumerate(fitted.regressors)
            )
            raw.append(_pct(move))
            resid.append(_pct(move - explained))
        return tuple(raw), tuple(resid)

    @staticmethod
    def _at(day: date, slot: int, kind: OnsetKind) -> datetime | None:
        if kind is OnsetKind.NONE:
            return None
        minute = FIRST_SLOT_MINUTE + SLOT_MINUTES * (slot + 1) if slot >= 0 else FIRST_SLOT_MINUTE
        return datetime.combine(day, datetime.min.time(), tzinfo=IST) + timedelta(minutes=minute)

    def _peak(self, day: date, onset: Onset) -> datetime | None:
        if onset is NOTHING or onset.kind is OnsetKind.NONE:
            return None
        minute = FIRST_SLOT_MINUTE + SLOT_MINUTES * (onset.peak_slot + 1)
        return datetime.combine(day, datetime.min.time(), tzinfo=IST) + timedelta(minutes=minute)


def _pct(value: float) -> float:
    return float(value) * PCT
