"""A name's business-group index: the equal-weight return of the OTHER members (EM-243).

Membership is the group's as of the previous session, and the name itself is left out, so a name's
own move never explains itself. A day on which no other member printed has no group return."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import numpy as np

from emporos.research.atlas.arrays import Bools, Floats
from emporos.research.atlas.groups import GroupMap
from emporos.research.atlas.returns import Returns

__all__ = ["GroupIndexer"]


class GroupIndexer:
    def __init__(
        self,
        groups: GroupMap,
        sessions: tuple[date, ...],
        symbols: Mapping[str, str],
        returns: Mapping[str, Returns],
    ) -> None:
        self._groups, self._sessions, self._symbols, self._returns = (
            groups,
            sessions,
            symbols,
            returns,
        )

    def for_symbol(self, symbol: str) -> Returns | None:
        group = self._groups.group_of(symbol)
        if group is None:
            return None
        previous = [None, *self._sessions[:-1]]
        others: list[str] = sorted(
            {
                s
                for day in self._sessions
                for s in self._groups.members(group, day)
                if s != symbol and self._symbols.get(s) in self._returns
            }
        )
        if not others:
            return None
        mask = np.array(
            [
                [(d is not None and s in self._groups.members(group, d)) for d in previous]
                for s in others
            ]
        )  # (members, sessions)
        stacks = [self._returns[self._symbols[s]] for s in others]
        daily = self._mean(np.stack([r.daily for r in stacks]), mask)
        gap = self._mean(np.stack([r.gap for r in stacks]), mask)
        bars = self._mean(np.stack([r.bars for r in stacks]), mask)
        level = np.cumprod(1 + np.nan_to_num(daily))
        level[np.isnan(daily) & (np.arange(len(daily)) == 0)] = 1.0
        return Returns(daily, gap, bars, level)

    @staticmethod
    def _mean(values: Floats, mask: Bools) -> Floats:
        shaped = mask if values.ndim == 2 else mask[:, :, None]
        valid = shaped & ~np.isnan(values)
        total = np.where(valid, values, 0.0).sum(axis=0)
        count = valid.sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(count > 0, total / np.maximum(count, 1), np.nan)
