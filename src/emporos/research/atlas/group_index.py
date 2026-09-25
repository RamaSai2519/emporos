"""A name's business-group index: the equal-weight return of the OTHER members (EM-243).

Membership is the group's as of the previous session, and the name itself is left out, so a name's
own move never explains itself. A day on which no other member printed has no group return."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import numpy as np

from emporos.research.atlas.arrays import Bools, Floats
from emporos.research.atlas.returns import Returns
from emporos.research.cause_ledger.groups import GroupMap

__all__ = ["GroupIndexer"]


class GroupIndexer:
    def __init__(
        self,
        groups: GroupMap,
        sessions: tuple[date, ...],
        symbols: Mapping[str, str],
        returns: Mapping[str, Returns],
    ) -> None:
        self._groups, self._sessions = groups, sessions
        self._symbols, self._returns = symbols, returns

    def label(self, symbol: str) -> str:
        """The group(s) the name is in on the last session, joined; "" when in none."""
        return "+".join(self._groups.groups_of(symbol, self._sessions[-1]))

    def for_symbol(self, symbol: str) -> Returns | None:
        """The equal-weight return of the name's peers (the other members of every group it is in),
        each session's peers as of the day before, the name itself never among them."""
        peers = [self._groups.peers_for_session(symbol, day) for day in self._sessions]
        others = sorted({p for row in peers for p in row if self._symbols.get(p) in self._returns})
        if not others:
            return None
        member = [set(row) for row in peers]
        mask = np.array([[s in day_peers for day_peers in member] for s in others])  # (peers, S)
        stacks = [self._returns[self._symbols[s]] for s in others]
        daily = self._mean(np.stack([r.daily for r in stacks]), mask)
        gap = self._mean(np.stack([r.gap for r in stacks]), mask)
        bars = self._mean(np.stack([r.bars for r in stacks]), mask)
        level = np.cumprod(1 + np.nan_to_num(daily))
        none = np.full(bars.shape, np.nan)
        return Returns(daily, gap, bars, level, none, np.full(len(daily), np.nan))

    @staticmethod
    def _mean(values: Floats, mask: Bools) -> Floats:
        shaped = mask if values.ndim == 2 else mask[:, :, None]
        valid = shaped & ~np.isnan(values)
        total = np.where(valid, values, 0.0).sum(axis=0)
        count = valid.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(count > 0, total / np.maximum(count, 1), np.nan)
